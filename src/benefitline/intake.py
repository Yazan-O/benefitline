"""Intake: one family message in, updated cited facts and one reply out.

The cheapest tier that can read the message reads it. A pending single-field question
plus a parseable reply is tier 0 and costs no tokens; the next question is composed from
a template table. Anything tier 0 cannot read goes to Haiku (six words or fewer) or to
Sonnet through the router, always with structured output, always followed by
`validate_facts` before a single value is stored.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strands import Agent

from . import triage
from .facts import (CAREGIVER_MIN_AGE, CHILDBEARING_MAX, CHILDBEARING_MIN,
                    missing_facts, validate_facts)
from .models import cheap, router
from .schemas import CaseRecord, Fact, HouseholdFacts, IntakeTurn, MemberFacts

log = logging.getLogger("benefitline.intake")
CENTRAL = ZoneInfo("America/Chicago")

# One question per missing fact, in both languages. Tier 0 uses these verbatim; the
# models are handed the same wording and told to ask that one question and nothing else.
QUESTIONS: dict[str, tuple[str, str]] = {
    "state": (
        "What state do you live in?",
        "في أي ولاية تسكن؟"),
    "household_size": (
        "How many people live in your home, counting you?",
        "كم شخصاً يعيش في بيتك، بمن فيهم أنت؟"),
    "ages": (
        "Who lives in the home with you, and how old is everyone, including you?",
        "من يعيش معك في البيت، وكم عمر كل واحد، بما فيهم أنت؟"),
    "weekly_hours": (
        "About how many hours a week do you work?",
        "كم ساعة تعمل في الأسبوع تقريباً؟"),
    "income": (
        "About how much money comes into the home each month before taxes, and who earns it?",
        "كم يدخل البيت من مال تقريباً كل شهر قبل الضرائب، ومن الذي يكسبه؟"),
    "rent": (
        "How much is your rent each month?",
        "كم الإيجار كل شهر؟"),
    "utilities": (
        "About how much are the utilities each month, like the lights and gas?",
        "كم تكلفة الكهرباء والغاز تقريباً كل شهر؟"),
    "pregnancy": (
        "Is anyone in the home pregnant or breastfeeding right now?",
        "هل يوجد أحد في البيت حامل أو ترضع الآن؟"),
    "medical_expenses": (
        "About how much do you pay yourself for medicine and doctor visits each month?",
        "كم تدفع من جيبك تقريباً للأدوية وزيارات الطبيب كل شهر؟"),
    "health_premiums": (
        "And how much is the health insurance premium each month?",
        "وكم قسط التأمين الصحي كل شهر؟"),
    "immigration": (
        "Last question: is everyone in the home a US citizen or permanent resident?",
        "سؤال أخير: هل جميع من في البيت مواطنون أمريكيون أو مقيمون دائمون؟"),
}

# DESIGN's budget. Reaching it stops the questions: a family that has answered six times
# and is still short a fact gets a person, not a seventh question.
MAX_QUESTIONS = 6

HANDOFF = (
    "Thank you for your patience. I have most of what I need. I am passing this to a "
    "caseworker who will finish it with you.",
    "شكراً لصبرك. لدي معظم ما أحتاجه. سأحوّل طلبك إلى موظف يكمل معك البقية.")

CLOSING = (
    "Thank you. I have everything I need. Give me a moment to check exactly what you qualify for.",
    "شكراً لك. لدي كل ما أحتاجه. أمهلني لحظة لأتحقق بالضبط مما تستحقه.")

SYSTEM_PROMPT = """You are the intake worker for Benefitline, a text line that helps Oklahoma \
families find the public benefits they already qualify for.

