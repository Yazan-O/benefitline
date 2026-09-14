"""Deterministic eligibility engine. Wraps policyengine-us; no model touches this file.

Every amount returned here comes from PolicyEngine's rule formulas. Each program result
carries the statute or regulation reference that PolicyEngine attaches to its variable,
so the UI can show the rule it used under every number.
"""
from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field, asdict
from typing import Any

# policyengine_us builds the whole tax-benefit system on import (about 40 s measured); importing it
# lazily lets the AgentCore container answer /ping right away and pay that cost on the first compute.
_pe: dict[str, Any] = {}


def _policyengine() -> tuple[Any, Any]:
    if not _pe:
        from policyengine_us import Simulation
        from policyengine_us.system import system
        _pe["Simulation"], _pe["system"] = Simulation, system
    return _pe["Simulation"], _pe["system"]

ENGINE = "policyengine-us"
ENGINE_VERSION = importlib.metadata.version("policyengine-us")
DEFAULT_YEAR = 2026

# PolicyEngine variable name -> how Benefitline reports it.
# period "month": monthly values; "year": annual values (tax credits, Medicaid flags).
PROGRAMS = {
    "snap": dict(label="SNAP (food)", var="snap", entity="spm_unit", period="month",
                 eligible_var="is_snap_eligible"),
    "wic": dict(label="WIC (women, infants, children)", var="wic", entity="person", period="month",
                eligible_var="is_wic_eligible",
                note="Conditional: a WIC clinic must find nutritional risk (7 CFR 246.7(e)); the engine assumes it."),
    "medicaid": dict(label="Medicaid (SoonerCare)", var="is_medicaid_eligible", entity="person",
                     period="year", eligible_var="is_medicaid_eligible", boolean=True),
    "tanf": dict(label="TANF (cash)", var="ok_tanf", entity="spm_unit", period="month",
                 eligible_var="ok_tanf_eligible"),
    "eitc": dict(label="Earned Income Tax Credit (federal)", var="eitc", entity="tax_unit", period="year",
                 eligible_var="eitc_eligible"),
    # Oklahoma EITC = 5% of the federal EITC recomputed with frozen tax-year-2020 parameters, prorated by
    # OK AGI share (ok_eitc.py docstring). Gated on eitc_eligible because the OK variable does not check SSNs.
    "ok_eitc": dict(label="Oklahoma EITC", var="ok_eitc", entity="tax_unit", period="year",
                    eligible_var="eitc_eligible", zero_when_ineligible=True),
    "ctc": dict(label="Child Tax Credit (federal)", var="ctc", entity="tax_unit", period="year",
                eligible_var="filer_meets_ctc_identification_requirements", require_amount=True),
    "refundable_ctc": dict(label="Child Tax Credit, refundable part", var="refundable_ctc",
                           entity="tax_unit", period="year",
                           eligible_var="filer_meets_ctc_identification_requirements", require_amount=True),
    "ssi": dict(label="SSI", var="ssi", entity="person", period="year"),
}

# Programs the gallery mentions that PolicyEngine does not model for Oklahoma.
# These are flagged for the agency, never computed by a model; no amount is shown.
NOT_MODELED = {
    "liheap": dict(label="LIHEAP (energy assistance)",
                   note="policyengine-us models LIHEAP only for DC and Riverside County, CA. "
                        "Benefitline flags LIHEAP for Oklahoma DHS screening; no amount is shown.",
                   agency_url="https://oklahoma.gov/okdhs/services/liheap.html"),
}


@dataclass
class Person:
    age: int
    employment_income: float = 0.0            # annual, USD
    social_security_retirement: float = 0.0   # annual, USD
    is_pregnant: bool = False
    is_breastfeeding: bool = False
    immigration_status: str = "CITIZEN"       # PolicyEngine ImmigrationStatus enum name
    years_since_us_entry: int | None = None
    other_medical_expenses: float = 0.0       # annual out-of-pocket, USD
    medical_expense_health_insurance_premiums: float = 0.0  # annual, USD
    is_tax_unit_head: bool = False
    is_tax_unit_spouse: bool = False
    is_disabled: bool = False
    weekly_hours_worked: float | None = None  # SNAP work rule (ABAWD) input; None = not collected
    # PolicyEngine SSN card type (CITIZEN, NON_CITIZEN_VALID_EAD, OTHER_NON_CITIZEN, NONE). Separate from
    # immigration_status: an LPR has a valid SSN. None -> "NONE" only for UNDOCUMENTED, else engine default.
    ssn_card_type: str | None = None


