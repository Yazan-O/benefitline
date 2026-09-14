"""Phase 2 done-check: the Strands Evals bench.

Every household in `gallery/scripts.json`, in every language that gallery gives it (English
only as of this writing, six cases), each run twice, as Strands Evals `Case` objects.
The case list and the languages are read from the gallery at run time, never hard-coded here.
A `Case`'s user simulator plays the fictional family; the agent under test is
`benefitline.intake.IntakeAgent` driving a `CaseRecord`.

    python evals/bench.py                      # every case in the gallery, twice
    python evals/bench.py --only case1_single_mother_two_kids:en   # one configuration, both repeats

Writes one JSON per run plus `bench_runs.json` to `_runs/2026-09-13_phase2_bench/`, and
`evals/RESULTS.md`.

--------------------------------------------------------------------------------------------
Differences from `../09_EVALS_SDK.md` found in the installed strands-agents-evals 1.2.0
--------------------------------------------------------------------------------------------
1. `ActorSimulator.from_case_for_user_simulator(case=...)` is NOT used here even though it
   exists with the documented signature. Its `_generate_profile_from_case` makes an extra,
   uncontrolled LLM call through a bare `Agent(callback_handler=None)` (no model, no region
   pinned) and *invents* an `ActorProfile` from `case.input`. This bench needs the family's
   exact fixture facts in the profile and needs the region pinned to us-east-1, so it
   hand-builds `ActorProfile(traits=..., context=..., actor_goal=...)` and calls the
   `ActorSimulator(...)` constructor directly, which the class docstring endorses
   ("If you already have a profile, use __init__() directly").
2. `model:` is type-hinted `str | None` but is passed straight to `Agent(model=...)`, so a
   `BedrockModel` instance is accepted. A bare model-id string would let Strands build its own
   BedrockModel and resolve the region to us-west-2; an instance from `benefitline.models` is
   passed instead.
3. `ActorResponse.message` is `str | None` (null when `stop=true`). The doc's loop line
   `str(user_result.structured_output.message)` would feed the literal "None" to the agent, so
   this bench breaks on `stop or message is None` before stringifying.
4. `ActorSimulator._initialize_conversation` calls `random.choice(INITIAL_GREETINGS)`, i.e.
   global-`random` nondeterminism at construction. `random.seed(...)` is set immediately before
   each construction so a repeat sees the same greeting.
5. `EvaluationOutput` field is `reason`, not `reasoning`. `EvaluationData` carries no case
   identity beyond `name`/`input`/`actual_output`, so the whole per-run record is handed back
   as the task output and every evaluator reads it from `evaluation_case.actual_output`.
6. `strands_evals.__version__` does not exist; the version comes from
   `importlib.metadata.version("strands-agents-evals")`.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime
from importlib.metadata import version as _pkg_version

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "gallery"))

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

from strands import Agent  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from strands_evals import Case, Experiment  # noqa: E402
from strands_evals.evaluators import Evaluator  # noqa: E402
from strands_evals.simulation import ActorSimulator  # noqa: E402
from strands_evals.types.evaluation import EvaluationData, EvaluationOutput  # noqa: E402
from strands_evals.types.simulation import ActorProfile  # noqa: E402

from benefitline import engine, models  # noqa: E402
from benefitline.facts import facts_match, missing_facts, to_household  # noqa: E402
from benefitline.intake import IntakeAgent  # noqa: E402
from benefitline.schemas import CaseRecord  # noqa: E402
from cases import CASES  # noqa: E402

EVALS_SDK_VERSION = _pkg_version("strands-agents-evals")
RUN_DIR = os.path.join(ROOT, "_runs", "2026-09-13_phase2_bench")
SCRIPTS = os.path.join(ROOT, "gallery", "scripts.json")
GROUND_TRUTH = os.path.join(ROOT, "gallery", "ground_truth.json")
RESULTS_MD = os.path.join(HERE, "RESULTS.md")

MAX_QUESTIONS = 6
MAX_TURNS = 10          # simulator turn budget, per the brief
MAX_AGENT_TURNS = 14    # hard stop on the conversation loop

REPEATS = 2

ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
# A dollar figure: a currency marker glued to a number. The family says "950 a month" freely;
# what the agent must never write is a priced amount.
DOLLAR = re.compile(r"(\$\s*\d)|(\d[\d,]*(?:\.\d+)?\s*(?:dollars?|USD|بالشهر\s*دولار|دولار))", re.I)

# ---------------------------------------------------------------------------- model-call counter
_CALLS = {"n": 0}
_orig_stream = BedrockModel.stream


def _counting_stream(self, *a, **kw):
    _CALLS["n"] += 1
    return _orig_stream(self, *a, **kw)


BedrockModel.stream = _counting_stream


# ------------------------------------------------------------------------------- family profile
def _household(case_id: str):
    h = next((x for x in CASES if x.case_id == case_id), None)
    if h is None:
        # ValueError, not SystemExit: a SystemExit inside the task escapes the evals SDK's
        # `except Exception` and tears down the whole batch, so one bad id would cost every row.
        raise ValueError(f"unknown gallery case {case_id!r}")
    return h


# How each earner would say their income out loud. Derived from gallery/cases.py:
# case1 is 14 * 30 * 52, i.e. an hourly job; the others are stated as annual in scripts.json.
INCOME_PHRASING = {
    "case1_single_mother_two_kids": "you are paid $14 an hour and work about 30 hours a week",
    "case2_couple_newborn": "you make about $32,000 a year at your job",
    "case4_mixed_status": "you make about $26,000 a year cleaning houses, 40 hours a week",
    "case5_on_snap_recert_due": "you make about $19,000 a year",
}

EXTRA_CONTEXT = {
    "case3b_senior_alone_with_medical": (
        "You pay about $180 a month out of pocket for medicines. Your only health insurance "
        "premium is Medicare Part B, exactly $202.90 a month; if asked for the premium say "
        "202.90, never a rounded or different figure. Between rent and medicines there is "
        "nothing left."
    ),
    "case4_mixed_status": (
        "Your two children were born here in the United States. You and your husband do not have "
        "immigration papers. If the agent asks whether everyone in the home is a citizen or "
        "permanent resident, say honestly that the children are citizens and the two of you are not."
    ),
    "case5_on_snap_recert_due": (
        "You already receive SNAP, and your renewal letter says the renewal is due in 12 days."
    ),
}


def _profile_context(case_id: str, lang: str) -> str:
    h = _household(case_id)
    lines = [
        "You are a fictional family in Tulsa, Oklahoma, texting a benefits helpline.",
        f"There are {len(h.members)} people in your home.",
        f"Your rent is ${h.rent:,.0f} a month and your utilities are about ${h.utility_expense:,.0f} a month.",
    ]
    for i, p in enumerate(h.members):
        who = "you" if p.is_tax_unit_head else ("your spouse" if p.is_tax_unit_spouse else "your child")
        bits = [f"{who}: age {p.age}"]
        if p.employment_income:
            bits.append(INCOME_PHRASING.get(case_id, f"earns about ${p.employment_income:,.0f} a year"))
        if p.social_security_retirement:
            bits.append(f"gets ${p.social_security_retirement / 12:,.0f} a month from Social Security")
        if p.is_pregnant:
            bits.append("is pregnant")
        if p.is_breastfeeding:
            bits.append("is breastfeeding a baby")
        if p.is_disabled:
            bits.append("is disabled")
        if p.immigration_status and p.immigration_status != "CITIZEN":
            bits.append("does not have immigration papers")
        lines.append("- " + "; ".join(bits) + ".")
    # The fixture's zeros are facts too. Without them the simulator invents plausible numbers
    # (a first run had case3's senior volunteer $50 of medicine and a $170 Medicare premium
    # that gallery/cases.py does not have), which would score intake against an invented household.
    if not any(p.is_pregnant or p.is_breastfeeding for p in h.members):
        lines.append("Nobody in the home is pregnant and nobody is breastfeeding.")
    if not any(p.other_medical_expenses or p.medical_expense_health_insurance_premiums
               for p in h.members):
        lines.append("You pay nothing out of pocket for medicine or doctor visits and you pay no "
                     "health insurance premium. If asked, the answer is zero, nothing.")
    if all((p.immigration_status or "CITIZEN") == "CITIZEN" for p in h.members):
        lines.append("Everyone in the home is a US citizen.")
    lines.append("Give every amount exactly as it is written above; never convert it to another "
                 "period yourself. If you are asked for a monthly figure and you only know the "
                 "yearly one, say the yearly one and let the helpline do the arithmetic.")
    if case_id in EXTRA_CONTEXT:
        lines.append(EXTRA_CONTEXT[case_id])
    if lang == "ar":
        lines.append(
            "You are an Arabic-speaking family. You write ONLY in Arabic script, in colloquial "
            "everyday Arabic, never in English and never in Latin letters."
        )
    return "\n".join(lines)


def _profile(case_id: str, lang: str) -> ActorProfile:
    return ActorProfile(
        traits={
            "role": "a parent or elder in a low-income household asking about benefits",
            "communication_style": "short, plain, everyday text messages; no jargon",
            "language": "Arabic script only" if lang == "ar" else "English",
            "disclosure": ("answers ONLY the question that was just asked, one answer per message; "
                           "never lists every fact at once unless the question is open-ended"),
            "numeracy": "gives amounts the way an ordinary person would, e.g. 'about 950'",
        },
        context=_profile_context(case_id, lang),
        actor_goal=("Answer the helpline's questions truthfully from your own situation. Your goal is "
                    "met only when the helpline itself says it has everything it needs; any message "
                    "that ends with a question mark still needs an answer, including a question about "
                    "citizenship or residency, so never stop while a question is unanswered."),
    )


def _scripts() -> dict:
    with open(SCRIPTS, encoding="utf-8") as f:
        return json.load(f)["cases"]


def configs() -> list[tuple[str, str]]:
    """One (case, language) pair per opening in gallery/scripts.json.

    The case list and the languages are read from the gallery, never hard-coded here: when the
    gallery gains or drops a language the bench follows without an edit.
    """
    return [(case_id, lang) for case_id, s in _scripts().items() for lang in sorted(s["opening"])]


def _opening(case_id: str, lang: str) -> str:
    return _scripts()[case_id]["opening"][lang]


# ------------------------------------------------------------------------------------- the run
def _new_case_record(case_id: str) -> CaseRecord:
    return CaseRecord(case_id=case_id, session_id=f"{uuid.uuid4()}-benefitline", gallery_case=case_id)


def run_conversation(case_id: str, lang: str, repeat: int) -> dict:
    """Play one household through IntakeAgent with a Strands Evals user simulator."""
    t0 = time.time()
    calls_at_start = _CALLS["n"]

    random.seed(hash((case_id, lang)) & 0xFFFF)
    sim = ActorSimulator(
        actor_profile=_profile(case_id, lang),
        initial_query=_opening(case_id, lang),
        model=models.router(temperature=0),  # same Sonnet on either profile; the daily quota is per profile
        max_turns=MAX_TURNS,
    )

    agent = IntakeAgent()
    record = _new_case_record(case_id)
    message = sim.initial_query
    stop_reason = "facts_complete"
    sim_turns = 0
    error = ""

    for _ in range(MAX_AGENT_TURNS):
        record = agent.step(record, message)
        if not missing_facts(record.facts):
            break
        if sim_turns >= MAX_TURNS:
            stop_reason = "max_turns"
            break
        reply = record.transcript[-1]["text"]
        out = sim.act(reply).structured_output
        sim_turns += 1
        # Per the brief the conversation ends when the facts are complete or the turns run out,
        # not when the simulator decides its goal is met; a stop with a message still answers.
        if out.message is None:
            stop_reason = getattr(out, "stop_reason", "") or "simulator_stopped"
            break
        message = str(out.message)
    else:
        stop_reason = "max_turns"

    still_missing = missing_facts(record.facts)
    household = _household(case_id)
    mismatches = facts_match(record.facts, household)

    # engine comparison against committed ground truth
    with open(GROUND_TRUTH, encoding="utf-8") as f:
        truth = json.load(f)["cases"][case_id]["programs"]
    engine_ok, engine_detail = False, ""
    try:
        got = engine.compute(to_household(record.facts, case_id))
        diffs = []
        for key, exp in truth.items():
            want, have = exp.get("amount"), got["programs"].get(key, {}).get("amount")
            if want is None and have is None:
                continue
            if want is None or have is None or round(float(want), 2) != round(float(have), 2):
                diffs.append(f"{key}: expected {want}, got {have}")
        engine_ok = not diffs
        engine_detail = "" if engine_ok else diffs[0] + (f" (+{len(diffs) - 1} more)" if len(diffs) > 1 else "")
    except Exception as exc:  # engine refuses incomplete or invalid input
        engine_detail = f"{type(exc).__name__}: {exc}"

    agent_turns = [t for t in record.transcript if t["role"] == "agent"]
    family_ids = {t["id"] for t in record.transcript if t["role"] == "family"}

    bad_citations = []
    for field, fact in _walk_facts(record.facts):
        if fact.source_msg_id not in family_ids:
            bad_citations.append(f"{field} -> {fact.source_msg_id}")

    amounts = [t["text"] for t in agent_turns if DOLLAR.search(t["text"])]
    if lang == "ar":
        wrong_lang = [t["text"] for t in agent_turns if not ARABIC.search(t["text"])]
    else:
        wrong_lang = [t["text"] for t in agent_turns if ARABIC.search(t["text"])]

    return {
        "case": case_id,
        "lang": lang,
        "repeat": repeat,
        "questions_asked": record.questions_asked,
        "turns": len(agent_turns),
        "tier_counts": dict(record.tier_counts),
        "status": record.status,
        "stop_reason": stop_reason,
        "still_missing": still_missing,
        "facts_fields_compared": _compared_count(household),
        "facts_mismatches": mismatches,
        "engine_match": engine_ok,
        "engine_detail": engine_detail,
        "bad_citations": bad_citations,
        "dollar_figures": amounts,
        "wrong_language": wrong_lang,
        "wall_seconds": round(time.time() - t0, 1),
        "model_calls": _CALLS["n"] - calls_at_start,
        "transcript": record.transcript,
        "error": error,
    }


def _walk_facts(facts):
    for name in ("state", "household_size", "rent_monthly", "utilities_monthly", "recert_due_date",
                 "contact_name", "contact_phone", "address"):
        f = getattr(facts, name, None)
        if f is not None:
            yield name, f
    for f in facts.already_receives:
        yield "already_receives", f
    for i, m in enumerate(facts.members):
        for name in type(m).model_fields:
            f = getattr(m, name, None)
            if f is not None:
                yield f"member[{i}].{name}", f


def _compared_count(household) -> int:
    """Fields `facts_match` compares: state, rent, utilities, member count, then 11 per member.

    Eleven, counted off the lists in `facts.facts_match`: five numeric (age, employment_income,
    social_security_retirement, other_medical_expenses, medical_expense_health_insurance_premiums),
    five boolean (is_pregnant, is_breastfeeding, is_disabled, is_tax_unit_head, is_tax_unit_spouse)
    and immigration_status.
    """
    return 4 + 11 * len(household.members)


# --------------------------------------------------------------------------------- evaluators
def _rec(evaluation_case: EvaluationData) -> dict:
    out = evaluation_case.actual_output
    return out["run"] if isinstance(out, dict) and "run" in out else out


class _Sync(Evaluator):
    def evaluate_async(self, evaluation_case):  # noqa: D102
        async def _run():
            return self.evaluate(evaluation_case)
        return _run()


class QuestionBudget(_Sync):
    """(a) The agent asked no more than six questions."""

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        n = r["questions_asked"]
        ok = n <= MAX_QUESTIONS and not r["still_missing"]
        reason = f"{n} questions asked (limit {MAX_QUESTIONS})"
        if r["still_missing"]:
            reason += f"; still missing {r['still_missing']}"
        return [EvaluationOutput(score=1.0 if ok else 0.0, test_pass=ok, reason=reason,
                                 label="questions_asked")]


class FactsGathered(_Sync):
    """(b) Facts gathered == the gallery case inputs (reuses run_intake's `facts_match`)."""

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        bad = r["facts_mismatches"]
        total = r["facts_fields_compared"]
        ok = not bad
        return [EvaluationOutput(score=1.0 if ok else 0.0, test_pass=ok,
                                 reason=f"{total - len(bad)}/{total} fields match" +
                                        ("" if ok else "; " + "; ".join(bad)),
                                 label="facts_match")]


class EngineNumbers(_Sync):
    """(c) `engine.compute` amounts == ground_truth for the case, every program, 2 dp."""

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        ok = bool(r["engine_match"])
        return [EvaluationOutput(score=1.0 if ok else 0.0, test_pass=ok,
                                 reason="all program amounts match ground truth" if ok else r["engine_detail"],
                                 label="engine_match")]


class CitationsPointAtFamily(_Sync):
    """(d) Every Fact.source_msg_id points at a family message.

    Regression guard: `validate_facts` already drops uncited facts inside `step()`, so this
    passes by construction unless that check breaks.
    """

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        bad = r["bad_citations"]
        return [EvaluationOutput(score=1.0 if not bad else 0.0, test_pass=not bad,
                                 reason="every fact cites a family message" if not bad else "; ".join(bad),
                                 label="citations")]


class NoDollarFigures(_Sync):
    """(e) No agent reply contains a dollar figure."""

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        bad = r["dollar_figures"]
        return [EvaluationOutput(score=1.0 if not bad else 0.0, test_pass=not bad,
                                 reason="no dollar figure in any agent reply" if not bad else bad[0],
                                 label="no_amounts")]


class LanguageMatches(_Sync):
    """(f) Every agent reply is in the family's language."""

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        bad = r["wrong_language"]
        return [EvaluationOutput(score=1.0 if not bad else 0.0, test_pass=not bad,
                                 reason=f"all {r['turns']} replies in {r['lang']}" if not bad else bad[0][:120],
                                 label="language")]


JUDGE_PROMPT = """You are grading a benefits-helpline intake agent's side of a text conversation.

Rubric, three equally weighted parts:
1. One question at a time: each reply asks at most one question.
2. Plain language: short everyday words a person with no benefits knowledge understands; no
   agency jargon, no form numbers, no acronyms left unexplained.
3. No legal advice: the agent never tells the family what their immigration or legal status
   means for them, and never states an eligibility amount in dollars.

Not violations, do not penalise them:
- Asking once, as a plain factual question, whether everyone in the home is a US citizen or
  permanent resident. That is a required intake question, not legal advice.
- Saying it will "check what you qualify for". Stating a dollar amount is the violation.
- Naming a program the family already named (SNAP, Social Security, Medicare) or a word the
  family used first.

Score 1.0 only if all three hold in every reply. Subtract about 0.33 for each part violated.
Give the score as a number between 0 and 1 and one sentence of reason naming the failing reply
if any.

Agent replies, in order:
{replies}
"""


class OneQuestionPlainLanguage(_Sync):
    """Judge (Haiku): one question at a time, plain language, no legal advice."""

    def __init__(self):
        super().__init__()
        self._judge = Agent(model=models.fallback(temperature=0), callback_handler=None)

    def evaluate(self, evaluation_case):
        r = _rec(evaluation_case)
        replies = "\n".join(f"{i + 1}. {t['text']}" for i, t in enumerate(r["agent_replies"]))
        out = self._judge(JUDGE_PROMPT.format(replies=replies),
                          structured_output_model=EvaluationOutput).structured_output
        out.label = "judge_one_question_plain_no_legal"
        return [out]


EVALUATORS = [QuestionBudget(), FactsGathered(), EngineNumbers(), CitationsPointAtFamily(),
              NoDollarFigures(), LanguageMatches(), OneQuestionPlainLanguage()]


# ------------------------------------------------------------------------------------ the bench
def build_cases(configs) -> list[Case]:
    return [Case(name=f"{c}|{lang}|run{r + 1}",
                 input=_opening(c, lang),
                 metadata={"gallery_case": c, "lang": lang, "repeat": r + 1,
                           "task_description": "The helpline has gathered every fact the engine needs."})
            for c, lang in configs for r in range(REPEATS)]


BENCH_STAMP = datetime.now().strftime("%Y-%m-%d %H:%M")


def _run_path(case_id: str, lang: str, repeat: int) -> str:
    return os.path.join(RUN_DIR, f"run_{case_id}_{lang}_r{repeat}.json")


def task(case: Case):
    md = case.metadata
    try:
        run = run_conversation(md["gallery_case"], md["lang"], md["repeat"])
    except Exception as exc:
        # The evals SDK swallows a task exception and drops the row. Leave a file behind so the
        # table names the failure instead of silently reporting fewer cases (or a stale file).
        run = {"case": md["gallery_case"], "lang": md["lang"], "repeat": md["repeat"],
               "error": f"{type(exc).__name__}: {str(exc)[:300]}", "bench_run": BENCH_STAMP}
        with open(_run_path(run["case"], run["lang"], run["repeat"]), "w", encoding="utf-8") as f:
            json.dump(run, f, ensure_ascii=False, indent=2)
        raise
    run["bench_run"] = BENCH_STAMP
    run["agent_replies"] = [t for t in run["transcript"] if t["role"] == "agent"]
    with open(_run_path(md["gallery_case"], md["lang"], md["repeat"]), "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2)
    return {"output": run}


IDENTITY_KEYS = ["questions_asked", "turns", "tier_counts", "facts_mismatches", "engine_match",
                 "still_missing", "bad_citations", "dollar_figures", "wrong_language"]


def _load_runs(stamp: str | None = None) -> list[dict]:
    """Run files in RUN_DIR; with `stamp`, only the files this bench run wrote."""
    runs = []
    for name in sorted(os.listdir(RUN_DIR)):
        if name.startswith("run_") and name.endswith(".json"):
            with open(os.path.join(RUN_DIR, name), encoding="utf-8") as f:
                r = json.load(f)
            if stamp is None or r.get("bench_run") == stamp:
                runs.append(r)
    return runs


def _shelve_stale_runs() -> None:
    """Move earlier run files aside so a run that dies cannot be reported from an older file."""
    stale = [n for n in os.listdir(RUN_DIR) if n.startswith("run_") and n.endswith(".json")]
    if not stale:
        return
    dest = os.path.join(RUN_DIR, "stale_" + BENCH_STAMP.replace(":", "").replace(" ", "_"))
    os.makedirs(dest, exist_ok=True)
    for n in stale:
        os.replace(os.path.join(RUN_DIR, n), os.path.join(dest, n))


def write_results(runs: list[dict], scores: dict, expected: list[tuple[str, str]] | None = None,
                  stamp: str | None = None) -> str:
    by_cfg: dict[tuple[str, str], list[dict]] = {}
    errors: dict[tuple[str, str], list[str]] = {}
    for r in runs:
        if r.get("error") and "transcript" not in r:
            errors.setdefault((r["case"], r["lang"]), []).append(f"r{r['repeat']}: {r['error']}")
        else:
            by_cfg.setdefault((r["case"], r["lang"]), []).append(r)

    rows, failures = [], []
    all_pass = True
    missing = sorted((set(expected or []) | set(errors)) - set(by_cfg))
    for cfg in missing:
        all_pass = False
        why = "; ".join(errors.get(cfg, [])) or "no run file landed (task never returned)"
        rows.append(f"| {cfg[0]} | {cfg[1]} | no run | - | FAIL | - | - | - |")
        failures.append(f"### {cfg[0]} / {cfg[1]}\n- run did not complete: {why}")
    for (case_id, lang), pair in sorted(by_cfg.items()):
        pair = sorted(pair, key=lambda x: x["repeat"])
        a = pair[0]
        identical = (len(pair) == 2 and
                     all(json.dumps(pair[0][k], sort_keys=True, ensure_ascii=False) ==
                         json.dumps(pair[1][k], sort_keys=True, ensure_ascii=False) for k in IDENTITY_KEYS))
        n_fields = a["facts_fields_compared"]
        matched = n_fields - len(a["facts_mismatches"])
        tc = a["tier_counts"]
        judge = scores.get((case_id, lang), {}).get("judge")
        judge_s = "n/a" if judge is None else f"{judge:.2f}"
        q_ok = a["questions_asked"] <= MAX_QUESTIONS and not a["still_missing"]
        rows.append(
            f"| {case_id} | {lang} | {a['questions_asked']}{'' if q_ok else ' (over/incomplete)'} "
            f"| {matched}/{n_fields} | {'pass' if a['engine_match'] else 'FAIL'} "
            f"| {tc.get('0', 0)}/{tc.get('1', 0)}/{tc.get('2', 0)} | {judge_s} "
            f"| {'yes' if identical else 'no'} |")
        row_fail = (not q_ok) or (a["facts_mismatches"]) or (not a["engine_match"]) or \
            a["bad_citations"] or a["dollar_figures"] or a["wrong_language"]
        judge_low = judge is not None and judge < 1.0
        if row_fail or judge_low:
            if row_fail:
                all_pass = False  # the judge column never decides the verdict
            detail = [f"### {case_id} / {lang}"]
            for jr in (scores.get((case_id, lang), {}).get("judge_reasons") or []) if judge_low else []:
                detail.append(f"- judge (advisory, not part of the verdict): {jr}")
            if not q_ok:
                detail.append(f"- questions asked {a['questions_asked']} (limit {MAX_QUESTIONS}); "
                              f"still missing {a['still_missing']}; stop reason {a['stop_reason']}")
            for m in a["facts_mismatches"]:
                detail.append(f"- fact mismatch: {m}")
            if not a["engine_match"]:
                detail.append(f"- engine: {a['engine_detail']}")
            for c in a["bad_citations"]:
                detail.append(f"- citation not a family message: {c}")
            for d in a["dollar_figures"]:
                detail.append(f"- agent reply contains a dollar figure: {d}")
            for w in a["wrong_language"]:
                detail.append(f"- agent reply not in {lang}: {w[:160]}")
            failures.append("\n".join(detail))

    verdict = (f"Phase 2 done-check: PASS (all {len(runs)} runs <= 6 questions, facts and engine "
               "numbers match)"
               if all_pass else
               "Phase 2 done-check: FAIL (" + ", ".join(
                   [f"{c}/{l} (no run)" for (c, l) in missing] +
                   [f"{c}/{l}" for (c, l) in sorted(by_cfg) if any(
                       x for x in [by_cfg[(c, l)][0]["facts_mismatches"],
                                   by_cfg[(c, l)][0]["bad_citations"],
                                   by_cfg[(c, l)][0]["dollar_figures"],
                                   by_cfg[(c, l)][0]["wrong_language"]]) or
                   not by_cfg[(c, l)][0]["engine_match"] or
                   by_cfg[(c, l)][0]["questions_asked"] > MAX_QUESTIONS or
                   by_cfg[(c, l)][0]["still_missing"]]) + ")")

    md = [
        "# Phase 2 done-check: intake bench (Strands Evals)",
        "",
        verdict,
        "",
        f"Run started {stamp or 'unknown (unstamped run files from more than one earlier run)'} "
        f"America/Chicago, table written "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}. Every cell below comes from a run file this "
        f"bench run wrote (`bench_run` stamp in each `run_*.json`); earlier files are shelved under "
        f"`stale_*/`; no number is transcribed from anywhere else.",
        "",
        "| case | language | questions asked | facts matched | engine match | tier 0/1/2 | judge | run-to-run identical |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "Columns: **questions asked** counts agent replies containing a question mark (Latin or Arabic); "
        "**facts matched** is `facts.facts_match` over state, rent, utilities, member count and eleven fields "
        "per member; **engine match** compares `engine.compute(to_household(...))` program amounts against "
        "`gallery/ground_truth.json` at two decimal places, all programs; **tier 0/1/2** is the case record's "
        "`tier_counts`; **judge** is a Haiku rating of one-question-at-a-time / plain language / no legal "
        "advice, 0-1; **run-to-run identical** compares the two repeats on "
        f"{', '.join(IDENTITY_KEYS)} (not transcript bytes: the user simulator is a separate LLM and its "
        "wording varies even at temperature 0).",
        "",
        "## Failure detail",
        "",
    ]
    md.append("\n\n".join(failures) if failures else "None. Every row passed every check.")
    md += [
        "",
        "## Reproduce",
        "",
        "```",
        "cd D:\\PHD_Take2\\03_Competitions\\amazon_agents\\benefitline",
        "python evals/bench.py",
        "```",
        "",
        f"strands-agents-evals {EVALS_SDK_VERSION}; strands-agents {_pkg_version('strands-agents')}; "
        f"policyengine-us {_pkg_version('policyengine-us')}. Credentials from `.env`, "
        "`BYPASS_TOOL_CONSENT=true`, region us-east-1. Raw per-run JSON: "
        "`_runs/2026-09-13_phase2_bench/`.",
        "",
        "## What each check can kill",
        "",
        "- questions asked: the done-check itself (six questions or fewer, nothing still missing).",
        "- facts matched: intake silently mis-reading or dropping a value the family gave.",
        "- engine match: a fact that survives `facts_match` but moves a benefit amount.",
        "- citations: a fact stored without a family message behind it. `validate_facts` already drops "
        "those inside `step()`, so this is a regression guard, not independent evidence.",
        f"- no dollar figure: an amount leaking out of the model. Regex `{DOLLAR.pattern}` over agent replies only.",
        "- language: an agent reply that is not in the family's language (every gallery script is "
        "English, so an Arabic-script reply is the failure).",
        "- judge: wording quality, which no deterministic check covers.",
        "",
        "## Notes on the harness",
        "",
        "- The user simulator is a hand-built `ActorProfile` passed to `ActorSimulator(...)` directly, "
        "not `from_case_for_user_simulator`, which would invent the family's facts in an extra "
        "uncontrolled model call. The profile carries the household exactly as `gallery/cases.py` "
        "defines it, including its zeros: without \"nobody is pregnant\" and \"you pay nothing for "
        "medicine\" the simulator volunteered a $50 medicine bill and a $170 Medicare premium the "
        "fixture does not have, and the bench then scored intake against an invented household. "
        "The full list of differences from `../09_EVALS_SDK.md` is at the top of `evals/bench.py`.",
        "- The simulator's own `stop` signal is recorded but does not end the conversation: per the "
        "done-check a run ends when `facts.missing_facts` is empty or the turns run out.",
        "- The judge rubric was revised once, before every run in the table above, after Haiku scored "
        "0.01-0.67 by counting the intake question \"is everyone in the home a US citizen or "
        "permanent resident?\" as legal advice and \"I will check what you qualify for\" as stating "
        "an amount. `src/DESIGN.md` mandates that citizenship question, so the rubric now carves both "
        "out. The revision touches the judge column only; the PASS/FAIL verdict is decided by the "
        "questions, facts and engine columns, which no rubric affects.",
        "",
    ]
    text = "\n".join(md)
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="gallery_case:lang, one configuration only")
    ap.add_argument("--report-only", action="store_true", help="rebuild RESULTS.md from saved run JSON")
    args = ap.parse_args()

    os.makedirs(RUN_DIR, exist_ok=True)
    cfgs = configs()
    if args.only:
        c, _, lang = args.only.partition(":")
        cfgs = [(c, lang or "en")]

    scores: dict[tuple[str, str], dict] = {}
    if not args.report_only:
        _shelve_stale_runs()
        experiment = Experiment(cases=build_cases(cfgs), evaluators=EVALUATORS)
        report = experiment.run_evaluations(task=task)
        with open(os.path.join(RUN_DIR, "report.json"), "w", encoding="utf-8") as f:
            json.dump(_report_dict(report), f, ensure_ascii=False, indent=2, default=str)
        scores = _judge_scores(report)

    if args.report_only:
        latest = max((r.get("bench_run", "") for r in _load_runs()), default="") or None
    else:
        latest = BENCH_STAMP
    runs = [r for r in _load_runs(latest) if (r["case"], r["lang"]) in set(cfgs)]
    print(write_results(runs, scores, expected=cfgs, stamp=latest))
    return 0


def _report_dict(report) -> dict:
    d = report.model_dump() if hasattr(report, "model_dump") else dict(report.__dict__)
    return d


def _judge_scores(report) -> dict:
    """Pull the judge evaluator's score per configuration out of the report (min over repeats)."""
    out: dict[tuple[str, str], dict] = {}
    for i, case in enumerate(report.cases):
        md = case["metadata"] if isinstance(case, dict) else case.metadata
        key = (md["gallery_case"], md["lang"])
        entry = out.setdefault(key, {"judge_all": []})
        for ev_out in report.detailed_results[i]:
            if getattr(ev_out, "label", "") == "judge_one_question_plain_no_legal":
                entry["judge_all"].append(ev_out.score)
                entry.setdefault("judge_reasons", []).append(
                    f"{ev_out.score} {getattr(ev_out, 'reason', '') or ''}".strip())
    for entry in out.values():
        vals = [v for v in entry["judge_all"] if v is not None]
        entry["judge"] = min(vals) if vals else None
    return out


if __name__ == "__main__":
    raise SystemExit(main())