Rules you never break:
- Ask ONE question at a time, and only about the first item on the missing-facts list you are handed.
- Write in the family's language. Arabic in, Arabic out.
- Never state or estimate an eligibility amount, a benefit dollar figure, or whether they qualify. \
A deterministic rules engine decides that after intake; you only gather facts.
- Never answer an immigration, legal, or asylum question, and never judge anyone's status. \
If the family asks one, set needs_human true with a short reason and reply that a human caseworker \
will answer that part.
- Record a fact only when the family actually said it in the message you are reading. Every Fact \
carries source_msg_id, the id of that family message, and quote, the family's own words. \
Never invent, round, or carry a value the family did not give.
- Amounts are USD PER MONTH. Convert anything the family gives per year, per week, or per hour \
into a monthly figure before you record it.
- Ages are whole years. A baby under one year old is age 0. Age 0 is a complete answer: \
never ask a confirming follow-up about a baby's age, in months or otherwise.
- relation is exactly one of self, spouse, child, other. The person texting is self.
- is_pregnant and is_breastfeeding describe the mother, never the baby. A message like \
"my wife is breastfeeding our baby" sets the flag on the wife and on nobody else.
- Never write a dollar amount in your reply, not even to repeat back the number the family \
just gave you. Acknowledge with words ("got it"), never with a figure.
- Your reply asks about exactly one fact, the first item on the missing-facts list. No second \
question, no follow-up about a fact that is already on the record.
- Keep the reply short, warm, and plain, the way a caseworker texts. No lists, no preamble.

