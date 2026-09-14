"""The specialists layer: three models in parallel, one deterministic judgment.

A Strands `Graph` runs the explainer (Sonnet), the filing agent (Sonnet) and the risk
agent (Haiku) as three entry points, so they execute in one concurrent batch, and joins
them into a `judgment` node whose three incoming edges all carry the same AND condition:
the Python graph fans out on OR semantics, so waiting for all three is expressed as one
condition that is only true once all three results exist.

Every node is a `MultiAgentBase` wrapper rather than a bare `Agent`, for three reasons the
graph itself cannot give: the wrapper passes `structured_output_model` on its own
invocation, it converts any exception into a completed node carrying a deterministic
fallback (the graph is fail-fast, and a failed node would strand `judgment` forever), and
it records which model answered and how long it took.

What a model may decide is bounded on purpose:
  - the explainer may not state an amount the engine did not compute; every dollar figure
    in its message is checked against the engine's own numbers, and a message that fails
    twice is replaced by a deterministic template;
  - the filing agent may only write the narrative labels `forms.free_text_fields` allows;
    every other field is overwritten from the deterministic draft after the call;
  - the risk agent runs after deterministic pre-rules and may only add reasons, never
    remove one, and never decides immigration eligibility.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel
from strands import Agent
from strands.hooks import AfterModelCallEvent, HookProvider, HookRegistry
from strands.multiagent import GraphBuilder
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status

from . import cards, forms, hooks
from .ledger import Ledger, now_utc
from .models import fallback, router
from .schemas import (
    CaseRecord,
    EngineResult,
    Explanation,
    FilingPlan,
    FormField,
    RiskReport,
)
from .store import Store

log = logging.getLogger(__name__)

LEGAL_STATUSES = ("CITIZEN", "LEGAL_PERMANENT_RESIDENT")
LIHEAP_KEY = "liheap"

# Arabic-Indic and extended Arabic-Indic digits, plus the Arabic thousands (U+066C) and
# decimal (U+066B) marks: without the marks "٥٬٠٠٠ دولار" parses as 0, and 0 is an engine
# amount (an ineligible program reports 0), so a fabricated 5,000 would pass the check.
_ARABIC_INDIC = {
    **str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"),
    **str.maketrans("٬٫", ",."),
}
_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:dollars?|دولار)")

# A family reads every numeral, marked as money or not, so the amount check scans them all.
# These four patterns are the numerals that are not amounts: a URL, a statutory citation, a
# phone number and an OKDHS form id are struck out before the scan.
_URL = re.compile(r"https?://\S+")
_STATUTE = re.compile(r"\b\d+\s*(?:CFR|USC|U\.S\.C\.|C\.F\.R\.|OAC)\s*[\d.§()a-z:\-]*", re.I)
_PHONE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
_FORMID = re.compile(r"\b0\d[A-Z]{2}\d{3}[A-Z]?\b")
_NUM = re.compile(r"[\d][\d,]*(?:\.\d+)?")

# Markdown and emoji: the family reads this on a phone as raw characters, so the validator
# strips them rather than trusting the prompt.
_MD_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.M)
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^\s{0,3}[-*+]\s+", re.M)
_MD_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_MD_EMPH = re.compile(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1", re.S)
_MD_STRAY = re.compile(r"[*_`]{1,3}")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2B00-\u2BFF\uFE0F\u200d]"
)


class FilingPlans(BaseModel):
    """The filing agent answers with a list; structured output needs a model to hold it."""
    plans: list[FilingPlan]


# --------------------------------------------------------------------------------------
# shared payload
# --------------------------------------------------------------------------------------


def _program_rows(engine: EngineResult) -> list[dict]:
    rows = []
    for p in engine.programs:
        rows.append(
            {
                "key": p.key,
                "label": p.label,
                "eligible": p.eligible,
                "amount": p.amount,
                "unit": p.unit,
                "eligible_members": p.eligible_members,
                "rule_url": p.rules[0] if p.rules else "",
                "note": p.note,
            }
        )
    return rows


def _rule_urls(engine: EngineResult) -> set[str]:
    out: set[str] = set()
    for p in engine.programs:
        out.update(u for u in p.rules if u)
    return out


def _engine_amounts(engine: EngineResult) -> list[float]:
    """Every number the family may see: the engine's amounts, plus any figure the engine
    itself prints in a program note (the $500 other-dependent credit, for one)."""
    out = [float(p.amount) for p in engine.programs if p.amount is not None]
    for p in engine.programs:
        out.extend(_amounts_in(f"{p.label} {p.note}"))
    return out


def _fmt_amount(amount: Optional[float]) -> str:
    if amount is None:
        return ""
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def _period(unit: Optional[str], language: str) -> str:
    if unit == "USD/month":
        return "شهريًا" if language == "ar" else "per month"
    if unit == "USD/year":
        return "سنويًا" if language == "ar" else "per year"
    return ""


def _filing_programs(engine: EngineResult) -> list[str]:
    """Programs the family is eligible for that Benefitline holds a form file for."""
    eligible = {p.key for p in engine.programs if p.eligible}
    return [k for k in forms.PROGRAMS if k in eligible]


def _liheap_url(engine: EngineResult) -> str:
    from .engine import NOT_MODELED  # local: engine imports policyengine, keep it lazy

    return NOT_MODELED.get(LIHEAP_KEY, {}).get("agency_url", "")


def build_payload(case: CaseRecord, store: Optional[Store] = None) -> dict:
    """Everything the three specialists see. Built once, deterministic, no model involved."""
    engine = case.engine_result
    if engine is None:
        raise ValueError(f"case {case.case_id} has no engine_result; run the engine first")
    drafts = {
        program: forms.plan_filing(program, case.facts, engine, case=case)
        for program in _filing_programs(engine)
    }
    org_rules = []
    if store is not None:
        try:
            org_rules = store.rules()
        except Exception as exc:                                   # a store outage is not fatal
            log.warning("org rules unavailable (%s: %s)", type(exc).__name__, exc)
    return {
        "case_id": case.case_id,
        "language": case.facts.language,
        "facts": case.facts.model_dump(mode="json", exclude_none=True),
        "programs": _program_rows(engine),
        "rule_urls": sorted(_rule_urls(engine)),
        "liheap_url": _liheap_url(engine),
        "org_rules": org_rules,
        "drafts": {k: v.model_dump(mode="json") for k, v in drafts.items()},
        "free_text_fields": {k: forms.free_text_fields(k) for k in drafts},
        "message_ids": [t.get("id") for t in case.transcript if t.get("id")],
        "transcript_tail": case.transcript[-4:],
        "_drafts": drafts,
    }


# --------------------------------------------------------------------------------------
# explainer
# --------------------------------------------------------------------------------------

EXPLAINER_PROMPT = """You write the message a family reads about benefits they may get.

