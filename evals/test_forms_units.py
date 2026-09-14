"""forms.plan_filing, with no model in the loop.

Every label and every document line a plan carries must appear verbatim in the one
`data/forms/*.md` file that matches that plan's form, every filled field must carry the
`source_msg_id` of the message it came from, and a deadline must appear where the sourced
rules produce one.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "gallery"))

from benefitline import forms                                        # noqa: E402
from benefitline.schemas import (                                    # noqa: E402
    CaseRecord, EngineResult, Fact, HouseholdFacts, MemberFacts, ProgramResult,
)

FORM_FILE = {
    "snap": "ok_snap_application_fields.md",
    "medicaid": "soonercare_application_fields.md",
    "wic": "ok_wic_documents.md",
    "eitc": "irs_eitc_fields.md",
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def form_text(program: str) -> str:
    path = os.path.join(ROOT, "data", "forms", FORM_FILE[program])
    with open(path, encoding="utf-8") as fh:
        return _squash(fh.read())


def ground_truth() -> dict:
    with open(os.path.join(ROOT, "gallery", "ground_truth.json"), encoding="utf-8") as fh:
        return json.load(fh)


def engine_result(case_id: str) -> EngineResult:
    """Ground truth -> EngineResult, the same conversion gen_mockdata.py does for the web page."""
    gt = ground_truth()
    c = gt["cases"][case_id]
    members = c["members"]
    programs = []
    for key, p in c["programs"].items():
        em = p.get("eligible_members")
        if em is None:
            em = (
                [m["index"] for m in members if not m.get("snap_excluded_member")]
                if key == "snap" and p.get("eligible")
                else []
            )
        programs.append(
            ProgramResult(
                key=key, label=p["label"], eligible=p.get("eligible"), amount=p.get("amount"),
                unit=p.get("unit"), eligible_members=em, rules=p.get("rules", []),
                note=p.get("note", "") or "",
            )
        )
    for key, nm in (c.get("not_modeled") or {}).items():
        programs.append(
            ProgramResult(
                key=key, label=nm["label"], eligible=nm.get("eligible"), amount=nm.get("amount"),
                unit=nm.get("unit"), eligible_members=[], rules=[], note=nm.get("note", ""),
            )
        )
    return EngineResult(
        engine=gt["engine"], engine_version=gt["engine_version"], year=c["year"],
        state=c["state"], programs=programs, snap_detail=c.get("snap_detail", {}),
        members=members,
    )


def f(value, msg_id, quote=""):
    return Fact(value=value, source_msg_id=msg_id, quote=quote)


def case1_facts() -> HouseholdFacts:
    """Case 1: single mother, two children. 14/hr x 30 h x 52 w = $21,840/yr -> $1,820/mo."""
    return HouseholdFacts(
        state=f("OK", "m1", "I live in Tulsa"),
        household_size=f(3, "m1", "me and my two kids"),
        members=[
            MemberFacts(
                age=f(29, "m2"), relation=f("self", "m1"),
                employment_income_monthly=f(1820.0, "m3", "about $14 an hour, 30 hours a week"),
                immigration_status=f("CITIZEN", "m6"),
            ),
            MemberFacts(age=f(7, "m2", "7 and 4"), relation=f("child", "m2"),
                        immigration_status=f("CITIZEN", "m6")),
            MemberFacts(age=f(4, "m2", "7 and 4"), relation=f("child", "m2"),
                        immigration_status=f("CITIZEN", "m6")),
        ],
        rent_monthly=f(950.0, "m4", "rent is 950"),
        utilities_monthly=f(180.0, "m4", "utilities run about 180"),
        contact_name=f("Maria Alvarez", "m5"),
        contact_phone=f("918-555-0142", "m5"),
        address=f("1420 S Peoria Ave", "m5"),
        language="en",
    )


def case4_facts() -> HouseholdFacts:
    """Case 4: two undocumented parents, two citizen children. $26,000/yr -> $2,166.67/mo."""
    return HouseholdFacts(
        state=f("OK", "m1"),
        household_size=f(4, "m1"),
        members=[
            MemberFacts(
                age=f(38, "m2"), relation=f("self", "m1"),
                employment_income_monthly=f(2166.67, "m3", "about 26 thousand a year"),
                immigration_status=f("UNDOCUMENTED", "m7", "we do not have papers"),
            ),
            MemberFacts(age=f(35, "m2"), relation=f("spouse", "m2"),
                        immigration_status=f("UNDOCUMENTED", "m7")),
            MemberFacts(age=f(9, "m2"), relation=f("child", "m2"),
                        immigration_status=f("CITIZEN", "m2", "both kids were born here")),
            MemberFacts(age=f(3, "m2"), relation=f("child", "m2"),
                        immigration_status=f("CITIZEN", "m2", "both kids were born here")),
        ],
        rent_monthly=f(900.0, "m4"),
        utilities_monthly=f(170.0, "m4"),
        contact_name=f("Ana Reyes", "m5"),
        contact_phone=f("918-555-0177", "m5"),
        address=f("3300 E Admiral Pl", "m5"),
        language="en",
    )


def case5_facts() -> HouseholdFacts:
    return HouseholdFacts(
        state=f("OK", "m1"),
        household_size=f(2, "m1"),
        members=[
            MemberFacts(age=f(44, "m2"), relation=f("self", "m1"),
                        employment_income_monthly=f(1583.33, "m3"),
                        immigration_status=f("CITIZEN", "m2")),
            MemberFacts(age=f(12, "m2"), relation=f("child", "m2"),
                        immigration_status=f("CITIZEN", "m2")),
        ],
        rent_monthly=f(800.0, "m4"),
        utilities_monthly=f(160.0, "m4"),
        already_receives=[f("snap", "m1", "I already get SNAP")],
        recert_due_date=f("2026-10-15", "m6", "my renewal is due October 15"),
        contact_name=f("Dana Whitefeather", "m5"),
        contact_phone=f("918-555-0110", "m5"),
        address=f("55 N Denver Ave", "m5"),
    )


def make_case(case_id: str, facts: HouseholdFacts) -> CaseRecord:
    return CaseRecord(
        case_id=case_id,
        session_id="units",
        gallery_case=case_id,
        facts=facts,
        transcript=[{"id": "m1", "role": "family", "text": "hi", "at": "2026-09-13T14:00:00+00:00"}],
        engine_result=engine_result(case_id),
        updated_at=datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc),
    )


CASES = {
    "case1_single_mother_two_kids": case1_facts,
    "case4_mixed_status": case4_facts,
    "case5_on_snap_recert_due": case5_facts,
}


@pytest.mark.parametrize("case_id", list(CASES))
@pytest.mark.parametrize("program", forms.PROGRAMS)
def test_every_field_carries_a_source_message_id(case_id, program):
    facts = CASES[case_id]()
    case = make_case(case_id, facts)
    plan = forms.plan_filing(program, facts, case.engine_result, case=case)
    assert plan.program == program
    for field in plan.fields:
        assert field.source_msg_id, f"{program}/{field.form_field} has no source_msg_id"
        assert field.value != "", f"{program}/{field.form_field} is empty"


@pytest.mark.parametrize("case_id", list(CASES))
@pytest.mark.parametrize("program", forms.PROGRAMS)
def test_no_invented_labels(case_id, program):
    """Each label is checked against the one form file that matches this plan's form."""
    facts = CASES[case_id]()
    case = make_case(case_id, facts)
    plan = forms.plan_filing(program, facts, case.engine_result, case=case)
    text = form_text(program)
    assert plan.form_url in text.replace(" ", "") or plan.form_url in text
    for field in plan.fields:
        assert _squash(field.form_field) in text, (
            f"{program}: label not printed in {FORM_FILE[program]}: {field.form_field!r}"
        )
    for doc in plan.documents_needed:
        body = doc.split(": ", 1)[1] if program == "wic" and ": " in doc else doc
        assert _squash(body) in text, (
            f"{program}: document line not printed in {FORM_FILE[program]}: {doc!r}"
        )
    for label in forms.free_text_fields(program):
        assert _squash(label) in text