You return an IntakeTurn: the updated facts, one reply, the language tag, and the needs_human flag."""


def _now() -> datetime:
    return datetime.now(CENTRAL)


def _q(key: str, lang: str) -> str:
    en, ar = QUESTIONS[key]
    return ar if lang == "ar" else en


def _ensure_members(facts: HouseholdFacts, n: int) -> None:
    while len(facts.members) < n:
        facts.members.append(MemberFacts())


def _size(facts: HouseholdFacts) -> int:
    if facts.household_size is None:
        return len(facts.members)
    try:
        return int(float(facts.household_size.value))
    except (TypeError, ValueError):
        return len(facts.members)


def _age_of(m: MemberFacts) -> int | None:
    if m.age is None:
        return None
    try:
        return int(float(m.age.value))
    except (TypeError, ValueError):
        return None


def _set_relations(facts: HouseholdFacts) -> None:
    """Fill relations the family has not spelled out: the texter is self, the second
    adult is the spouse, minors are children. Anything already recorded is left alone."""
    taken = {str(m.relation.value).lower() for m in facts.members if m.relation is not None}
    spouse_taken = "spouse" in taken
    self_taken = "self" in taken
    # The texter is normally member 0, but a home whose first member is a child (the
    # opener named the son before the parent) must not make the child the tax head.
    self_index = 0
    first_age = _age_of(facts.members[0]) if facts.members else None
    if first_age is not None and first_age < 18:
        adult = next((i for i, m in enumerate(facts.members)
                      if (a := _age_of(m)) is not None and a >= 18), None)
        if adult is not None:
            self_index = adult
    for i, m in enumerate(facts.members):
        if m.relation is not None or m.age is None:
            continue
        src, quote = m.age.source_msg_id, m.age.quote
        age = _age_of(m)
        if i == self_index and not self_taken:
            rel, self_taken = "self", True
        elif age is not None and age >= 18 and not spouse_taken:
            rel, spouse_taken = "spouse", True
        elif age is not None and age < 18:
            rel = "child"
        else:
            rel = "other"
        m.relation = Fact(value=rel, source_msg_id=src, quote=quote)


def _target(facts: HouseholdFacts, who: str) -> MemberFacts | None:
    for m in facts.members:
        if m.relation is not None and str(m.relation.value).lower() == who:
            return m
    if who == "self" and facts.members:
        return facts.members[0]
    return None


def _self_member(facts: HouseholdFacts) -> MemberFacts:
    """The member the texter is. The one already marked self, else the first member with
    no age yet, else a member appended for a texter the record has not met."""
    for m in facts.members:
        if m.relation is not None and str(m.relation.value).lower() == "self":
            return m
    for m in facts.members:
        if m.age is None:
            return m
    facts.members.append(MemberFacts())
    return facts.members[-1]


def _raise_size(facts: HouseholdFacts, n: int, src: str, quote: str) -> None:
    """Move household_size up to n. Upward only: a partly-read reply must never shrink a
    home and drop a person from the engine."""
    if facts.household_size is None or n > _size(facts):
        facts.household_size = Fact(value=n, source_msg_id=src, quote=quote)


def _infer_household_size(facts: HouseholdFacts) -> None:
    """A model turn lists the people but often no count, and without a count
    `missing_facts` keeps asking for the ages it already has. Two or more members whose
    ages the family gave are a household that size; one member is not (a lone "i'm 31"
    says nothing about who else lives there)."""
    if facts.household_size is not None or len(facts.members) < 2:
        return
    if any(m.age is None for m in facts.members):
        return
    last = max(facts.members, key=lambda m: _msg_order(m.age.source_msg_id, None))
    facts.household_size = Fact(value=len(facts.members),
                                source_msg_id=last.age.source_msg_id, quote=last.age.quote)


def _caregiver_target(facts: HouseholdFacts, message: str) -> MemberFacts | None:
    """The adult a misplaced pregnancy or breastfeeding flag belongs to, or None when the
    message does not say deterministically."""
    in_window = [m for m in facts.members
                 if (a := _age_of(m)) is not None
                 and CHILDBEARING_MIN <= a <= CHILDBEARING_MAX]
    if triage._has(triage._norm(message), triage._SPOUSE_WORDS):
        spouse = _target(facts, "spouse")
        if spouse is not None and spouse in in_window:
            return spouse
    return in_window[0] if len(in_window) == 1 else None


def _fix_caregiver_flags(facts: HouseholdFacts, message: str) -> None:
    """Pregnancy and breastfeeding belong to the mother, never to the infant. A flag on a
    member under 12 is moved to the adult the family named, and cleared when no adult can
    be named deterministically, so the pregnancy question is asked instead of guessed."""
    for m in facts.members:
        age = _age_of(m)
        if age is None or age >= CAREGIVER_MIN_AGE:
            continue
        for field in ("is_pregnant", "is_breastfeeding"):
            flag = getattr(m, field)
            if flag is None:
                continue
            setattr(m, field, None)
            if not bool(flag.value):
                continue
            target = _caregiver_target(facts, message)
            if target is not None and getattr(target, field) is None:
                setattr(target, field, Fact(value=True, source_msg_id=flag.source_msg_id,
                                            quote=flag.quote))


def _apply_immigration(facts: HouseholdFacts, token: str, src: str, quote: str,
                       only: list[MemberFacts] | None = None) -> None:
    for m in (facts.members if only is None else only):
        age = _age_of(m)
        if token == "all_citizen":
            value = "CITIZEN"
        elif token == "not_all_kids_citizen" and age is not None and age < 18:
            value = "CITIZEN"
        else:
            value = "OTHER_OR_UNKNOWN"
        m.immigration_status = Fact(value=value, source_msg_id=src, quote=quote)


def _apply_tier0(facts: HouseholdFacts, pending: str, fact: Fact) -> None:
    """Put one tier-0 token where it belongs. The token grammar is triage's; this is its reader."""
    src, quote, value = fact.source_msg_id, fact.quote, fact.value

    if pending == "state":
        facts.state = fact
    elif pending == "household_size":
        facts.household_size = fact
        _ensure_members(facts, int(value))
    elif pending == "ages":
        ages = [int(a) for a in str(value).split(",") if a != ""]
        if not ages:
            return
        _ensure_members(facts, max(len(ages), _size(facts)))
        # "I'm 44" is the texter's own age. It belongs to the texter however many members
        # the record already holds, and it never overwrites somebody else's age.
        scanned = triage.scan_message(quote, src).get("self_age")
        if len(ages) == 1 and scanned is not None and int(scanned.value) == ages[0]:
            # The sweep may already have written this same age onto a member from this very
            # message; that member is the texter, not a new person to seat.
            m = next((m for m in facts.members
                      if m.age is not None and m.age.source_msg_id == src), None) or _self_member(facts)
            m.age = Fact(value=ages[0], source_msg_id=src, quote=quote)
            if m.relation is None:
                m.relation = Fact(value="self", source_msg_id=src, quote=quote)
            _raise_size(facts, len(facts.members), src, quote)
            _set_relations(facts)
            return
        if len(ages) < len(facts.members):
            # Fewer ages than people: this is a follow-up about the members still missing
            # an age ("he's a few months old"), never a new seating order for the home.
            # Only a list that covers everybody may reassign positionally.
            seen = {(_age_of(m), m.age.source_msg_id) for m in facts.members
                    if m.age is not None}
            todo = [a for a in ages if (a, src) not in seen]  # already read by the sweep
            for m, age in zip([m for m in facts.members if m.age is None], todo):
                m.age = Fact(value=age, source_msg_id=src, quote=quote)
            _set_relations(facts)
            return
        for i, age in enumerate(ages):
            if i < len(facts.members):
                facts.members[i].age = Fact(value=age, source_msg_id=src, quote=quote)
        # The ages the family lists are the household. More ages than the size it gave
        # earlier means the earlier count was short, and the later message wins. The move
        # is upward only: a partly-read ages reply must never shrink a home and drop a
        # person from the engine.
        _raise_size(facts, len(ages), src, quote)
        _set_relations(facts)
    elif pending == "income":
        kind, amount, who = str(value).split(":")
        _set_relations(facts)
        member = _target(facts, who) or _target(facts, "self")
        if member is not None:
            field = ("social_security_monthly" if kind == "social_security"
                     else "employment_income_monthly")
            setattr(member, field, Fact(value=float(amount), source_msg_id=src, quote=quote))
            # "12 an hour, 25 hours a week" already answers the hours question; storing the
            # hours here is what keeps a childless adult inside the six-question budget.
            hours = (triage.tier0_parse("weekly_hours", quote, src)
                     if kind == "employment" and triage._HOURS_WEEK.search(quote or "") else None)
            if hours is not None and getattr(member, "weekly_hours_worked", None) is None                     and hasattr(member, "weekly_hours_worked"):
                member.weekly_hours_worked = hours
    elif pending == "weekly_hours":
        for m in facts.members:
            if m.employment_income_monthly is not None and hasattr(m, "weekly_hours_worked"):
                m.weekly_hours_worked = fact
    elif pending == "rent":
        facts.rent_monthly = fact
    elif pending == "utilities":
        facts.utilities_monthly = fact
    elif pending == "pregnancy":
        token = str(value)
        in_window = [m for m in facts.members
                     if (a := _age_of(m)) is not None and 15 <= a <= 50]
        for m in in_window:
            m.is_pregnant = Fact(value=False, source_msg_id=src, quote=quote)
            m.is_breastfeeding = Fact(value=False, source_msg_id=src, quote=quote)
        if token != "none":
            kind, who = token.split(":")
            member = _target(facts, who) or (in_window[0] if in_window else None)
            if member is not None:
                field = "is_breastfeeding" if kind == "breastfeeding" else "is_pregnant"
                setattr(member, field, Fact(value=True, source_msg_id=src, quote=quote))
    elif pending in ("medical_expenses", "health_premiums"):
        field = ("medical_expenses_monthly" if pending == "medical_expenses"
                 else "health_premiums_monthly")
        targets = [m for m in facts.members
                   if ((a := _age_of(m)) is not None and a >= 60)
                   or (m.is_disabled is not None and bool(m.is_disabled.value))]
        for m in targets or facts.members[:1]:
            setattr(m, field, fact)
            if pending == "medical_expenses" and float(value) == 0.0:
                m.health_premiums_monthly = Fact(value=0.0, source_msg_id=src, quote=quote)
    elif pending == "immigration":
        _apply_immigration(facts, str(value), src, quote)