Hard rules:
- Write in the language named by `language` (BCP-47: "en" English, "ar" Arabic). Every word.
- The only numbers you may write are the amounts in `programs`. Copy them exactly, each with
  a leading "$" and its period ("per month" / "per year"). Never add, round differently,
  total, or estimate a number of your own.
- Say plainly that these are estimates and the agency decides.
- `per_program` has one entry per eligible program: {"key", "one_line", "rule_url"}. The
  rule_url must be copied character for character from that program's rule_url in `programs`.
- For WIC, repeat the engine's note that a clinic must find nutritional risk.
- For LIHEAP say the amount is not computed here and give the agency link in `liheap_url`.
- No advice about immigration status, and no promise that a program will be approved.
- Plain text only. The family reads this as a text message on a phone: no markdown, which
  means no "#" headings, no "---" rules, no "*" or "**" around words, no bullet characters,
  no backticks, and no emoji. Write short paragraphs separated by a blank line, and put each
  program's rule URL on its own line.

Answer with the Explanation structure only."""


def _ascii_digits(text: str) -> str:
    return (text or "").translate(_ARABIC_INDIC)


def plain_text(text: str) -> str:
    """Markdown markers and emoji out. The family reads this as a text message, where a
    heading marker, a `---` rule or a `**bold**` pair arrives as literal punctuation."""
    out = _MD_RULE.sub("", text or "")
    out = _MD_HEAD.sub("", out)
    out = _MD_BULLET.sub("", out)
    out = _MD_QUOTE.sub("", out)
    out = _MD_EMPH.sub(r"\2", out)
    out = _MD_STRAY.sub("", out)
    out = _EMOJI.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return "\n".join(line.rstrip() for line in out.splitlines()).strip()


def _numbers_in(text: str) -> list[float]:
    """Every numeral a family would read, once URLs, statutes, phone numbers and form ids go."""
    t = _ascii_digits(text or "")
    for pat in (_URL, _STATUTE, _PHONE, _FORMID):
        t = pat.sub(" ", t)
    out: list[float] = []
    for m in _NUM.finditer(t):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _allowed_numbers(engine: EngineResult) -> list[float]:
    """Engine amounts, plus the numerals that are not claims about money: years, small
    counts and ages, the size of an eligible-member list, and anything the engine itself
    printed in a program label or note."""
    ok = {round(float(a), 2) for a in _engine_amounts(engine)}
    ok |= {float(y) for y in range(2020, 2101)}
    ok |= {float(n) for n in range(0, 21)}
    for member in engine.members:
        age = member.get("age")
        if isinstance(age, (int, float)):
            ok.add(float(age))
    for p in engine.programs:
        if p.eligible_members:
            ok.add(float(len(p.eligible_members)))
        ok.update(_numbers_in(f"{p.label} {p.note}"))
    return sorted(ok)


def _amounts_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _MONEY.finditer(_ascii_digits(text)):
        raw = m.group(1) or m.group(2) or ""
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def bad_amounts(text: str, engine: EngineResult) -> list[float]:
    """Figures in `text` that are not one of the engine's own numbers.

    Every numeral counts, not only the ones written with a `$` or the word dollars: a
    fabricated "9,999 a month for food" reads as an amount to the family whether or not the
    model marked it as money.
    """
    allowed = _allowed_numbers(engine)
    bad = []
    for value in _numbers_in(text):
        if not any(abs(value - a) <= 0.5 for a in allowed):
            bad.append(value)
    return bad


def template_explanation(case: CaseRecord) -> Explanation:
    """The same message without a model: used when the explainer fails or drifts."""
    engine = case.engine_result
    language = case.facts.language or "en"
    ar = language == "ar"
    lines: list[str] = []
    per_program: list[dict] = []
    for p in engine.programs:
        if not p.eligible and p.key != LIHEAP_KEY:
            continue
        rule_url = p.rules[0] if p.rules else ""
        if p.key == LIHEAP_KEY or p.amount is None and p.eligible is None:
            url = _liheap_url(engine) or rule_url
            one = (
                f"{p.label}: لم يُحتسب هنا. قدّم الطلب عبر {url}"
                if ar
                else f"{p.label}: not computed here. Apply through {url}"
            )
            lines.append(one)
            per_program.append({"key": p.key, "one_line": one, "rule_url": rule_url or url})
            continue
        money = _fmt_amount(p.amount)
        period = _period(p.unit, language)
        if money:
            one = (
                f"{p.label}: حوالي {money} {period}."
                if ar
                else f"{p.label}: about {money} {period}."
            )
        else:
            one = (
                f"{p.label}: مؤهل، بدون مبلغ نقدي."
                if ar
                else f"{p.label}: eligible, no cash amount."
            )
        if p.note:
            one = f"{one} {p.note}"
        lines.append(one)
        per_program.append({"key": p.key, "one_line": one, "rule_url": rule_url})

    head = (
        "هذه تقديرات من محرك القواعد لأسرتك. إنها تقديرات، والجهة الحكومية هي التي تقرر."
        if ar
        else "These are estimates from the rules engine for your household. "
        "They are estimates, and the agency decides."
    )
    return Explanation(
        language=language,
        message_to_family="\n".join([head] + lines),
        per_program=per_program,
    )


def clean_explanation(raw: Explanation, case: CaseRecord) -> Explanation:
    """Force the language; plain text, no identifier, no invented amount, engine rule_urls only.

    Every narrative string the model wrote passes the same three code-owned checks: markdown
    and emoji stripped, identifiers redacted, and any line carrying a figure the engine did
    not compute replaced by the deterministic line for that same program.
    """
    engine = case.engine_result
    template = {e["key"]: e for e in template_explanation(case).per_program}
    allowed = _rule_urls(engine)
    liheap_url = _liheap_url(engine)
    if liheap_url:                      # LIHEAP has no statute row; its line carries the agency link
        allowed.add(liheap_url)
    kept = []
    for entry in raw.per_program or []:
        if not isinstance(entry, dict):
            log.warning("explainer per_program entry is not an object: %r", entry)
            continue
        url = str(entry.get("rule_url", ""))
        if not url and liheap_url and str(entry.get("key", "")) == LIHEAP_KEY:
            url = liheap_url          # LIHEAP carries no statute row, so the model often has none
        if url not in allowed:
            log.warning("dropped per_program %r: rule_url not from the engine (%r)",
                        entry.get("key"), url)
            continue
        key = str(entry.get("key", ""))
        one_line = hooks.redact(plain_text(str(entry.get("one_line", ""))))
        if bad_amounts(one_line, engine):
            fallback_line = template.get(key)
            if fallback_line is None:
                log.warning("dropped per_program %r: amounts not from the engine and no "
                            "deterministic line to put in its place", key)
                continue
            log.warning("replaced per_program %r with the deterministic line: %s",
                        key, bad_amounts(one_line, engine))
            one_line = fallback_line["one_line"]
        kept.append({"key": key, "one_line": one_line, "rule_url": url})
    return Explanation(
        language=case.facts.language or "en",
        message_to_family=hooks.redact(plain_text(raw.message_to_family)),
        per_program=kept,
    )


# --------------------------------------------------------------------------------------
# filing
# --------------------------------------------------------------------------------------

FILING_PROMPT = """You complete filing plans that are already filled in for you.

