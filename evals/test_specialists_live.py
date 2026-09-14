"""run_specialists against Bedrock. Three real model calls per case.

Run: python -m pytest evals/test_specialists_live.py -q
Credentials come from `.env` in the project root; models run in us-east-1.
"""
import os
import re
import sys

import pytest
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "gallery"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

from benefitline import specialists                                   # noqa: E402
from benefitline.schemas import CaseRecord, Fact, HouseholdFacts, MemberFacts   # noqa: E402
from test_forms_units import case1_facts, case4_facts, engine_result, f  # noqa: E402

pytestmark = pytest.mark.live

ARABIC = re.compile(r"[؀-ۿ]")

TRANSCRIPT = [
    {"id": "m1", "role": "family", "text": "I need help with food", "at": "2026-09-13T14:00:00+00:00"},
    {"id": "m2", "role": "family", "text": "three of us, my two kids", "at": "2026-09-13T14:01:00+00:00"},
    {"id": "m3", "role": "family", "text": "I make about $14 an hour, 30 hours a week",
     "at": "2026-09-13T14:02:00+00:00"},
    {"id": "m4", "role": "family", "text": "rent is 950 and utilities about 180",
     "at": "2026-09-13T14:03:00+00:00"},
    {"id": "m5", "role": "family", "text": "my name and number", "at": "2026-09-13T14:04:00+00:00"},
    {"id": "m6", "role": "family", "text": "we are all citizens", "at": "2026-09-13T14:05:00+00:00"},
]


def case2_facts() -> HouseholdFacts:
    """Case 2: couple with a newborn. $32,000/yr -> $2,666.67/mo."""
    return HouseholdFacts(
        state=f("OK", "m1"),
        household_size=f(3, "m1"),
        members=[
            MemberFacts(age=f(31, "m2"), relation=f("self", "m1"),
                        employment_income_monthly=f(2666.67, "m3"),
                        immigration_status=f("CITIZEN", "m6")),
            MemberFacts(age=f(28, "m2"), relation=f("spouse", "m2"),
                        is_breastfeeding=f(True, "m2"),
                        immigration_status=f("CITIZEN", "m6")),
            MemberFacts(age=f(0, "m2"), relation=f("child", "m2"),
                        immigration_status=f("CITIZEN", "m6")),
        ],
        rent_monthly=f(1050.0, "m4"),
        utilities_monthly=f(200.0, "m4"),
        contact_name=f("Dana Whitfield", "m5"),
        contact_phone=f("918-555-0163", "m5"),
        address=f("2100 E 11th St", "m5"),
        language="en",
    )


def arabic_speaker_facts() -> HouseholdFacts:
    """The same household, written by an Arabic-speaking family. Every gallery script is English;
    language detection stays in the code, so one live case still exercises it."""
    return case2_facts().model_copy(update={"language": "ar"})


def build_case(case_id: str, facts: HouseholdFacts) -> CaseRecord:
    return CaseRecord(
        case_id=case_id,
        session_id="live",
        gallery_case=case_id,
        facts=facts,
        transcript=list(TRANSCRIPT),
        engine_result=engine_result(case_id),
        status="computed",
    )


def dollar_figures_are_the_engine_s(case) -> list[float]:
    return specialists.bad_amounts(case.explanation.message_to_family, case.engine_result)


@pytest.fixture(scope="module")
def case1():
    case = build_case("case1_single_mother_two_kids", case1_facts())
    return specialists.run_specialists(case)


@pytest.fixture(scope="module")
def case2():
    case = build_case("case2_couple_newborn", case2_facts())
    return specialists.run_specialists(case)


@pytest.fixture(scope="module")
def arabic_speaker():
    case = build_case("case2_couple_newborn", arabic_speaker_facts())
    return specialists.run_specialists(case)


@pytest.fixture(scope="module")
def case4():
    case = build_case("case4_mixed_status", case4_facts())
    return specialists.run_specialists(case)