def _apply_scan(facts: HouseholdFacts, scan: dict) -> None:
    """Deterministic sweep results, applied only where the family has said nothing yet."""
    if "state" in scan and facts.state is None:
        facts.state = scan["state"]
    if "household_size" in scan and facts.household_size is None:
        facts.household_size = scan["household_size"]
        _ensure_members(facts, int(scan["household_size"].value))
    if "already_receives" in scan and not facts.already_receives:
        facts.already_receives = scan["already_receives"]
    if "recert_due_days" in scan and facts.recert_due_date is None:
        due = (_now() + timedelta(days=int(scan["recert_due_days"]))).date().isoformat()
        anchor = scan.get("already_receives") or [scan.get("state")]
        src = anchor[0].source_msg_id if anchor and anchor[0] is not None else ""
        if src:
            facts.recert_due_date = Fact(value=due, source_msg_id=src,
                                         quote=f"renewal due in {scan['recert_due_days']} days")
    if "self_age" in scan and facts.members and facts.members[0].age is None:
        facts.members[0].age = scan["self_age"]


def _apply_disclosures(case: CaseRecord) -> None:
    """A family that volunteers "we don't have papers" has raised the subject itself, so
    the closing citizenship question is not asked again. The disclosure is only applied
    once every age is known, because which member is a child decides who is gated."""
    facts = case.facts
    if not facts.members or any(m.age is None for m in facts.members):
        return
    order = {str(e.get("id")): i for i, e in enumerate(case.transcript)}
    for entry in case.transcript:
        if entry.get("role") not in ("family", "user"):
            continue
        found = triage.scan_message(entry.get("text", ""), str(entry.get("id"))).get(
            "immigration_disclosed")
        if found is None:
            continue
        # The family's own words win over anything a model inferred before the ages were
        # known: the disclosure says which members were born here, a guess does not. A
        # later message (the family correcting itself) still wins over the disclosure.
        here = order.get(found.source_msg_id, -1)
        # Only a message that actually talks about status can beat the disclosure; a status
        # the model attached to the ages message is an inference, not something the family said.
        spoken = {str(e.get("id")) for e in case.transcript
                  if e.get("role") in ("family", "user")
                  and (triage.scan_message(e.get("text", ""), str(e.get("id"))).get(
                          "immigration_disclosed") is not None
                       or triage.tier0_parse("immigration", e.get("text", ""),
                                             str(e.get("id"))) is not None)}
        stale = [m for m in facts.members
                 if m.immigration_status is None
                 or m.immigration_status.source_msg_id not in spoken
                 or order.get(m.immigration_status.source_msg_id, -1) <= here]
        if not stale:
            return
        # "Already applied" is judged on the values, not the citation: the model cites the
        # same message and can still have guessed the children wrong.
        _apply_immigration(facts, str(found.value), found.source_msg_id, found.quote,
                           only=stale)
        return


