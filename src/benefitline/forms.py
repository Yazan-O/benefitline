"""Deterministic filing plans. No model runs here.

Every field label in this file is copied verbatim from a file in `data/forms/`, and every
`form_url` is a URL printed in one of those files (and, for SNAP, SoonerCare and Schedule
EIC, in `data/SOURCES.md` as well). Where a form file carries no field labels the plan
carries no fields: SoonerCare's portal is behind a login and Oklahoma WIC publishes a
document list rather than an application, so those two plans are documents plus a route.
Nothing is invented to fill the hole.

Values come from `HouseholdFacts`; each one carries the `source_msg_id` of the family
message it was read from. A field whose fact is missing is omitted, never guessed.
"""
from __future__ import annotations

import re
from typing import Optional

from .deadlines import deadlines_for, load_rules
from .schemas import CaseRecord, EngineResult, Fact, FilingPlan, FormField, HouseholdFacts

PROGRAMS = ("snap", "medicaid", "wic", "eitc")

# --- form identities, all from data/forms/*.md -----------------------------------------

SNAP_FORM_URL = (
    "https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/"
    "okdhsformresults/08mp001e.pdf"
)
SNAP_FORM_002E = (
    "https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/"
    "okdhsformresults/08mp002e.pdf"
)
SNAP_FORM_003E = (
    "https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/"
    "okdhsformresults/08mp003e.pdf"
)
MEDICAID_PORTAL = "https://www.apply.okhca.org/"
WIC_ENROLLMENT = (
    "https://osdhcfhs.az1.qualtrics.com/jfe/form/SV_b8vNxstcQGy9YzA?Source=Website"
)
EITC_FORM_URL = "https://www.irs.gov/pub/irs-pdf/f1040sei.pdf"

FORM_NAMES = {
    "snap": "OKDHS Request for Benefits packet: 08MP001E, 08MP002E, 08MP003E",
    "medicaid": "SoonerCare online application (Oklahoma Health Care Authority portal)",
    "wic": "Oklahoma WIC enrollment request (OSDH)",
    "eitc": "Schedule EIC (Form 1040) 2025 - Earned Income Credit - Qualifying Child Information",
}
FORM_URLS = {
    "snap": SNAP_FORM_URL,
    "medicaid": MEDICAID_PORTAL,
    "wic": WIC_ENROLLMENT,
    "eitc": EITC_FORM_URL,
}

SUBMIT_ROUTES = {
    "snap": (
        "Online at https://www.okdhslive.org/ , or, as form 08MP001E page 9 puts it, "
        '"Please give this form to the receptionist or fax or mail it to an OKDHS office." '
        "Office locator https://oklahoma.gov/okdhs/contact-us.html ; "
        "upload proofs at https://changerequest.dhs.ok.gov/UploadDocuments . "
        f"Companion forms: {SNAP_FORM_002E} and {SNAP_FORM_003E}"
    ),
    "medicaid": (
        f"Apply online at {MEDICAID_PORTAL} (entry page "
        "https://www.apply.okhca.org/Site/Rights.aspx). SoonerCare Helpline 1-800-987-7767. "
        "OHCA's mail-in PDF is the expired federal CMS Marketplace form; do not use it for "
        "an Oklahoma applicant."
    ),
    "wic": (
        f"Request enrollment at the OSDH WIC tool {WIC_ENROLLMENT} , which also takes uploads "
        'of "each applicant\'s proofs of identification, residency, and income". Clinic list: '
        "https://oklahoma.gov/content/dam/ok/en/health/health2/aem-documents/family-health/wic/"
        "wic-clinic-sites.pdf"
    ),
    "eitc": (
        '"Complete and attach to Form 1040 or 1040-SR only if you have a qualifying child." '
        "About page https://www.irs.gov/forms-pubs/about-schedule-eic-form-1040 . "
        "The Oklahoma EITC (ok_eitc) is claimed on the Oklahoma return from the same federal "
        "figures; the engine estimates it as a separate line and it needs no separate filing here."
    ),
}

# --- documents, verbatim from the forms files ------------------------------------------