def test_snap_plan_for_case1_reads_the_household():
    facts = case1_facts()
    case = make_case("case1_single_mother_two_kids", facts)
    plan = forms.plan_filing("snap", facts, case.engine_result, case=case)
    by_label = {}
    for field in plan.fields:
        by_label.setdefault(field.form_field, []).append(field)
    assert by_label["First name"][0].value == "Maria"
    assert by_label["Last name"][0].value == "Alvarez"
    assert by_label["Rent or mortgage amount"][0].value == "$950.00 per month"
    assert by_label["Rent or mortgage amount"][0].source_msg_id == "m4"
    assert by_label["Amount before taxes"][0].value == "$1,820.00 per month"
    assert by_label["Amount before taxes"][0].source_msg_id == "m3"
    assert [f.value for f in by_label["U.S. Citizen?"]] == ["Yes", "Yes", "Yes"]
    assert len(plan.documents_needed) == 7


def test_mixed_status_case4_records_what_the_family_said_and_nothing_more():
    facts = case4_facts()
    case = make_case("case4_mixed_status", facts)
    plan = forms.plan_filing("snap", facts, case.engine_result, case=case)
    citizen = [f.value for f in plan.fields if f.form_field == "U.S. Citizen?"]
    assert citizen == ["No", "No", "Yes", "Yes"]
    sources = {f.source_msg_id for f in plan.fields if f.form_field == "U.S. Citizen?"}
    assert sources == {"m7", "m2"}