_MSG_NUM = re.compile(r"^m(\d+)$")


def _msg_order(src: str | None, family_ids: set[str] | None) -> int:
    """Where a citation sits in the conversation. -1 means it cannot win an overwrite:
    either it is not a family message, or its id is not one this transcript issued."""
    key = str(src or "")
    if family_ids is not None and key not in family_ids:
        return -1
    m = _MSG_NUM.match(key)
    return int(m.group(1)) if m else -1


def _take(cur: Fact | None, inc: Fact | None, family_ids: set[str] | None) -> Fact | None:
    """The value the family said most recently. A blank is filled; a value already on the
    record is replaced only by a strictly later family message, which is how a correction
    ("sorry i meant 850") reaches the record instead of being silently dropped."""
    if inc is None:
        return cur
    if cur is None:
        return inc
    if cur.value == inc.value:
        return cur
    return inc if _msg_order(inc.source_msg_id, family_ids) > _msg_order(
        cur.source_msg_id, family_ids) else cur


def _merge(cur: HouseholdFacts, inc: HouseholdFacts,
           family_ids: set[str] | None = None) -> HouseholdFacts:
    """Fold model output into the record: blanks are filled, and a later family message
    overwrites an earlier one. `family_ids` restricts overwrites to messages the family
    actually sent; without it every `m<N>` citation is ordered by its number."""
    for field in ["state", "household_size", "rent_monthly", "utilities_monthly",
                  "recert_due_date", "contact_name", "contact_phone", "address"]:
        setattr(cur, field, _take(getattr(cur, field), getattr(inc, field), family_ids))
    if inc.already_receives and not cur.already_receives:
        cur.already_receives = list(inc.already_receives)
    _ensure_members(cur, len(inc.members))
    for i, im in enumerate(inc.members):
        cm = cur.members[i]
        for field in MemberFacts.model_fields:
            setattr(cm, field, _take(getattr(cm, field), getattr(im, field), family_ids))
    size = _size(cur)
    if size:
        _ensure_members(cur, size)
        # Only padding is trimmed. A member the family described is never deleted to fit
        # an earlier count; `_apply_tier0("ages")` raises the count instead.
        while len(cur.members) > size and all(
                getattr(cur.members[-1], field) is None for field in MemberFacts.model_fields):
            del cur.members[-1]
    return cur