DOCUMENTS = {
    # 08MP001E, "What You Will Need to Bring to Your Interview"
    "snap": [
        "proof of identity, such as driver license or school identification;",
        "Social Security number or card for everyone who wants benefits. If you are only "
        "applying for child care benefits, Social Security numbers are not required;",
        "proof of citizenship for everyone who wants benefits;",
        "proof of legal status for anyone who is not a U.S. citizen and wants benefits;",
        "proof of income for everyone living with you, such as pay stubs or award letters;",
        "proof of all resources, such as bank accounts, car titles, or land; and",
        "proof of your need for child care, such as your work or school schedule, and the name "
        "of the place you want to use to care for your child.",
    ],
    # OHCA "Important Information Before Starting" / "Before you start, you need to have the
    # following information available:"
    "medicaid": [
        "You and your spouse's taxable income.",
        "Social Security numbers and birthdates of people in your home.",
        "Current or recent health insurance information.",
        "Identity and citizenship information, or alien registration information.",
        "Income information including employer name, address and phone number, of all household "
        "members who are employed.",
        "Amount of money received from other types of income.",
        "Expected date of delivery and number of babies of any pregnant household member.",
        "Current health insurance information for all household members with health insurance "
        "including company name, policy or group number, type of coverage, effective date, "
        "policy holder's name and ID.",
    ],
    # ODH No. P-692 (rev. 1/30/09), the only itemized OSDH list; categories match the 2026 page.
    "wic": [
        "Identification: Birth certificate",
        "Identification: Driver's license/Photo ID",
        "Identification: Medicaid document showing current eligibility",
        "Home Address: Recent utility bill (tele., water, gas, elec., or cable)",
        "Home Address: Rent or mortgage receipts for lodging/housing",
        "Income: Current pay stubs (two from last 90 days per person)",
        "Income: SNAP/food stamp letter showing current eligibility",
        "Please bring infant's/child's immunization record to scheduled certification visit.",
    ],
    # Schedule EIC page 1 "Before you begin" and page 2 CAUTION.
    "eitc": [
        "Be sure the child's name on line 1 and social security number (SSN) on line 2 agree "
        "with the child's social security card.",
        "The IRS may ask you for documents to show you lived with each qualifying child. "
        "Documents you might want to keep for this purpose include school and childcare records "
        "and other records that show your child's address.",
    ],
}

# The only label a model is allowed to write into: the one narrative prompt on 08MP002E that
# stands on its own. "If yes, explain below:" is deliberately excluded - on the printed form it
# is the tail of a Yes/No resources question, and nothing in the transcript answers that
# question, so a model filling it writes the right words under the wrong heading. The other
# three programs publish no narrative field, so their list is empty.
FREE_TEXT = {
    "snap": [
        "When income is less than expenses, explain below how you are paying your bills:",
    ],
    "medicaid": [],
    "wic": [],
    "eitc": [],
}


def free_text_fields(program: str) -> list[str]:
    """Labels the filing model may write. Everything else on a plan is deterministic."""
    return list(FREE_TEXT.get(program, []))


# --- value helpers ----------------------------------------------------------------------


def _money(fact: Fact, period: str = "per month") -> str:
    try:
        return f"${float(fact.value):,.2f} {period}".strip()
    except (TypeError, ValueError):
        return str(fact.value)


def _field(label: str, value: str, fact: Fact) -> FormField:
    return FormField(form_field=label, value=value, source_msg_id=fact.source_msg_id)


def _name_parts(fact: Fact) -> list[FormField]:
    parts = str(fact.value).split()
    if not parts:
        return []
    out = [_field("First name", parts[0], fact)]
    if len(parts) > 1:
        out.append(_field("Last name", " ".join(parts[1:]), fact))
    return out


def _is_child(member) -> bool:
    if member.relation is not None and str(member.relation.value).lower() == "child":
        return True
    if member.age is not None:
        try:
            return int(member.age.value) < 19
        except (TypeError, ValueError):
            return False
    return False


# --- per-program field builders ----------------------------------------------------------


def _snap_fields(facts: HouseholdFacts) -> list[FormField]:
    out: list[FormField] = []
    if facts.contact_name is not None:
        out += _name_parts(facts.contact_name)
        out.append(
            _field(
                "Who do you want to choose as head of household?",
                str(facts.contact_name.value),
                facts.contact_name,
            )
        )
    if facts.address is not None:
        out.append(
            _field(
                "Mailing address, street or PO Box",
                str(facts.address.value),
                facts.address,
            )
        )
    if facts.state is not None:
        out.append(_field("State", str(facts.state.value), facts.state))
    if facts.contact_phone is not None:
        out.append(
            _field(
                "Phone number where you can be reached",
                str(facts.contact_phone.value),
                facts.contact_phone,
            )
        )

    for member in facts.members:
        if member.immigration_status is not None:
            citizen = str(member.immigration_status.value).upper() == "CITIZEN"
            out.append(
                _field("U.S. Citizen?", "Yes" if citizen else "No", member.immigration_status)
            )
        if member.relation is not None:
            out.append(
                _field(
                    "Relationship to head of household",
                    str(member.relation.value),
                    member.relation,
                )
            )

    earners = [m for m in facts.members if m.employment_income_monthly is not None]
    for member in earners:
        fact = member.employment_income_monthly
        out.append(
            _field("Type of income", "money you get from working for someone else", fact)
        )
        out.append(_field("How often received?", "Monthly", fact))
        out.append(_field("Amount before taxes", _money(fact), fact))
    for member in facts.members:
        if member.social_security_monthly is not None:
            fact = member.social_security_monthly
            out.append(_field("Type of income", "Social Security", fact))
            out.append(_field("How often received?", "Monthly", fact))
            out.append(_field("Amount before taxes", _money(fact), fact))
    if len(earners) == 1:
        fact = earners[0].employment_income_monthly
        out.append(
            _field(
                "How much money did you get or will you get this month from working "
                "(total amount before taxes?) $",
                _money(fact),
                fact,
            )
        )

    if facts.rent_monthly is not None:
        out.append(
            _field(
                "Check the box that shows how you pay for housing:", "Rent", facts.rent_monthly
            )
        )
        out.append(_field("Rent or mortgage amount", _money(facts.rent_monthly), facts.rent_monthly))
        out.append(
            _field(
                "How much do you pay for your rent, mortgage, or, if homeless, for sleeping "
                "accommodations? $",
                _money(facts.rent_monthly),
                facts.rent_monthly,
            )
        )
    if facts.utilities_monthly is not None:
        out.append(
            _field("Total amount:", _money(facts.utilities_monthly), facts.utilities_monthly)
        )
        out.append(
            _field(
                "Do you pay the heating or cooling bill where you live?",
                "Yes",
                facts.utilities_monthly,
            )
        )

    for member in facts.members:
        for fact, kind in (
            (member.medical_expenses_monthly, "Medical costs not paid by insurance"),
            (member.health_premiums_monthly, "health insurance premiums"),
        ):
            if fact is not None:
                out.append(_field("Type of Expense", kind, fact))
                out.append(_field("Monthly Expense", _money(fact), fact))
    return out