You may write ONLY the narrative labels listed in `free_text_fields` for that program.
Every other field, the form name, the form URL, the documents, the deadline and the submit
route are fixed by the agency form and are overwritten after you answer, so do not change
them. Return every draft plan, in the same order, with the same `program` values.

For each narrative field you write, quote the message ids it came from at the end of the
value like this: [from: m3, m4]. Use only ids that appear in `message_ids`. If nothing in
the transcript supports a narrative field, leave it out rather than inventing a situation.

Never state a benefit amount that is not in `programs`. Never write a Social Security
number, an A-number, or any identifier."""

_CITE = re.compile(r"\[from:\s*([^\]]*)\]\s*$")


def _merge_free_text(draft: FilingPlan, written: FilingPlan, message_ids: set[str]) -> FilingPlan:
    """Take the draft whole; add only the narrative fields the model was allowed to write."""
    allowed = set(forms.free_text_fields(draft.program))
    merged = draft.model_copy(deep=True)
    seen = {f.form_field for f in merged.fields}
    for field in written.fields or []:
        label = field.form_field
        if label not in allowed or label in seen:
            continue
        value = (field.value or "").strip()
        if not value:
            continue
        cited = [i.strip() for i in (_CITE.search(value).group(1).split(",")
                                     if _CITE.search(value) else [])]
        cited += [i.strip() for i in str(field.source_msg_id or "").split(",")]
        cited = list(dict.fromkeys(i for i in cited if i in message_ids))
        if not cited:
            # Quiet Core: a finding that does not cite a family message is dropped, never
            # stamped "agent". Stamping it made an invented situation look sourced.
            log.warning("dropped narrative field %r: no family message id behind it", label)
            continue
        value = hooks.redact(plain_text(_CITE.sub("", value).strip()))
        if not value:
            continue
        merged.fields.append(
            FormField(form_field=label, value=value, source_msg_id=",".join(cited))
        )
        seen.add(label)
    return merged


def merge_filing(drafts: dict[str, FilingPlan], written: list[FilingPlan],
                 message_ids: set[str]) -> list[FilingPlan]:
    by_program = {p.program: p for p in written if p.program in drafts}
    return [
        _merge_free_text(draft, by_program.get(key, draft), message_ids)
        for key, draft in drafts.items()
    ]


# --------------------------------------------------------------------------------------
# risk
# --------------------------------------------------------------------------------------

RISK_PROMPT = """You look for reasons a human coordinator must see this case before anything
is filed. Deterministic rules have already run and their reasons are in `pre_rules`.