@dataclass
class Household:
    case_id: str
    state: str = "OK"
    members: list[Person] = field(default_factory=list)
    rent: float = 0.0             # monthly, USD
    utility_expense: float = 0.0  # monthly, USD
    year: int = DEFAULT_YEAR
    # Fact provenance: message id each fact came from (filled by intake; the engine ignores it)
    citations: dict[str, str] = field(default_factory=dict)


class EngineInputError(ValueError):
    """A household the engine cannot price; the message names the missing or conflicting fact."""


def validate(h: Household) -> None:
    if not h.members:
        raise EngineInputError(f"{h.case_id}: no household members; ask who lives in the home")
    heads = sum(p.is_tax_unit_head for p in h.members)
    spouses = sum(p.is_tax_unit_spouse for p in h.members)
    if heads != 1:
        raise EngineInputError(f"{h.case_id}: exactly one tax-unit head required, got {heads}")
    if spouses > 1:
        raise EngineInputError(f"{h.case_id}: at most one spouse, got {spouses}")
    if h.state != "OK":
        raise EngineInputError(f"{h.case_id}: Benefitline v1 prices Oklahoma households only, got {h.state}")
    if h.rent < 0 or h.utility_expense < 0:
        raise EngineInputError(f"{h.case_id}: rent and utilities must be non-negative")


def _situation(h: Household) -> dict[str, Any]:
    y = str(h.year)
    people, names = {}, []
    for i, p in enumerate(h.members):
        n = f"p{i}"
        names.append(n)
        d = {
            "age": {y: p.age},
            "employment_income": {y: p.employment_income},
            "social_security_retirement": {y: p.social_security_retirement},
            "is_pregnant": {y: p.is_pregnant},
            "is_breastfeeding": {y: p.is_breastfeeding},
            "immigration_status": {y: p.immigration_status},
            "other_medical_expenses": {y: p.other_medical_expenses},
            "medical_expense_health_insurance_premiums": {y: p.medical_expense_health_insurance_premiums},
            "is_disabled": {y: p.is_disabled},
        }
        if p.years_since_us_entry is not None:
            d["years_since_us_entry"] = {y: p.years_since_us_entry}
        if p.weekly_hours_worked is not None:
            d["weekly_hours_worked_before_lsr"] = {y: p.weekly_hours_worked}
        ssn = p.ssn_card_type or ("NONE" if p.immigration_status == "UNDOCUMENTED" else None)
        if ssn is not None:
            d["ssn_card_type"] = {y: ssn}
        if p.is_tax_unit_head:
            d["is_tax_unit_head"] = {y: True}
            d["rent"] = {y: h.rent * 12}  # rent is a person-level input in PolicyEngine; paid by the head
        if p.is_tax_unit_spouse:
            d["is_tax_unit_spouse"] = {y: True}
        people[n] = d
    adults = [n for n, p in zip(names, h.members) if p.is_tax_unit_head or p.is_tax_unit_spouse]
    return {
        "people": people,
        "families": {"f": {"members": names}},
        "marital_units": {"m": {"members": adults or names[:1]}},
        "tax_units": {"t": {"members": names}},
        "spm_units": {"s": {"members": names,
                            # Both set on purpose: Oklahoma applies the standard utility allowance whenever a
                            # heating or cooling cost exists (OK-only; other states would double count).
                            "utility_expense": {y: h.utility_expense * 12},
                            "heating_cooling_expense": {y: h.utility_expense * 12}}},
        "households": {"h": {"members": names, "state_code": {y: h.state}}},
    }


def _refs(var: str) -> list[str]:
    v = _policyengine()[1].variables.get(var)
    if v is None or not v.reference:
        return []
    r = v.reference
    return [r] if isinstance(r, str) else list(r)


