"""Fact bookkeeping. No model call happens in this file.

Three jobs:
  `missing_facts`  what the engine still needs, in the order the family should be asked
  `validate_facts` drop anything the family did not actually say
  `to_household`   turn cited monthly facts into the engine's annual dataclasses
plus `facts_match`, which the gallery bench uses to compare what intake gathered
against the household the case was built from.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import engine
from .schemas import Fact, HouseholdFacts, MemberFacts

log = logging.getLogger("benefitline.facts")

# Ordered exactly as DESIGN.md "Intake behavior" orders the questions.
FACT_ORDER = [
    "state", "household_size", "ages", "income", "weekly_hours", "rent", "utilities",
    "pregnancy", "medical_expenses", "health_premiums", "immigration",
]

# SNAP's ABAWD work rule only bites a home with no child and a working-age adult, so the
# hours question is asked only there. The value rides on MemberFacts.weekly_hours_worked;
# until schemas.py carries that field the question is not asked and the engine is handed
# None, which is exactly "not collected".
HOURS_FIELD = "weekly_hours_worked"
HAS_HOURS_FIELD = HOURS_FIELD in MemberFacts.model_fields
ABAWD_MIN, ABAWD_MAX = 18, 59

FAMILY_ROLES = {"family", "user"}

# A woman 15-50 may be pregnant or breastfeeding (WIC). Sex is never asked, so the
# age window alone opens the question, and it is asked once for the whole home.
CHILDBEARING_MIN, CHILDBEARING_MAX = 15, 50
ELDERLY_AGE = 60  # SNAP's excess-medical deduction starts here

# Pregnancy and breastfeeding describe the mother. A model that reads "my wife is
# breastfeeding our baby" can hang the flag on the baby, which moves WIC by hundreds of
# dollars, so any such flag on a member below this age is cleared in code, not in a prompt.
CAREGIVER_MIN_AGE = 12


def _v(f: Optional[Fact]) -> Any:
    return None if f is None else f.value


def _num(f: Optional[Fact], default: float = 0.0) -> float:
    if f is None:
        return default
    try:
        return float(f.value)
    except (TypeError, ValueError):
        return default


def _age_of(m: MemberFacts) -> Optional[int]:
    if m.age is None:
        return None
    try:
        return int(float(m.age.value))
    except (TypeError, ValueError):
        return None


def _ages(facts: HouseholdFacts) -> list[int]:
    out = []
    for m in facts.members:
        if m.age is not None:
            try:
                out.append(int(float(m.age.value)))
            except (TypeError, ValueError):
                pass
    return out


def _has_income_fact(m: MemberFacts) -> bool:
    return m.employment_income_monthly is not None or m.social_security_monthly is not None


def missing_facts(facts: HouseholdFacts) -> list[str]:
    """What the engine still needs, most valuable first. Empty means `compute` can run.

    Conditional facts appear only when they change a number: pregnancy/breastfeeding
    when someone 15-50 lives in the home, medical costs when someone is 60+ or disabled,
    health premiums only when out-of-pocket medical costs are above zero.
    """
    need: list[str] = []
    if facts.state is None:
        need.append("state")

    # Size and ages are one question ("who lives with you and how old is everyone"), so a
    # home whose size is unknown and a home whose ages are unknown ask for the same thing.
    n = 0
    if facts.household_size is not None:
        try:
            n = int(float(facts.household_size.value))
        except (TypeError, ValueError):
            n = 0
    if n <= 0 or len(facts.members) < n or any(m.age is None for m in facts.members[:n]):
        need.append("ages")
        return need  # every later question depends on who is in the home

    members = facts.members[:n]
    if not any(_has_income_fact(m) for m in members):
        need.append("income")
    elif HAS_HOURS_FIELD and not any(_age_of(m) is not None and _age_of(m) < 18 for m in members):
        working = [m for m in members
                   if _num(m.employment_income_monthly) > 0
                   and (a := _age_of(m)) is not None and ABAWD_MIN <= a <= ABAWD_MAX]
        if working and all(getattr(m, HOURS_FIELD, None) is None for m in working):
            need.append("weekly_hours")
    if facts.rent_monthly is None:
        need.append("rent")
    if facts.utilities_monthly is None:
        need.append("utilities")

    ages = _ages(facts)
    if any(CHILDBEARING_MIN <= a <= CHILDBEARING_MAX for a in ages):
        in_window = [m for m in members
                     if m.age is not None
                     and CHILDBEARING_MIN <= int(float(m.age.value)) <= CHILDBEARING_MAX]
        if all(m.is_pregnant is None and m.is_breastfeeding is None for m in in_window):
            need.append("pregnancy")

    elderly_or_disabled = [
        m for m in members
        if (m.age is not None and int(float(m.age.value)) >= ELDERLY_AGE)
        or (m.is_disabled is not None and bool(m.is_disabled.value))
    ]
    if elderly_or_disabled:
        if all(m.medical_expenses_monthly is None for m in elderly_or_disabled):
            need.append("medical_expenses")
        elif (any(_num(m.medical_expenses_monthly) > 0 for m in elderly_or_disabled)
              and all(m.health_premiums_monthly is None for m in elderly_or_disabled)):
            need.append("health_premiums")

    if any(m.immigration_status is None for m in members):
        need.append("immigration")

    return [k for k in FACT_ORDER if k in need]


def validate_facts(facts: HouseholdFacts, transcript: list[dict]) -> HouseholdFacts:
    """Drop every Fact whose `source_msg_id` is not a family message in this transcript.

    Same bar for both models and for tier 0: a value the family never said does not
    reach the engine. Every drop is logged with the field and the id that failed.
    """
    allowed = {str(e.get("id")) for e in transcript if e.get("role") in FAMILY_ROLES}
    clean = facts.model_copy(deep=True)
    dropped: list[str] = []
    # Before the citation sweep: the ages this reads may themselves be dropped below, and
    # a flag on a baby is wrong whether or not the age behind it survives.
    _clear_infant_caregiver_flags(clean)

    def keep(field: str, f: Optional[Fact]) -> Optional[Fact]:
        if f is None:
            return None
        if f.source_msg_id in allowed:
            return f
        dropped.append(f"{field}(src={f.source_msg_id!r})")
        return None

    for field in ["state", "household_size", "rent_monthly", "utilities_monthly",
                  "recert_due_date", "contact_name", "contact_phone", "address"]:
        setattr(clean, field, keep(field, getattr(clean, field)))

    clean.already_receives = [f for i, f in enumerate(clean.already_receives)
                              if keep(f"already_receives[{i}]", f) is not None]

    member_fields = [f for f in MemberFacts.model_fields]
    for i, m in enumerate(clean.members):
        for field in member_fields:
            setattr(m, field, keep(f"members[{i}].{field}", getattr(m, field)))

    if dropped:
        log.warning("validate_facts dropped %d uncited fact(s): %s", len(dropped), ", ".join(dropped))
    return clean


def _clear_infant_caregiver_flags(facts: HouseholdFacts) -> None:
    """No member under `CAREGIVER_MIN_AGE` is pregnant or breastfeeding. The flag is
    cleared, never moved here: `intake._fix_caregiver_flags` has the family's words and
    moves it to the mother when they name her; whatever survives that is a guess, and a
    cleared flag makes `missing_facts` ask the pregnancy question instead."""
    cleared: list[str] = []
    for i, m in enumerate(facts.members):
        age = _age_of(m)
        if age is None or age >= CAREGIVER_MIN_AGE:
            continue
        for field in ("is_pregnant", "is_breastfeeding"):
            if getattr(m, field) is not None:
                cleared.append(f"members[{i}].{field}")
                setattr(m, field, None)
    if cleared:
        log.warning("validate_facts cleared %d caregiver flag(s) on a child: %s",
                    len(cleared), ", ".join(cleared))


# A status arrives as free text (the model writes what the family said), so the everyday
# spellings are mapped here. Getting this wrong gates a lawful resident as UNDOCUMENTED and
# zeroes the household's SNAP, so the map is exhaustive rather than clever.
STATUS_SYNONYMS = {
    "PERMANENT_RESIDENT": "LEGAL_PERMANENT_RESIDENT",
    "PERMANENT_RESIDENTS": "LEGAL_PERMANENT_RESIDENT",
    "LAWFUL_PERMANENT_RESIDENT": "LEGAL_PERMANENT_RESIDENT",
    "LEGAL_RESIDENT": "LEGAL_PERMANENT_RESIDENT",
    "GREEN_CARD": "LEGAL_PERMANENT_RESIDENT",
    "GREEN_CARD_HOLDER": "LEGAL_PERMANENT_RESIDENT",
    "LPR": "LEGAL_PERMANENT_RESIDENT",
    "مقيم_دائم": "LEGAL_PERMANENT_RESIDENT",
    "مقيمون_دائمون": "LEGAL_PERMANENT_RESIDENT",
    "مقيمين_دائمين": "LEGAL_PERMANENT_RESIDENT",
    "US_CITIZEN": "CITIZEN",
    "U.S._CITIZEN": "CITIZEN",
    "AMERICAN_CITIZEN": "CITIZEN",
    "NATURALIZED_CITIZEN": "CITIZEN",
    "NATURALIZED": "CITIZEN",
    "BORN_HERE": "CITIZEN",
    "BORN_IN_THE_US": "CITIZEN",
    "مواطن": "CITIZEN",
    "مواطنون": "CITIZEN",
    "REFUGEE_STATUS": "REFUGEE",
    "ASYLUM": "ASYLEE",
    "ASYLEE_STATUS": "ASYLEE",
    "DACA_RECIPIENT": "DACA",
    "TEMPORARY_PROTECTED_STATUS": "TPS",
    "NO_PAPERS": "UNDOCUMENTED",
    "UNDOCUMENTED_IMMIGRANT": "UNDOCUMENTED",
}

# Checked in order, on the normalised string, after the exact maps miss.
STATUS_CONTAINS = [
    ("LEGAL_PERMANENT_RESIDENT", "LEGAL_PERMANENT_RESIDENT"),
    ("PERMANENT_RESIDENT", "LEGAL_PERMANENT_RESIDENT"),
    ("GREEN_CARD", "LEGAL_PERMANENT_RESIDENT"),
    ("CITIZEN", "CITIZEN"),
    ("REFUGEE", "REFUGEE"),
    ("ASYL", "ASYLEE"),
    ("DACA", "DACA"),
]


def _immigration(value: Any) -> tuple[str, bool]:
    """Map a gathered status to a PolicyEngine status. Returns (status, is_unknown).

    OTHER_OR_UNKNOWN is not a PolicyEngine status. The engine gate is run at the
    conservative end (UNDOCUMENTED) so no number is promised that an agency would
    refuse, and the member is flagged so the risk agent escalates instead of deciding.
    """
    if value is None:
        return "CITIZEN", False
    s = "_".join(str(value).strip().upper().replace("-", " ").split())
    if s in ("OTHER_OR_UNKNOWN", "UNKNOWN", "OTHER", ""):
        return "UNDOCUMENTED", True
    known = {"CITIZEN", "LEGAL_PERMANENT_RESIDENT", "REFUGEE", "ASYLEE",
             "UNDOCUMENTED", "DACA", "TPS"}
    if s in known:
        return s, False
    if s in STATUS_SYNONYMS:
        return STATUS_SYNONYMS[s], False
    if s.startswith("NOT_") or s.startswith("NO_") or "NON_CITIZEN" in s:
        return "UNDOCUMENTED", True
    for token, status in STATUS_CONTAINS:
        if token in s:
            return status, False
    return "UNDOCUMENTED", True


def to_household(facts: HouseholdFacts, case_id: str) -> engine.Household:
    """Cited monthly facts -> the engine's annual household.

    Income and medical costs are gathered per month and multiplied by 12; `rent` and
    `utility_expense` stay monthly because `engine._situation` annualizes them itself.
    """
    citations: dict[str, str] = {}

    def cite(key: str, f: Optional[Fact]) -> None:
        if f is not None:
            citations[key] = f.source_msg_id

    state = str(_v(facts.state) or "OK").upper()
    cite("state", facts.state)
    cite("household_size", facts.household_size)
    cite("rent", facts.rent_monthly)
    cite("utility_expense", facts.utilities_monthly)

    n = len(facts.members)
    if facts.household_size is not None:
        try:
            n = int(float(facts.household_size.value))
        except (TypeError, ValueError):
            pass
    # The slice exists to drop the blank padding members `_merge` leaves behind. It must
    # never drop a person the family described, so the count floors at the number of
    # members carrying an age even when a stale, smaller household_size is on the record.
    n = max(n, sum(1 for m in facts.members if m.age is not None))
    members_facts = facts.members[:n] if n else facts.members

    people: list[engine.Person] = []
    unknown_status: list[str] = []
    for i, m in enumerate(members_facts):
        status, unknown = _immigration(_v(m.immigration_status))
        if unknown:
            unknown_status.append(str(i))
        rel = str(_v(m.relation) or "").lower()
        for field in ["age", "employment_income_monthly", "social_security_monthly",
                      "is_pregnant", "is_breastfeeding", "immigration_status",
                      "medical_expenses_monthly", "health_premiums_monthly",
                      "is_disabled", "relation"]:
            cite(f"members[{i}].{field}", getattr(m, field))
        people.append(engine.Person(
            age=int(_num(m.age)),
            # Income is annual whole dollars: a yearly figure the family gave ($32,000) is stored
            # as monthly cents (2666.67), and cents times 12 (32000.04) moves the EITC by a penny.
            employment_income=float(round(_num(m.employment_income_monthly) * 12)),
            social_security_retirement=float(round(_num(m.social_security_monthly) * 12)),
            is_pregnant=bool(_v(m.is_pregnant) or False),
            is_breastfeeding=bool(_v(m.is_breastfeeding) or False),
            immigration_status=status,
            other_medical_expenses=round(_num(m.medical_expenses_monthly) * 12, 2),
            medical_expense_health_insurance_premiums=round(_num(m.health_premiums_monthly) * 12, 2),
            is_tax_unit_head=(rel == "self"),
            is_tax_unit_spouse=(rel == "spouse"),
            is_disabled=bool(_v(m.is_disabled) or False),
        ))
        hours = getattr(m, HOURS_FIELD, None) if HAS_HOURS_FIELD else None
        if hours is not None:
            people[-1].weekly_hours_worked = float(_num(hours))

    # `engine.validate` rejects a household without exactly one head, or with two spouses.
    if people:
        heads = [i for i, p in enumerate(people) if p.is_tax_unit_head]
        if not heads:
            heads = [next((i for i, p in enumerate(people) if p.age >= 18), 0)]
        for i, p in enumerate(people):
            p.is_tax_unit_head = (i == heads[0])
        spouses = [i for i, p in enumerate(people) if p.is_tax_unit_spouse and i != heads[0]]
        for i, p in enumerate(people):
            p.is_tax_unit_spouse = bool(spouses) and i == spouses[0]

    if unknown_status:
        # Read by the risk agent: these members were gated conservatively, not decided.
        citations["immigration_unknown_members"] = ",".join(unknown_status)

    return engine.Household(
        case_id=case_id,
        state=state,
        members=people,
        rent=_num(facts.rent_monthly),
        utility_expense=_num(facts.utilities_monthly),
        citations=citations,
    )


_TOL = 1.0  # one dollar: annual amounts are monthly cents times 12 (32000.04 vs 32000)


def facts_match(facts: HouseholdFacts, household: engine.Household) -> list[str]:
    """Mismatches between the gathered facts and a known household. Empty list = exact."""
    got = to_household(facts, household.case_id)
    out: list[str] = []

    def near(a: float, b: float) -> bool:
        return abs(float(a) - float(b)) <= _TOL

    if got.state != household.state:
        out.append(f"state: got {got.state!r}, want {household.state!r}")
    if not near(got.rent, household.rent):
        out.append(f"rent: got {got.rent}, want {household.rent}")
    if not near(got.utility_expense, household.utility_expense):
        out.append(f"utilities: got {got.utility_expense}, want {household.utility_expense}")
    if len(got.members) != len(household.members):
        out.append(f"members: got {len(got.members)}, want {len(household.members)}")
        return out

    numeric = ["age", "employment_income", "social_security_retirement",
               "other_medical_expenses", "medical_expense_health_insurance_premiums"]
    boolean = ["is_pregnant", "is_breastfeeding", "is_disabled",
               "is_tax_unit_head", "is_tax_unit_spouse"]
    for i, (g, w) in enumerate(zip(got.members, household.members)):
        for field in numeric:
            a, b = getattr(g, field), getattr(w, field)
            if not near(a, b):
                out.append(f"members[{i}].{field}: got {a}, want {b}")
        for field in boolean:
            a, b = getattr(g, field), getattr(w, field)
            if bool(a) != bool(b):
                out.append(f"members[{i}].{field}: got {a}, want {b}")
        if g.immigration_status != w.immigration_status:
            out.append(f"members[{i}].immigration_status: "
                       f"got {g.immigration_status}, want {w.immigration_status}")
    return out