def _eitc_fields(facts: HouseholdFacts) -> list[FormField]:
    out: list[FormField] = []
    if facts.contact_name is not None:
        out.append(
            _field(
                "Name(s) shown on return", str(facts.contact_name.value), facts.contact_name
            )
        )
    for member in facts.members:
        if _is_child(member) and member.relation is not None:
            out.append(
                _field(
                    "Child's relationship to you",
                    str(member.relation.value),
                    member.relation,
                )
            )
    return out


FIELD_BUILDERS = {
    "snap": _snap_fields,
    "medicaid": lambda facts: [],   # portal is behind a login; OHCA publishes no field labels
    "wic": lambda facts: [],        # OSDH publishes documents and a request tool, no form fields
    "eitc": _eitc_fields,
}


# --- deadlines ----------------------------------------------------------------------------


def _normalized_rules() -> list[dict]:
    """The rules as `deadlines.load_rules` now hands them over: program key lowercased and
    the "30-calendar days" spelling respelled so the period parser sees it. Kept as a name
    because the plan builder and the review suite both call it."""
    return load_rules()


def _pick_deadline(case: CaseRecord, program: str) -> tuple[Optional[str], str]:
    """The deadline this plan should show, and the sentence plus URL of the rule behind it."""
    try:
        items = deadlines_for(case, rules=_normalized_rules())
    except FileNotFoundError:
        return None, ""
    mine = [d for d in items if d["program"] == program]
    if not mine:
        return None, ""

    def rank(item: dict) -> tuple[int, int, str]:
        """A dated rule first; the family's own recert date beats the processing standard."""
        event = item["event"].lower()
        if event.startswith("recertification"):
            order = 0
        elif "application processing" in event:
            order = 1
        elif "renewal" in event:
            order = 2
        else:
            order = 3
        return (0 if item["due"] else 1, order, item["due"] or "")

    best = sorted(mine, key=rank)[0]
    rule = f"{best['event']}: {best['quote']}".strip()
    if best["rule_url"]:
        rule = f"{rule} {best['rule_url']}"
    return (best["due"] or None), rule


# --- the entry point ------------------------------------------------------------------------


def plan_filing(
    program: str,
    facts: HouseholdFacts,
    engine_result: EngineResult,
    case: Optional[CaseRecord] = None,
) -> FilingPlan:
    """A filled filing plan for one program. Deterministic: same facts, same plan."""
    key = program.lower()
    if key == "ok_eitc":
        key = "eitc"
    if key not in PROGRAMS:
        raise ValueError(
            f"no form file for program {program!r}; data/forms covers {', '.join(PROGRAMS)}"
        )

    deadline, deadline_rule = (None, "")
    if case is not None:
        deadline, deadline_rule = _pick_deadline(case, key)

    submit = SUBMIT_ROUTES[key]
    if key == "eitc" and engine_result is not None:
        ok = next((p for p in engine_result.programs if p.key == "ok_eitc"), None)
        if ok is not None and ok.eligible:
            submit = (
                f"{submit} The engine puts the Oklahoma EITC line at "
                f"{ok.amount} {ok.unit or ''}".strip()
                + "; the agency decides the final figure."
            )

    return FilingPlan(
        program=key,
        form_name=FORM_NAMES[key],
        form_url=FORM_URLS[key],
        fields=FIELD_BUILDERS[key](facts),
        documents_needed=list(DOCUMENTS[key]),
        deadline=deadline,
        deadline_rule=deadline_rule,
        submit_route=submit,
    )