def test_case1_is_filed_with_a_veto_window(case1):
    assert case1.status == "filed"
    assert case1.cards == []
    filings = [e for e in case1.ledger if e.action.startswith("filed ")]
    assert filings, "no filing was written to the ledger"
    for entry in filings:
        assert entry.final_at is not None, f"{entry.action} has no veto window"
        assert entry.final_at > entry.at
    programs = {e.action.split()[1] for e in filings}
    assert {"snap", "medicaid", "wic", "eitc"} == programs


def test_case1_explanation_uses_only_engine_amounts(case1):
    assert case1.explanation is not None
    assert case1.explanation.language == "en"
    assert dollar_figures_are_the_engine_s(case1) == []
    assert specialists._amounts_in(case1.explanation.message_to_family), "no amount was written"
    allowed = specialists._rule_urls(case1.engine_result)
    liheap = specialists._liheap_url(case1.engine_result)      # the one program with no statute row
    if liheap:
        allowed.add(liheap)
    for entry in case1.explanation.per_program:
        assert entry["rule_url"] in allowed


def test_case1_every_filed_field_is_cited(case1):
    assert case1.filing_plans
    for plan in case1.filing_plans:
        assert plan.form_url.startswith("https://")
        for field in plan.fields:
            assert field.source_msg_id, f"{plan.program}/{field.form_field} has no source"


def test_case1_trace_counts_the_tiers(case1):
    assert case1.tier_counts["2"] >= 2      # explainer and filing on Sonnet
    assert case1.tier_counts["1"] >= 1      # risk on Haiku
    traced = [e for e in case1.ledger if e.action.endswith("answered")]
    assert {e.action.split()[0] for e in traced} == {"explainer", "filing", "risk"}


def test_case4_escalates_and_the_children_are_still_filed(case4):
    assert case4.status == "escalated"
    assert case4.cards, "a mixed-status household must produce a Decision Card"
    risk = case4.risk
    assert risk.escalate is True
    assert {"medicaid", "wic", "snap"}.issubset(set(risk.programs_safe_to_file))
    assert any(h.startswith("snap") for h in risk.programs_held)
    assert any(h.startswith("medicaid") for h in risk.programs_held)
    assert "eitc" in risk.programs_held
    filed = {e.action.split()[1] for e in case4.ledger if e.action.startswith("filed ")}
    assert filed == {"snap", "medicaid", "wic"}


def test_case4_never_decides_immigration_eligibility(case4):
    text = " ".join(case4.risk.reasons).lower()
    assert "immigration" in text
    assert case4.risk.question_for_coordinator


def test_case2_answers_in_english(case2):
    assert case2.explanation.language == "en"
    assert not ARABIC.search(case2.explanation.message_to_family), "the family wrote in English"
    assert dollar_figures_are_the_engine_s(case2) == []


def test_an_arabic_speaking_family_is_answered_in_arabic(arabic_speaker):
    assert arabic_speaker.explanation.language == "ar"
    assert ARABIC.search(arabic_speaker.explanation.message_to_family), "no Arabic script in the message"
    assert dollar_figures_are_the_engine_s(arabic_speaker) == []


def test_every_node_can_fail_and_the_case_still_files(monkeypatch):
    """No Bedrock call: all three specialists raise, so only the deterministic paths run."""
    async def boom(self, agent):
        raise RuntimeError("bedrock is down")

    for node in (specialists.ExplainerNode, specialists.FilingNode, specialists.RiskNode):
        monkeypatch.setattr(node, "_answer", boom)
    case = specialists.run_specialists(build_case("case1_single_mother_two_kids", case1_facts()))
    assert case.status == "filed"
    assert case.explanation is not None and case.explanation.message_to_family
    assert case.filing_plans
    fell_back = [e.action for e in case.ledger if e.action.startswith("fallback used: ")]
    assert {a.split(": ")[1] for a in fell_back} == {"explainer", "filing", "risk"}


def test_case2_wic_line_carries_the_engine_note(case2):
    wic = next(p for p in case2.engine_result.programs if p.key == "wic")
    assert wic.note
    lines = {e.get("key"): e.get("one_line", "") for e in case2.explanation.per_program}
    assert "wic" in lines