You may ADD reasons. You may never remove one, never lower `escalate` from true to false,
and never change `programs_held` or `programs_safe_to_file`; those are recomputed after you
answer. You never decide whether anyone is eligible because of immigration status: that is a
question for a human, and saying so is the correct output.

Add a reason when the transcript shows income the family cannot document (self-employment,
cash work, tips), any legal question, an abuse or safety topic, or a family that stopped
replying. Cite the message id in the reason text. Answer with the RiskReport structure."""


def _members_by_index(engine: EngineResult) -> dict[int, dict]:
    return {int(m.get("index", i)): m for i, m in enumerate(engine.members)}


def _relation(member) -> str:
    return str(member.relation.value).lower() if member.relation is not None else ""


def _status(member) -> str:
    return str(member.immigration_status.value).upper() if member.immigration_status else ""


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


_COUNT_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}


def _is_adult(member) -> bool:
    """An age the family stated; a member with no stated age is counted as an adult, which
    is the answer that puts the case in front of a coordinator."""
    age = getattr(member, "age", None)
    try:
        return age is None or float(age.value) >= 18
    except (TypeError, ValueError):
        return True


def _mixed_status_question(mixed: list[tuple[int, Any]]) -> str:
    """The coordinator's question, counted from this household rather than written once."""
    adults = [m for _i, m in mixed if _is_adult(m)]
    n = len(adults)
    if n == 0:
        who = _COUNT_WORDS.get(len(mixed), str(len(mixed)))
        subject = f"{who} household member{'s' if len(mixed) != 1 else ''}"
        verb = "is" if len(mixed) == 1 else "are"
        return (
            f"{subject} {verb} not "
            f"{'a citizen or lawful permanent resident' if len(mixed) == 1 else 'citizens or lawful permanent residents'}. "
            "File the programs that are safe now and hold those lines for you?"
        )
    word = _COUNT_WORDS.get(n, str(n))
    if n == 1:
        return (
            "One adult in this household is not a citizen or lawful permanent resident. "
            "File the children's programs now and hold that adult's lines for you?"
        )
    return (
        f"{word} adults in this household are not citizens or lawful permanent residents. "
        "File the children's programs now and hold the adults' lines for you?"
    )


