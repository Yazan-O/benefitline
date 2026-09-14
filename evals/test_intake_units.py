"""Unit tests for the deterministic half of intake. No model is called anywhere here."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "gallery"))

from benefitline import triage  # noqa: E402
from benefitline.facts import (  # noqa: E402
    HAS_HOURS_FIELD, facts_match, missing_facts, to_household, validate_facts)
from benefitline.schemas import CaseRecord, Fact, HouseholdFacts, MemberFacts  # noqa: E402
from cases import CASES  # noqa: E402

M = "m1"


def f(value, src: str = M) -> Fact:
    return Fact(value=value, source_msg_id=src, quote="q")


def parse(pending: str, message: str):
    return triage.tier0_parse(pending, message, M)


# --------------------------------------------------------------------------- language

@pytest.mark.parametrize("text,want", [
    ("rent is 950", "en"),
    ("الإيجار ٩٥٠", "ar"),
    ("نعم", "ar"),
    ("mi renta es 950", "en"),      # Spanish stays "en" here; the cheap model labels it later
    ("", "en"),
])
def test_detect_language(text, want):
    assert triage.detect_language(text) == want


# --------------------------------------------------------------------------- tier-0 regexes

@pytest.mark.parametrize("message,want_monthly", [
    ("I make 14 an hour, 30 hours a week", 14 * 30 * 52 / 12),
    ("I make 32,000 a year", 32_000 / 12),
    ("19k a year", 19_000 / 12),
    ("$1,200 a month", 1200.0),
    ("about 600 a week", 600 * 52 / 12),
    ("أعمل ١٤ بالساعة، ٣٠ ساعة بالأسبوع", 14 * 30 * 52 / 12),
    ("راتبي ٣٢ ألف بالسنة", 32_000 / 12),
])
def test_income_amounts(message, want_monthly):
    got = parse("income", message)
    assert got is not None, message
    kind, amount, _who = str(got.value).split(":")
    assert kind == "employment"
    assert float(amount) == pytest.approx(want_monthly, abs=0.01)


def test_income_social_security_and_who():
    got = parse("income", "I get 1250 a month from social security")
    assert str(got.value).split(":")[:2] == ["social_security", "1250.0"]
    ar = parse("income", "أستلم ١٢٥٠ شهرياً من الضمان الاجتماعي")
    assert str(ar.value).startswith("social_security:1250.0")
    spouse = parse("income", "my husband brings home 2000 a month")
    assert str(spouse.value).endswith(":spouse")


@pytest.mark.parametrize("pending,message,want", [
    ("rent", "rent is 950", 950.0),
    ("rent", "$1,050", 1050.0),
    ("rent", "الإيجار ٩٥٠", 950.0),
    ("utilities", "lights and gas about 180", 180.0),
    ("utilities", "الكهرباء والغاز حوالي ٢٠٠", 200.0),
    ("medical_expenses", "about 180 a month for my pills", 180.0),
    ("medical_expenses", "no, nothing much", 0.0),
    ("medical_expenses", "لا، لا شيء يذكر", 0.0),
    ("health_premiums", "185 a month for my medicare plan", 185.0),
])
def test_money_fields(pending, message, want):
    got = parse(pending, message)
    assert got is not None and float(got.value) == pytest.approx(want, abs=0.01)


@pytest.mark.parametrize("message,want", [
    ("yes, we are all citizens", "all_citizen"),
    ("نعم، كلنا مقيمون دائمون", "all_citizen"),
    ("ايوه كلنا مواطنين", "all_citizen"),
    ("no", "not_all"),
    ("لا", "not_all"),
    ("the kids were born here, but my husband and i don't have papers", "not_all_kids_citizen"),
    ("الأطفال مولودين هنا، لكن أنا وزوجي بدون أوراق", "not_all_kids_citizen"),
])
def test_immigration_yes_no(message, want):
    got = parse("immigration", message)
    assert got is not None and got.value == want


def test_arabic_no_is_a_whole_word_not_a_substring():
    """"لا" lives inside ordinary words such as "الأولاد"; a substring test would misread it."""
    assert parse("immigration", "نعم، الأولاد مواطنون") .value == "all_citizen"


@pytest.mark.parametrize("message,want", [
    ("I'm 29, and they're 7 and 4", [29, 7, 4]),
    ("7 and 4", [7, 4]),
    ("I'm 31, my wife is 28, and our baby is 2 months old", [31, 28, 0]),
    ("أنا ٣١ وزوجتي ٢٨ والرضيع عمره شهرين", [31, 28, 0]),
    ("أنا ٢٩، وهما ٧ و ٤", [29, 7, 4]),
])
def test_ages(message, want):
    got = parse("ages", message)
    assert got is not None
    assert [int(a) for a in str(got.value).split(",")] == want


@pytest.mark.parametrize("message,want", [
    ("no, nobody", "none"),
    ("لا، لا أحد", "none"),
    ("yes, my wife is breastfeeding our baby", "breastfeeding:spouse"),
    ("نعم، زوجتي ترضع طفلنا", "breastfeeding:spouse"),
    ("i am pregnant", "pregnant:self"),
])
def test_pregnancy(message, want):
    got = parse("pregnancy", message)
    assert got is not None and got.value == want


def test_bare_yes_to_pregnancy_is_not_tier0():
    assert parse("pregnancy", "yes") is None


def test_weekly_hours_parse():
    assert parse("weekly_hours", "about 30 hours a week").value == 30.0
    assert parse("weekly_hours", "35").value == 35.0
    assert parse("weekly_hours", "٢٠ ساعة بالأسبوع").value == 20.0


def test_state_and_size():
    assert parse("state", "Oklahoma").value == "OK"
    assert parse("state", "we live in Tulsa").value == "OK"
    assert parse("state", "أوكلاهوما").value == "OK"
    assert parse("household_size", "3, me and my two kids").value == 3
    assert parse("household_size", "٤ أشخاص").value == 4


def test_route_tiers():
    case = CaseRecord(case_id="c", session_id="s" * 33)
    case.transcript = [{"id": "m1", "role": "agent", "text": "How much is your rent?",
                        "tier": 0, "asks": "rent"}]
    assert triage.route(case, "rent is 950") == 0          # tier 0 reads it
    assert triage.route(case, "my brother pays it") == 1   # short, unreadable -> Haiku
    assert triage.route(case, "my brother covers the rent every month and I pay him back "
                              "whenever I can") == 2       # long -> Sonnet
    empty = CaseRecord(case_id="c", session_id="s" * 33)
    assert triage.route(empty, "hi, i need help with food for me and my two kids") == 2


def test_scan_message_reads_state_program_and_deadline():
    scan = triage.scan_message(
        "we're in tulsa, we already get snap and my renewal is due in 12 days", "m1")
    assert scan["state"].value == "OK"
    assert [p.value for p in scan["already_receives"]] == ["snap"]
    assert scan["recert_due_days"] == 12


# --------------------------------------------------------------------------- missing_facts

def _facts_for(household, upto: str = "") -> HouseholdFacts:
    """Build HouseholdFacts equal to a gallery Household, all cited to m1."""
    hf = HouseholdFacts(state=f(household.state),
                        household_size=f(len(household.members)),
                        rent_monthly=f(household.rent),
                        utilities_monthly=f(household.utility_expense))
    for i, p in enumerate(household.members):
        rel = "self" if p.is_tax_unit_head else ("spouse" if p.is_tax_unit_spouse
                                                 else ("child" if p.age < 18 else "other"))
        m = MemberFacts(age=f(p.age), relation=f(rel),
                        immigration_status=f(p.immigration_status))
        if p.employment_income:
            m.employment_income_monthly = f(p.employment_income / 12)
        if p.social_security_retirement:
            m.social_security_monthly = f(p.social_security_retirement / 12)
        if 15 <= p.age <= 50:
            m.is_pregnant = f(p.is_pregnant)
            m.is_breastfeeding = f(p.is_breastfeeding)
        if p.age >= 60 or p.is_disabled:
            m.medical_expenses_monthly = f(p.other_medical_expenses / 12)
            m.health_premiums_monthly = f(p.medical_expense_health_insurance_premiums / 12)
        hf.members.append(m)
    _ = upto
    return hf


def test_missing_facts_order_from_empty():
    hf = HouseholdFacts()
    # Size and ages are one question, so an unknown size and unknown ages ask the same thing.
    assert missing_facts(hf) == ["state", "ages"]
    hf.state = f("OK")
    assert missing_facts(hf) == ["ages"]
    hf.household_size = f(3)
    assert missing_facts(hf) == ["ages"]
    hf.members = [MemberFacts(age=f(29)), MemberFacts(age=f(7)), MemberFacts(age=f(4))]
    assert missing_facts(hf) == ["income", "rent", "utilities", "pregnancy", "immigration"]


@pytest.mark.skipif(not HAS_HOURS_FIELD,
                    reason="schemas.MemberFacts has no weekly_hours_worked field yet")
def test_weekly_hours_asked_only_for_a_childless_working_age_adult():
    # A childless adult 18-59 with earnings: SNAP's work rule needs the hours.
    adult = HouseholdFacts(state=f("OK"), household_size=f(1), rent_monthly=f(600),
                           utilities_monthly=f(120),
                           members=[MemberFacts(age=f(40), relation=f("self"),
                                                employment_income_monthly=f(900))])
    assert missing_facts(adult)[0] == "weekly_hours"
    # The same home with a child in it is not subject to the rule, so it is never asked.
    with_child = adult.model_copy(deep=True)
    with_child.household_size = f(2)
    with_child.members.append(MemberFacts(age=f(6), relation=f("child")))
    assert "weekly_hours" not in missing_facts(with_child)


def test_missing_facts_conditionals_only_when_relevant():
    # No one 15-50 and no one 60+: neither pregnancy nor medical costs are asked.
    hf = HouseholdFacts(state=f("OK"), household_size=f(2), rent_monthly=f(800),
                        utilities_monthly=f(160),
                        members=[MemberFacts(age=f(55), employment_income_monthly=f(1000)),
                                 MemberFacts(age=f(12))])
    assert missing_facts(hf) == ["immigration"]

    # A senior is present: medical costs, then premiums only when the costs are above zero.
    senior = HouseholdFacts(state=f("OK"), household_size=f(1), rent_monthly=f(650),
                            utilities_monthly=f(140),
                            members=[MemberFacts(age=f(71), social_security_monthly=f(1250))])
    assert missing_facts(senior) == ["medical_expenses", "immigration"]
    senior.members[0].medical_expenses_monthly = f(180)
    assert missing_facts(senior) == ["health_premiums", "immigration"]
    senior.members[0].health_premiums_monthly = f(185)
    assert missing_facts(senior) == ["immigration"]

    zero = HouseholdFacts(state=f("OK"), household_size=f(1), rent_monthly=f(650),
                          utilities_monthly=f(140),
                          members=[MemberFacts(age=f(71), social_security_monthly=f(1250),
                                               medical_expenses_monthly=f(0))])
    assert missing_facts(zero) == ["immigration"]  # nothing out of pocket, so no premium question


def test_missing_facts_empty_when_engine_can_run():
    for household in CASES:
        assert missing_facts(_facts_for(household)) == [], household.case_id


# --------------------------------------------------------------------------- validate_facts

def test_validate_facts_drops_uncited_fact(caplog):
    transcript = [
        {"id": "m1", "role": "family", "text": "rent is 950"},
        {"id": "m2", "role": "agent", "text": "How much are utilities?"},
    ]
    hf = HouseholdFacts(state=f("OK", "m1"),
                        rent_monthly=f(950, "m1"),
                        utilities_monthly=f(180, "m2"),       # cited to the AGENT's message
                        household_size=f(3, "m99"),           # cited to nothing at all
                        members=[MemberFacts(age=f(29, "m1"),
                                             employment_income_monthly=f(1820, "m2"))])
    with caplog.at_level("WARNING"):
        clean = validate_facts(hf, transcript)
    assert clean.rent_monthly is not None and clean.state is not None
    assert clean.utilities_monthly is None
    assert clean.household_size is None
    assert clean.members[0].age is not None
    assert clean.members[0].employment_income_monthly is None
    assert "dropped 3 uncited fact" in caplog.text


def test_validate_facts_keeps_everything_when_all_cited():
    transcript = [{"id": "m1", "role": "family", "text": "rent is 950"}]
    hf = HouseholdFacts(rent_monthly=f(950, "m1"))
    assert validate_facts(hf, transcript).rent_monthly is not None


# --------------------------------------------------------------------------- to_household

@pytest.mark.parametrize("household", CASES, ids=[h.case_id for h in CASES])
def test_to_household_round_trip(household):
    got = to_household(_facts_for(household), household.case_id)
    assert facts_match(_facts_for(household), household) == []
    assert got.state == household.state
    assert got.rent == household.rent                       # rent stays monthly
    assert got.utility_expense == household.utility_expense
    for i, (g, w) in enumerate(zip(got.members, household.members)):
        assert g.age == w.age, i
        assert g.employment_income == pytest.approx(w.employment_income, abs=0.01), i
        assert g.social_security_retirement == pytest.approx(
            w.social_security_retirement, abs=0.01), i
        assert g.other_medical_expenses == pytest.approx(w.other_medical_expenses, abs=0.01), i
        assert g.medical_expense_health_insurance_premiums == pytest.approx(
            w.medical_expense_health_insurance_premiums, abs=0.01), i
        assert g.is_tax_unit_head == w.is_tax_unit_head, i
        assert g.is_tax_unit_spouse == w.is_tax_unit_spouse, i
        assert g.immigration_status == w.immigration_status, i


def test_unknown_status_is_gated_conservatively_and_flagged():
    hf = HouseholdFacts(state=f("OK"), household_size=f(2), rent_monthly=f(900),
                        utilities_monthly=f(170),
                        members=[MemberFacts(age=f(38), relation=f("self"),
                                             immigration_status=f("OTHER_OR_UNKNOWN")),
                                 MemberFacts(age=f(9), relation=f("child"),
                                             immigration_status=f("CITIZEN"))])
    h = to_household(hf, "c")
    assert h.members[0].immigration_status == "UNDOCUMENTED"   # conservative engine gate
    assert h.members[1].immigration_status == "CITIZEN"
    assert h.citations["immigration_unknown_members"] == "0"   # the risk agent reads this


def test_head_is_assigned_when_no_relation_was_gathered():
    hf = HouseholdFacts(state=f("OK"), household_size=f(2),
                        members=[MemberFacts(age=f(7)), MemberFacts(age=f(30))])
    h = to_household(hf, "c")
    assert [p.is_tax_unit_head for p in h.members] == [False, True]


def test_facts_match_reports_a_wrong_number():
    household = CASES[0]
    hf = _facts_for(household)
    hf.rent_monthly = f(1000)
    out = facts_match(hf, household)
    assert out == ["rent: got 1000.0, want 950"]


def test_volunteered_disclosure_beats_a_model_guess_about_the_children():
    """Regression: the model cited the same opening message but marked the US-born
    children OTHER_OR_UNKNOWN. The deterministic disclosure has to win."""
    from benefitline.intake import _apply_disclosures

    text = ("hi, we need help with food. my two kids were born here but my husband and i "
            "don't have papers. we live in tulsa.")
    case = CaseRecord(case_id="c", session_id="s-benefitline")
    case.transcript = [{"id": M, "role": "family", "text": text, "tier": 2}]
    case.facts = HouseholdFacts(
        state=f("OK"), household_size=f(4),
        members=[MemberFacts(age=f(a), immigration_status=f(s)) for a, s in
                 [(38, "UNDOCUMENTED"), (35, "UNDOCUMENTED"),
                  (9, "OTHER_OR_UNKNOWN"), (3, "OTHER_OR_UNKNOWN")]])
    _apply_disclosures(case)
    got = [m.immigration_status.value for m in case.facts.members]
    assert got == ["OTHER_OR_UNKNOWN", "OTHER_OR_UNKNOWN", "CITIZEN", "CITIZEN"]
    _apply_disclosures(case)   # idempotent
    assert [m.immigration_status.value for m in case.facts.members] == got


def test_hours_inside_the_income_reply_are_stored():
    """"12 an hour, 25 hours a week" answers the hours question too; a childless adult must
    not spend a seventh question on it. A plain monthly amount stores no hours."""
    from benefitline import triage
    from benefitline.intake import _apply_tier0
    from benefitline.schemas import Fact, HouseholdFacts, MemberFacts

    def run(text):
        facts = HouseholdFacts(members=[MemberFacts(age=Fact(value=30, source_msg_id="m1", quote="30"))])
        _apply_tier0(facts, "income", triage.tier0_parse("income", text, "m5"))
        m = facts.members[0]
        return m.employment_income_monthly.value, (m.weekly_hours_worked.value if m.weekly_hours_worked else None)

    assert run("12 an hour, 25 hours a week") == (1300.0, 25.0)
    assert run("I make 100 a month") == (100.0, None)


def test_own_age_after_an_ageless_opening_seats_no_second_person():
    """The opening left one member with no age; "i'm 30" is that member's age, not a new
    person. The live childless-adult case turned a home of one into spouse plus self."""
    from benefitline import triage
    from benefitline.intake import _apply_scan, _apply_tier0
    from benefitline.schemas import Fact, HouseholdFacts, MemberFacts

    facts = HouseholdFacts(household_size=Fact(value=1, source_msg_id="m1", quote="alone"),
                           members=[MemberFacts()])
    _apply_scan(facts, triage.scan_message("i'm 30", "m3"))
    _apply_tier0(facts, "ages", triage.tier0_parse("ages", "i'm 30", "m3"))
    assert [(m.relation.value, m.age.value) for m in facts.members] == [("self", 30)]
    assert facts.household_size.value == 1