def test_programs_without_published_field_labels_carry_no_fields():
    facts = case1_facts()
    case = make_case("case1_single_mother_two_kids", facts)
    for program in ("medicaid", "wic"):
        plan = forms.plan_filing(program, facts, case.engine_result, case=case)
        assert plan.fields == []
        assert plan.documents_needed
        assert plan.submit_route
    assert forms.plan_filing(
        "medicaid", facts, case.engine_result, case=case
    ).form_url == "https://www.apply.okhca.org/"


def test_ok_eitc_folds_into_the_eitc_plan():
    facts = case1_facts()
    case = make_case("case1_single_mother_two_kids", facts)
    plan = forms.plan_filing("eitc", facts, case.engine_result, case=case)
    assert plan.program == "eitc"
    assert "Oklahoma EITC" in plan.submit_route
    assert "269.57" in plan.submit_route
    assert forms.plan_filing("ok_eitc", facts, case.engine_result, case=case).program == "eitc"


def test_snap_deadline_comes_from_the_sourced_rules():
    """Case 1 has no recert date, so the deadline must come from data/deadline_rules.md."""
    facts = case1_facts()
    case = make_case("case1_single_mother_two_kids", facts)
    plan = forms.plan_filing("snap", facts, case.engine_result, case=case)
    assert plan.deadline == "2026-10-13"          # filed 2026-09-13 + 30 calendar days
    assert "30-calendar days" in plan.deadline_rule
    assert "http" in plan.deadline_rule


def test_recert_date_drives_the_deadline_for_case5():
    facts = case5_facts()
    case = make_case("case5_on_snap_recert_due", facts)
    plan = forms.plan_filing("snap", facts, case.engine_result, case=case)
    assert plan.deadline == "2026-10-15"
    assert "recertification due" in plan.deadline_rule
    assert "October 15" in plan.deadline_rule


def test_no_case_means_no_guessed_deadline():
    facts = case1_facts()
    plan = forms.plan_filing("snap", facts, engine_result("case1_single_mother_two_kids"))
    assert plan.deadline is None
    assert plan.deadline_rule == ""


def test_unknown_program_is_refused():
    facts = case1_facts()
    with pytest.raises(ValueError):
        forms.plan_filing("liheap", facts, engine_result("case1_single_mother_two_kids"))