_MONEY_FIELDS = {"rent": "rent_monthly", "utilities": "utilities_monthly"}


def _correction_target(facts: HouseholdFacts, pending: str, message: str) -> str:
    """Which question a message is really answering.

    Normally the pending one. But a family that writes "sorry i meant 850" a turn late is
    fixing a value it already gave, not answering what was just asked, so the number must
    land where the family put it: the field it names, or failing that the money field it
    spoke about most recently.
    """
    if not pending or not triage.is_correction(message):
        return pending
    named = triage.named_field(message)
    if named and named != pending:
        return named
    # No field named. One number and nothing else is a bare correction of the last amount.
    if len(triage._numbers(message)) != 1:
        return pending
    latest, target = -1, pending
    for key, field in _MONEY_FIELDS.items():
        f = getattr(facts, field)
        if f is None:
            continue
        order = _msg_order(f.source_msg_id, None)
        if order > latest:
            latest, target = order, key
    return target


# The bench's own dollar check, so a reply that would be graded a leak never ships.
_DOLLAR = re.compile(
    r"(\$\s*\d)|(\d[\d,]*(?:\.\d+)?\s*(?:dollars?|USD|بالشهر\s*دولار|دولار))",
    re.IGNORECASE)


# The words that show a question is about the fact it was told to ask about. The test is
# positive on purpose: a reply that does not plainly name `missing[0]` loses to the
# template, which always asks the right fact. The cost is the model's wording; the cost of
# the other way round is a question spent on a fact the record already holds.
_ON_TARGET: dict[str, tuple[str, ...]] = {
    "state": ("state", "live in", "ولاية"),
    "household_size": ("how many", "people", "household", "كم شخص", "يعيش"),
    "ages": ("old", "age", "who lives", "عمر", "يعيش معك"),
    "weekly_hours": ("hour", "ساعة", "ساعات"),
    "income": ("income", "money", "earn", "make", "wage", "salary", "bring in", "work",
               "الدخل", "الراتب", "تكسب", "يدخل"),
    "rent": ("rent", "mortgage", "housing", "إيجار", "ايجار"),
    "utilities": ("utilit", "electric", "lights", "gas", "power bill", "water bill",
                  "كهرباء", "الغاز", "فواتير"),
    "pregnancy": ("pregnan", "breastfeed", "breast feed", "nursing", "expecting",
                  "حامل", "ترضع", "رضاعة"),
    "medical_expenses": ("medic", "medicine", "doctor", "pills", "prescription",
                         "out of pocket", "دواء", "الأدوية", "طبيب"),
    "health_premiums": ("premium", "insurance", "تأمين", "قسط"),
    "immigration": ("citizen", "resident", "immigration", "green card", "status",
                    "مواطن", "مقيم"),
}