def compute(h: Household) -> dict[str, Any]:
    """Run PolicyEngine on one household. Returns per-program eligibility and amounts."""
    validate(h)
    sim = _policyengine()[0](situation=_situation(h))
    y = str(h.year)
    month = f"{h.year}-01"
    out: dict[str, Any] = {
        "case_id": h.case_id, "state": h.state, "year": h.year,
        "engine": ENGINE, "engine_version": ENGINE_VERSION, "programs": {},
    }
    for key, spec in PROGRAMS.items():
        period = month if spec["period"] == "month" else y
        vals = sim.calculate(spec["var"], period)
        if spec.get("boolean"):
            per_person = [bool(v) for v in vals]
            res = dict(eligible=any(per_person),
                       eligible_members=[i for i, e in enumerate(per_person) if e],
                       amount=None, unit=None)
        else:
            total = float(sum(vals))
            res = dict(amount=round(total, 2),
                       unit="USD/month" if spec["period"] == "month" else "USD/year")
            if spec.get("eligible_var"):
                ev = sim.calculate(spec["eligible_var"], period)
                res["eligible"] = bool(any(ev))
                if spec["entity"] == "person":
                    res["eligible_members"] = [i for i, e in enumerate(ev) if e]
                if spec.get("zero_when_ineligible") and not res["eligible"]:
                    res["amount"] = 0.0
                if spec.get("require_amount"):  # the flag alone (e.g. SSN check) is not eligibility
                    res["eligible"] = res["eligible"] and total > 0
            else:
                res["eligible"] = total > 0  # SSI: no separate eligibility flag exposed
        if spec.get("note"):
            res["note"] = spec["note"]
            if spec["entity"] == "person":
                res["per_member"] = [round(float(v), 2) for v in vals]
        res["label"] = spec["label"]
        res["variable"] = spec["var"]
        res["rules"] = _refs(spec["var"]) or _refs(spec.get("eligible_var", ""))
        out["programs"][key] = res

    # The pieces of the SNAP number a coordinator asks about.
    snap_detail, snap_detail_errors = {}, {}
    for v in ["snap_gross_income", "snap_net_income", "snap_max_allotment", "snap_standard_deduction",
              "snap_earned_income_deduction", "snap_excess_shelter_expense_deduction",
              "snap_excess_medical_expense_deduction", "snap_utility_allowance",
              "snap_expected_contribution", "meets_snap_gross_income_test", "meets_snap_net_income_test"]:
        try:
            val = sim.calculate(v, month)
            snap_detail[v] = bool(val[0]) if val.dtype == bool else round(float(val[0]), 2)
        except Exception as e:  # a missing variable is reported, never hidden
            snap_detail_errors[v] = f"{type(e).__name__}: {e}"
    out["snap_detail"] = snap_detail
    out["snap_detail_errors"] = snap_detail_errors
    # Per-member immigration gates: the escalation scene shows which members each program covers.
    out["members"] = [
        dict(index=i, age=p.age, immigration_status=p.immigration_status,
             weekly_hours_worked=p.weekly_hours_worked,
             snap_excluded_member=bool(sim.calculate("is_snap_excluded_member", month)[i]),
             snap_meets_work_rule=bool(sim.calculate("meets_snap_work_requirements_person", month)[i]),
             snap_immigration_eligible=bool(sim.calculate("is_snap_immigration_status_eligible", month)[i]),
             medicaid_immigration_eligible=bool(sim.calculate("is_medicaid_immigration_status_eligible", y)[i]))
        for i, p in enumerate(h.members)
    ]
    out["not_modeled"] = {k: dict(**v, eligible=None, amount=None) for k, v in NOT_MODELED.items()}
    # N3: a member the work rule excludes while hours were never collected is a missing fact, not a $0.
    for m, p in zip(out["members"], h.members):
        if (m["snap_excluded_member"] and not m["snap_meets_work_rule"] and p.weekly_hours_worked is None
                and not p.is_disabled and 18 <= p.age < 60):
            raise EngineInputError(f"{h.case_id}: member {m['index']} (age {p.age}) falls under the SNAP work rule; "
                                   "ask weekly hours worked before pricing SNAP")
    # N1: with no valid SSN on the filer, the CTC figure is the other-dependent credit and assumes an ITIN filing.
    if any((p.ssn_card_type or ("NONE" if p.immigration_status == "UNDOCUMENTED" else "")) == "NONE"
           for p in h.members if p.is_tax_unit_head or p.is_tax_unit_spouse):
        for k in ("ctc", "refundable_ctc"):
            out["programs"][k]["note"] = ("Filer has no valid SSN: this is the $500-per-dependent other-dependent "
                                          "credit (26 USC 24(h)(4)), available only if the filer files with an ITIN.")
    return out


def household_from_dict(d: dict[str, Any]) -> Household:
    members = [Person(**m) for m in d["members"]]
    rest = {k: v for k, v in d.items() if k != "members"}
    return Household(members=members, **rest)


def household_to_dict(h: Household) -> dict[str, Any]:
    return asdict(h)