def pre_rules(case: CaseRecord, now: Optional[datetime] = None) -> RiskReport:
    """The escalations Benefitline decides in code. A model can add to this, never subtract."""
    engine = case.engine_result
    facts = case.facts
    at = (now or now_utc()).date()
    reasons: list[str] = []
    held: list[str] = []
    safe: list[str] = []
    question = ""

    mixed = [
        (i, m) for i, m in enumerate(facts.members)
        if _status(m) and _status(m) not in LEGAL_STATUSES
    ]
    if mixed:
        who = ", ".join(
            f"member {i} ({_relation(m) or 'unstated relation'}), said in {m.immigration_status.source_msg_id}"
            for i, m in mixed
        )
        reasons.append(
            f"a household member is not a citizen or lawful permanent resident: {who}. "
            "Benefitline does not decide immigration eligibility."
        )
        question = _mixed_status_question(mixed)
        eligible = {p.key: p for p in engine.programs if p.eligible}
        for key in ("snap", "medicaid"):
            if key in eligible:
                held.append(f"{key}:adults")
                safe.append(key)
        # Every credit on the same tax return is held together: the engine grants case 4 a
        # $1,000 other-dependent CTC, which a coordinator must see beside the held EITC.
        for key in ("eitc", "ok_eitc", "ctc", "refundable_ctc"):
            program = next((p for p in engine.programs if p.key == key), None)
            if program is not None and (key == "eitc" or program.eligible):
                held.append(key)
        if "wic" in eligible:
            safe.append("wic")
        gates = _members_by_index(engine)
        excluded = [i for i, g in gates.items() if g.get("snap_excluded_member")]
        if excluded:
            reasons.append(
                f"the engine already excludes members {excluded} from the SNAP benefit "
                "(snap_excluded_member), so the amount shown is the children's share."
            )

    recert = _parse_date(facts.recert_due_date.value) if facts.recert_due_date else None
    receives = [str(f.value).lower() for f in facts.already_receives]
    # Measured against the case's own recertification date, never a window around the wall
    # clock: the date is a fact the family stated, so the same case escalates on every run.
    if recert is not None and receives:
        days = (recert - at).days
        when = (
            f"in {days} days" if days > 0
            else ("today" if days == 0 else f"{-days} days ago")
        )
        reasons.append(
            f"recert due: {', '.join(receives)} recertification is due {recert.isoformat()} "
            f"({when} at the reference date {at.isoformat()}), "
            f"said in {facts.recert_due_date.source_msg_id}."
        )
        question = question or (
            f"This household's {', '.join(receives)} recertification is due {recert.isoformat()}. "
            "File the renewal now?"
        )

    for turn in case.transcript[-4:]:
        if turn.get("needs_human"):
            reasons.append(
                f"intake flagged needs_human on message {turn.get('id', '?')}: "
                f"{turn.get('needs_human_reason', 'no reason recorded')}"
            )

    gates = _members_by_index(engine)
    head_index = next(
        (i for i, m in enumerate(facts.members) if _relation(m) in ("self", "head")), 0
    )
    head_gate = gates.get(head_index, {})
    if head_gate.get("snap_excluded_member") and head_gate.get("weekly_hours_worked") is None:
        reasons.append(
            "the head of household is excluded from SNAP and the engine has no weekly work "
            "hours for them, so the work rule cannot be checked in code."
        )

    if not safe and reasons:
        safe = [p.key for p in engine.programs if p.eligible and p.key in forms.PROGRAMS]

    # dedupe, keep order
    ordered_held = list(dict.fromkeys(held))
    ordered_safe = [k for k in dict.fromkeys(safe) if k not in ordered_held]
    return RiskReport(
        escalate=bool(reasons),
        reasons=reasons,
        question_for_coordinator=question,
        programs_safe_to_file=ordered_safe,
        programs_held=ordered_held,
    )


def merge_risk(pre: RiskReport, written: RiskReport) -> RiskReport:
    """The model's additions on top of the code's decision. Nothing of the code's is lost.

    The model may add reasons and a question; it may not escalate. Escalation holds filings,
    so letting one model sentence flip it would let the model subtract from a decision
    `pre_rules` made in code: on a clean case that turns into zero filings, silently.
    """
    reasons = list(pre.reasons)
    for reason in written.reasons or []:
        text = str(reason).strip()
        if text and text not in reasons:
            reasons.append(text)
    return RiskReport(
        escalate=bool(pre.escalate),
        reasons=reasons,
        question_for_coordinator=pre.question_for_coordinator
        or (written.question_for_coordinator or ""),
        programs_safe_to_file=list(pre.programs_safe_to_file),
        programs_held=list(pre.programs_held),
    )


# --------------------------------------------------------------------------------------
# graph nodes
# --------------------------------------------------------------------------------------


def _model_id(model: Any) -> str:
    config = getattr(model, "config", None)
    if isinstance(config, dict) and config.get("model_id"):
        return str(config["model_id"])
    getter = getattr(model, "get_config", None)
    if callable(getter):
        try:
            got = getter()
        except Exception:                              # noqa: BLE001 - a label is not worth a crash
            got = None
        if isinstance(got, dict) and got.get("model_id"):
            return str(got["model_id"])
    return "unknown"