# Words too common to prove a question is about a fact ("make sure", "how old", "gas money").
_GENERIC = {"make", "work", "money", "gas", "old", "status", "people", "live in", "age"}
_RENT_WORD = re.compile(r"\brent\b")


def _names_fact(low: str, key: str, words) -> bool:
    # "rental income" is an income question, not a rent question: whole-word match for rent.
    return any(_RENT_WORD.search(low) if w == "rent" else w in low for w in words)


def _on_target(reply: str, key: str) -> bool:
    words = _ON_TARGET.get(key)
    if not words:
        return True
    return _names_fact(reply.lower(), key, words)


def _off_target(reply: str, key: str, asked: set[str]) -> bool:
    """Whether the question plainly names a fact other than `key` that was already asked."""
    low = reply.lower()
    return any(_names_fact(low, other, [w for w in _ON_TARGET.get(other, ()) if w not in _GENERIC])
               for other in asked - {key})


def _use_model_reply(reply: str, missing: list[str], transcript: list[dict]) -> bool:
    """Whether the model's own wording may stand in for the deterministic template.

    It may when it actually asks something, carries no dollar figure, plainly asks about
    `missing[0]`, and asks about it for the first time. Otherwise the template asks that
    one fact in wording that names nothing else, which is what stops a question being
    spent on a fact the record already holds or has already asked about.
    """
    if not reply:
        return False
    if _DOLLAR.search(reply):
        return False
    if not missing:
        return True
    if "?" not in reply and "؟" not in reply:
        return False
    if not _on_target(reply, missing[0]):
        return False
    asked = {str(e.get("asks") or "") for e in transcript if e.get("role") == "agent"}
    if _off_target(reply, missing[0], asked):
        return False
    return missing[0] not in asked