class _AnsweringModel(HookProvider):
    """Records the model that actually produced the answer.

    On a `ModelRouter` the agent's own `model.config["model_id"]` is the router's default,
    so a run the Haiku fallback answered was traced as Sonnet. The router keeps its choice
    in a per-invocation state object inside `invocation_state` and clears it at
    `AfterInvocationEvent`, so the selection is read here, at the end of each model call,
    while it is still there.
    """

    def __init__(self) -> None:
        self.model_id = ""

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AfterModelCallEvent, self._record)

    def _record(self, event: AfterModelCallEvent) -> None:
        if event.stop_response is None:                # this call failed; the next one answers
            return
        state = getattr(event, "invocation_state", None) or {}
        for value in state.values():
            model = getattr(value, "model", None)
            if model is not None and getattr(value, "candidate", None) is not None:
                self.model_id = _model_id(model)
                return
        self.model_id = _model_id(getattr(event.agent, "model", None))


class SpecialistNode(MultiAgentBase):
    """One model call, its validation, its fallback, and its trace row.

    The node always completes. A failure is caught here and answered with the deterministic
    fallback, because the Python graph is fail-fast: a raised exception would kill the run
    and a FAILED status would leave `judgment` waiting for a dependency that never lands.
    """

    def __init__(self, name: str, tier: int, case: CaseRecord, payload: dict, collector: dict):
        super().__init__()
        self.id = name
        self.name = name
        self.tier = tier
        self.case = case
        self.payload = payload
        self.collector = collector

    # each subclass fills these three in
    system_prompt: str = ""

    def _model(self):
        return router() if self.tier == 2 else fallback()

    def _prompt(self) -> str:
        raise NotImplementedError

    async def _answer(self, agent: Agent) -> Any:
        raise NotImplementedError

    def _fallback(self, error: str) -> Any:
        raise NotImplementedError

    async def invoke_async(self, task=None, invocation_state=None, **kwargs) -> MultiAgentResult:
        started = time.time()
        agent = None
        answered = _AnsweringModel()
        try:
            agent = Agent(
                model=self._model(), system_prompt=self.system_prompt,
                callback_handler=None, hooks=[answered],
            )
            value = await self._answer(agent)
            error = ""
        except Exception as exc:                       # noqa: BLE001 - the demo must not die here
            error = f"{type(exc).__name__}: {exc}"
            log.warning("%s node failed (%s); using the deterministic fallback", self.name, error)
            value = self._fallback(error)
        elapsed = int((time.time() - started) * 1000)
        picked = answered.model_id or _model_id(getattr(agent, "model", None))
        self.collector[self.name] = {
            "value": value,
            "error": error,
            "latency_ms": elapsed,
            "model": picked,
            "tier": self.tier,
        }
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={},
            execution_count=1,
            execution_time=elapsed,
        )


class ExplainerNode(SpecialistNode):
    system_prompt = EXPLAINER_PROMPT

    def _prompt(self, correction: str = "") -> str:
        body = {
            "language": self.payload["language"],
            "programs": self.payload["programs"],
            "liheap_url": self.payload["liheap_url"],
            "org_rules": self.payload["org_rules"],
            "household": self.payload["facts"],
        }
        text = json.dumps(body, ensure_ascii=False, indent=1)
        if correction:
            text = f"{text}\n\nYour previous answer was rejected: {correction}"
        return text

    def _bad(self, raw: Explanation) -> list[float]:
        """Every narrative field the model wrote, checked with the same validator."""
        engine = self.case.engine_result
        texts = [raw.message_to_family or ""]
        for entry in raw.per_program or []:
            if isinstance(entry, dict):
                texts.append(str(entry.get("one_line", "")))
        out: list[float] = []
        for text in texts:
            out.extend(bad_amounts(text, engine))
        return out

    async def _answer(self, agent: Agent) -> Explanation:
        engine = self.case.engine_result
        result = await agent.invoke_async(self._prompt(), structured_output_model=Explanation)
        raw = result.structured_output
        bad = self._bad(raw)
        if bad:
            log.warning("explainer wrote amounts the engine did not compute: %s", bad)
            retry = await agent.invoke_async(
                self._prompt(
                    f"it contained {bad}, which are not engine amounts. Use only the amounts "
                    "in `programs`, each written with a leading $."
                ),
                structured_output_model=Explanation,
            )
            raw = retry.structured_output
            # A per-program line the retry still gets wrong is repaired by
            # `clean_explanation`, which puts the deterministic line in its place; only the
            # prose, which has no deterministic substitute, forces the whole fallback.
            still = bad_amounts(raw.message_to_family, engine)
            if still:
                raise ValueError(f"amounts not from the engine after one retry: {still}")
        return clean_explanation(raw, self.case)

    def _fallback(self, error: str) -> Explanation:
        return template_explanation(self.case)


class FilingNode(SpecialistNode):
    system_prompt = FILING_PROMPT

    def _prompt(self) -> str:
        body = {
            "drafts": self.payload["drafts"],
            "free_text_fields": self.payload["free_text_fields"],
            "message_ids": self.payload["message_ids"],
            "transcript": self.payload["transcript_tail"],
            "programs": self.payload["programs"],
        }
        return json.dumps(body, ensure_ascii=False, indent=1)

    async def _answer(self, agent: Agent) -> list[FilingPlan]:
        result = await agent.invoke_async(self._prompt(), structured_output_model=FilingPlans)
        written = result.structured_output.plans
        return merge_filing(
            self.payload["_drafts"], written, set(self.payload["message_ids"])
        )

    def _fallback(self, error: str) -> list[FilingPlan]:
        return list(self.payload["_drafts"].values())


class RiskNode(SpecialistNode):
    system_prompt = RISK_PROMPT

    def _prompt(self, pre: RiskReport) -> str:
        body = {
            "pre_rules": pre.model_dump(),
            "household": self.payload["facts"],
            "programs": self.payload["programs"],
            "transcript": self.payload["transcript_tail"],
            "org_rules": self.payload["org_rules"],
        }
        return json.dumps(body, ensure_ascii=False, indent=1)

    async def _answer(self, agent: Agent) -> RiskReport:
        pre = pre_rules(self.case)
        result = await agent.invoke_async(self._prompt(pre), structured_output_model=RiskReport)
        return merge_risk(pre, result.structured_output)

    def _fallback(self, error: str) -> RiskReport:
        return pre_rules(self.case)


class JudgmentNode(MultiAgentBase):
    """Deterministic. No model. It decides what is filed and what waits for a human."""

    def __init__(self, case: CaseRecord, collector: dict, store: Optional[Store] = None):
        super().__init__()
        self.id = "judgment"
        self.name = "judgment"
        self.case = case
        self.collector = collector
        self.store = store

    async def invoke_async(self, task=None, invocation_state=None, **kwargs) -> MultiAgentResult:
        started = time.time()
        apply_results(self.case, self.collector, store=self.store)
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={},
            execution_count=1,
            execution_time=int((time.time() - started) * 1000),
        )


def _held_members(case: CaseRecord) -> list[int]:
    """Indexes of the members whose lines the Decision Card holds for a coordinator."""
    return [
        i for i, m in enumerate(case.facts.members)
        if _status(m) and _status(m) not in LEGAL_STATUSES
    ]


def _narrow_held_rows(case: CaseRecord, risk: RiskReport) -> list[FilingPlan]:
    """Drop the held members' own rows from a plan whose program is filed anyway.

    `programs_held` carries "snap:adults" beside a filed "snap": the children's application
    goes, the adults' answers do not. Which rows belong to the held members is decided by
    building the same deterministic draft for the household without them and keeping only
    the rows both drafts share, so no label list has to be maintained by hand.
    """
    held_members = _held_members(case)
    partial = {p.split(":", 1)[0] for p in risk.programs_held if ":" in p}
    if not held_members or not partial:
        return list(case.filing_plans)

    reduced_facts = case.facts.model_copy(deep=True)
    reduced_facts.members = [
        m for i, m in enumerate(reduced_facts.members) if i not in held_members
    ]
    out: list[FilingPlan] = []
    for plan in case.filing_plans:
        if plan.program not in partial:
            out.append(plan)
            continue
        try:
            full = forms.plan_filing(plan.program, case.facts, case.engine_result, case=case)
            without = forms.plan_filing(
                plan.program, reduced_facts, case.engine_result, case=case
            )
        except Exception as exc:                       # noqa: BLE001 - a filed plan is not lost
            log.warning("could not narrow %s to the safe rows (%s: %s); filing it whole",
                        plan.program, type(exc).__name__, exc)
            out.append(plan)
            continue
        def rowkey(field: FormField) -> tuple:
            return (field.form_field, field.value, field.source_msg_id)
        theirs = Counter(rowkey(f) for f in full.fields) - Counter(
            rowkey(f) for f in without.fields
        )
        kept = plan.model_copy(deep=True)
        drop = Counter(theirs)
        rows = []
        for field in kept.fields:
            key = rowkey(field)
            if drop[key] > 0:
                drop[key] -= 1
                continue
            rows.append(field)
        if len(rows) != len(kept.fields):
            log.info("held %d %s rows for the coordinator (members %s)",
                     len(kept.fields) - len(rows), plan.program, held_members)
        kept.fields = rows
        out.append(kept)
    return out