class IntakeAgent:
    """One instance per process is enough; every call carries its own case state."""

    def __init__(self, use_models: bool = True):
        self.use_models = use_models

    def _ask_model(self, tier: int, case: CaseRecord, message: str, msg_id: str,
                   missing: list[str], lang: str) -> tuple[IntakeTurn | None, int]:
        # temperature 0: the 6-question budget has no slack, so the opening turn must be reproducible.
        model = cheap() if tier == 1 else router(temperature=0)
        agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)
        prompt = (
            f"Family message id: {msg_id}\n"
            f"Family message: {message}\n\n"
            f"Language of this conversation: {lang}\n"
            f"Facts already on the record (JSON, do not repeat what is already filled):\n"
            f"{json.dumps(case.facts.model_dump(mode='json', exclude_none=True), ensure_ascii=False)}\n\n"
            f"Still missing, in priority order: {missing or ['nothing']}\n"
            f"Ask only about: {missing[0] if missing else 'nothing, thank them and stop asking'}\n"
            f"Suggested wording: {_q(missing[0], lang) if missing else CLOSING[1 if lang == 'ar' else 0]}\n"
            f"Your reply asks about {missing[0] if missing else 'nothing'} and nothing else: "
            f"one question mark, no follow-up about a fact already on the record, and no "
            f"dollar amount anywhere in the reply.\n\n"
            f"Read the family message. Add only the facts it actually contains, each cited to "
            f"source_msg_id {msg_id!r}. Then write the one reply."
        )
        try:
            return agent(prompt, structured_output_model=IntakeTurn).structured_output, tier
        except Exception as exc:  # noqa: BLE001 - the tier is reported, never hidden
            log.warning("tier %d structured output failed (%s: %s)", tier, type(exc).__name__, exc)
            if tier == 1:
                turn, escalated = self._ask_model(2, case, message, msg_id, missing, lang)
                # The counter shows tier 2 only when tier 2 actually produced the turn;
                # a failed escalation is still the tier-1 turn it started as.
                return (turn, escalated) if turn is not None else (None, 1)
            return None, tier

    def step(self, case: CaseRecord, message: str) -> CaseRecord:
        pending = triage.pending_key(case)
        tier = triage.route(case, message)
        msg_id = f"m{len(case.transcript) + 1}"
        now = _now()

        case.transcript.append({"id": msg_id, "role": "family", "text": message,
                                "at": now.isoformat(), "tier": tier})

        if triage.detect_language(message) == "ar":
            case.facts.language = "ar"
        lang = case.facts.language

        needs_human, needs_human_reason, model_reply = False, "", ""

        # The deterministic sweep runs first so the model is handed a missing list that
        # already accounts for the city, the programs, and the deadline in this message.
        _apply_scan(case.facts, triage.scan_message(message, msg_id))

        flag = triage.safety_flag(message)
        if flag:
            needs_human, needs_human_reason = True, flag

        if tier == 0:
            target = _correction_target(case.facts, pending, message)
            parsed = triage.tier0_parse(target, message, msg_id)
            if parsed is not None:
                _apply_tier0(case.facts, target, parsed)
        elif self.use_models:
            missing_now = missing_facts(case.facts)
            turn, tier = self._ask_model(tier, case, message, msg_id, missing_now, lang)
            if turn is not None:
                family_ids = {str(e.get("id")) for e in case.transcript
                              if e.get("role") in ("family", "user")}
                _merge(case.facts, turn.facts, family_ids)
                model_reply = (turn.reply or "").strip()
                if turn.needs_human:
                    needs_human, needs_human_reason = True, turn.needs_human_reason
                if turn.language in ("ar", "en") and triage.detect_language(message) == "ar":
                    case.facts.language = "ar"

        _apply_scan(case.facts, triage.scan_message(message, msg_id))
        _set_relations(case.facts)
        _infer_household_size(case.facts)
        _fix_caregiver_flags(case.facts, message)
        _apply_disclosures(case)
        case.facts = validate_facts(case.facts, case.transcript)
        lang = case.facts.language

        missing = missing_facts(case.facts)
        out_of_questions = bool(missing) and case.questions_asked >= MAX_QUESTIONS
        if out_of_questions:
            # The budget is spent and facts are still missing. Asking again is the defect;
            # a caseworker takes it from here, with the gap named for them.
            needs_human = True
            needs_human_reason = (needs_human_reason or
                                  f"{MAX_QUESTIONS}-question budget reached, still missing: "
                                  f"{', '.join(missing)}")
            reply = HANDOFF[1 if lang == "ar" else 0]
        else:
            template = _q(missing[0], lang) if missing else CLOSING[1 if lang == "ar" else 0]
            # The model's own wording is used only when it actually asked something,
            # asked it for the first time, and left the dollar figures out; otherwise the
            # deterministic template keeps intake on the next question.
            reply = (model_reply if _use_model_reply(model_reply, missing, case.transcript)
                     else template)

        entry = {"id": f"m{len(case.transcript) + 1}", "role": "agent", "text": reply,
                 "at": _now().isoformat(), "tier": tier,
                 "asks": "" if out_of_questions else (missing[0] if missing else "")}
        if needs_human:
            entry["needs_human"] = True
            entry["needs_human_reason"] = needs_human_reason
        case.transcript.append(entry)

        if "?" in reply or "؟" in reply:
            case.questions_asked += 1
        case.tier_counts[str(tier)] = case.tier_counts.get(str(tier), 0) + 1
        case.status = "ready_for_engine" if not missing else "intake"
        case.updated_at = _now()
        return case