def apply_results(case: CaseRecord, collector: dict, store: Optional[Store] = None,
                  now: Optional[datetime] = None) -> CaseRecord:
    """Write the three answers onto the case, then file or escalate. Pure Python."""
    at = now or now_utc()
    explanation: Explanation = collector["explainer"]["value"]
    plans: list[FilingPlan] = collector["filing"]["value"]
    risk: RiskReport = collector["risk"]["value"]

    case.explanation = explanation
    case.filing_plans = list(plans)
    case.risk = risk

    for name in ("explainer", "filing", "risk"):
        row = collector[name]
        tier = str(row["tier"])
        case.tier_counts[tier] = int(case.tier_counts.get(tier, 0)) + 1
        Ledger.add(
            case,
            action=f"{name} answered",
            why=f"model {row['model']}, {row['latency_ms']} ms, tier {row['tier']}",
            undo="none: this row records a model call, not a change to the case",
            tier=row["tier"],
            is_read=True,
            now=at,
        )
        if row["error"]:
            Ledger.add(
                case,
                action=f"fallback used: {name}: {row['error']}",
                why="the node failed and the deterministic fallback answered instead",
                undo=f"re-run the {name} specialist for this case",
                tier=row["tier"],
                is_read=True,
                now=at,
            )

    if risk.escalate:
        # Narrow before the card is written, so the card describes the rows that are filed.
        case.filing_plans = _narrow_held_rows(case, risk)
        cards.make_card(case, risk, store=store, now=at)
        case.status = "escalated"
        filed = [p for p in case.filing_plans if p.program in set(risk.programs_safe_to_file)]
        why_suffix = "held for the coordinator: " + (", ".join(risk.programs_held) or "none")
    else:
        filed = list(case.filing_plans)
        why_suffix = "no risk rule fired"

    for plan in filed:
        Ledger.add(
            case,
            action=f"filed {plan.program} with {plan.form_name}",
            why=(
                f"{len(plan.fields)} fields from the family's own messages; "
                f"deadline {plan.deadline or 'none stated'}; {why_suffix}"
            ),
            undo=f"withdraw the {plan.program} filing on {plan.form_name} before the "
            "veto window closes",
            tier=2,
            now=at,
        )
    if not risk.escalate:
        case.status = "filed"

    case.updated_at = at
    if store is not None:
        store.put(case)
    return case


# --------------------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------------------


def all_three_complete(state) -> bool:
    """The AND join. Python graph edges fan out on OR, so the wait is written as a condition."""
    return {"explainer", "filing", "risk"}.issubset(set(state.results))


def build_graph(case: CaseRecord, payload: dict, collector: dict,
                store: Optional[Store] = None):
    builder = GraphBuilder()
    builder.add_node(ExplainerNode("explainer", 2, case, payload, collector), "explainer")
    builder.add_node(FilingNode("filing", 2, case, payload, collector), "filing")
    builder.add_node(RiskNode("risk", 1, case, payload, collector), "risk")
    builder.add_node(JudgmentNode(case, collector, store=store), "judgment")
    for node in ("explainer", "filing", "risk"):
        builder.set_entry_point(node)
        builder.add_edge(node, "judgment", condition=all_three_complete)
    return builder.build()


def _fill_missing(case: CaseRecord, payload: dict, collector: dict, error: str) -> None:
    """A node that never reported still gets a deterministic answer."""
    defaults = {
        "explainer": lambda: template_explanation(case),
        "filing": lambda: list(payload["_drafts"].values()),
        "risk": lambda: pre_rules(case),
    }
    tiers = {"explainer": 2, "filing": 2, "risk": 1}
    for name, make in defaults.items():
        if name not in collector:
            collector[name] = {
                "value": make(),
                "error": error,
                "latency_ms": 0,
                "model": "none",
                "tier": tiers[name],
            }


def run_specialists(case: CaseRecord, store: Optional[Store] = None) -> CaseRecord:
    """Explain, plan the filings, weigh the risk, then decide. Returns the same case object."""
    payload = build_payload(case, store=store)
    collector: dict[str, dict] = {}
    graph = build_graph(case, payload, collector, store=store)
    try:
        asyncio.run(graph.invoke_async(f"Benefitline case {case.case_id}"))
    except Exception as exc:                           # noqa: BLE001 - the demo must not die here
        error = f"{type(exc).__name__}: {exc}"
        log.warning("specialists graph failed (%s); finishing deterministically", error)
        _fill_missing(case, payload, collector, error)
        apply_results(case, collector, store=store)
        return case

    if not {"explainer", "filing", "risk"}.issubset(collector):
        missing = sorted({"explainer", "filing", "risk"} - set(collector))
        _fill_missing(case, payload, collector, f"node did not report: {missing}")
        apply_results(case, collector, store=store)
    elif case.risk is None:                            # judgment never ran
        _fill_missing(case, payload, collector, "judgment node did not run")
        apply_results(case, collector, store=store)
    return case
