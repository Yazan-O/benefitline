"""Shared typed contracts. Every module imports from here; nothing here imports a model.

Rule of the product: a fact enters the engine only with a citation to the message it came from.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ImmigrationStatus = Literal[
    "CITIZEN", "LEGAL_PERMANENT_RESIDENT", "REFUGEE", "ASYLEE", "UNDOCUMENTED", "DACA", "TPS",
    "OTHER_OR_UNKNOWN",
]


class Fact(BaseModel):
    """One gathered value and where it came from."""
    value: str | int | float | bool
    source_msg_id: str = Field(description="id of the family's message this value was read from")
    quote: str = Field(default="", description="the words in that message that carry the value")


class MemberFacts(BaseModel):
    age: Optional[Fact] = None
    employment_income_monthly: Optional[Fact] = None   # USD per month, gross
    social_security_monthly: Optional[Fact] = None     # USD per month
    is_pregnant: Optional[Fact] = None
    is_breastfeeding: Optional[Fact] = None
    immigration_status: Optional[Fact] = None          # value is an ImmigrationStatus name
    medical_expenses_monthly: Optional[Fact] = None    # out-of-pocket, elderly or disabled only
    health_premiums_monthly: Optional[Fact] = None
    is_disabled: Optional[Fact] = None
    weekly_hours_worked: Optional[Fact] = None         # SNAP work rule; asked only for childless adults 18-59
    relation: Optional[Fact] = None                    # "self" | "spouse" | "child" | "other"


class HouseholdFacts(BaseModel):
    """Everything the engine needs, each value cited. Unknown stays None; never guessed."""
    state: Optional[Fact] = None                       # two-letter code
    household_size: Optional[Fact] = None
    members: list[MemberFacts] = Field(default_factory=list)
    rent_monthly: Optional[Fact] = None
    utilities_monthly: Optional[Fact] = None
    already_receives: list[Fact] = Field(default_factory=list)  # program keys the family says it has
    recert_due_date: Optional[Fact] = None             # ISO date, for families already enrolled
    language: str = "en"                               # BCP-47 primary tag of the family's messages
    contact_name: Optional[Fact] = None                # masked on the board until claimed
    contact_phone: Optional[Fact] = None               # masked on the board until claimed
    address: Optional[Fact] = None                     # masked on the board until claimed


class IntakeTurn(BaseModel):
    """What the intake model returns for one family message."""
    facts: HouseholdFacts
    reply: str = Field(description="next message to the family, in their language, one question at most")
    language: str = Field(default="en", description="BCP-47 tag of the family's language")
    needs_human: bool = Field(default=False, description="true only for immigration, legal, or abuse topics")
    needs_human_reason: str = ""


class ProgramResult(BaseModel):
    key: str
    label: str
    eligible: Optional[bool]
    amount: Optional[float]
    unit: Optional[str]                                # "USD/month" | "USD/year" | None
    eligible_members: list[int] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)     # statute or regulation URLs from the engine
    note: str = ""                                     # e.g. "not computed by the engine"


class EngineResult(BaseModel):
    engine: str
    engine_version: str
    year: int
    state: str
    programs: list[ProgramResult]
    snap_detail: dict = Field(default_factory=dict)
    members: list[dict] = Field(default_factory=list)  # per-member immigration gates


class FormField(BaseModel):
    form_field: str                                    # label as printed on the agency form
    value: str
    source_msg_id: str                                 # citation carried through from the fact


class FilingPlan(BaseModel):
    program: str
    form_name: str
    form_url: str
    fields: list[FormField]
    documents_needed: list[str]
    deadline: Optional[str] = None                     # ISO date
    deadline_rule: str = ""                            # the sentence and URL of the rule
    submit_route: str = ""                             # where the family or coordinator files it


class RiskReport(BaseModel):
    escalate: bool
    reasons: list[str] = Field(default_factory=list)   # each cites a fact or engine line
    question_for_coordinator: str = ""
    programs_safe_to_file: list[str] = Field(default_factory=list)
    programs_held: list[str] = Field(default_factory=list)


class Explanation(BaseModel):
    language: str
    message_to_family: str                             # the amounts and what each program needs
    per_program: list[dict] = Field(default_factory=list)  # {key, one_line, rule_url}


class DecisionCard(BaseModel):
    """The only way a coordinator is interrupted. One tap answers it."""
    card_id: str
    case_id: str
    situation: str                                     # two lines
    options: list[dict]                                # [{label, consequence}]
    default: str
    deadline: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)  # message ids and engine lines
    created_at: datetime
    answered: Optional[str] = None
    make_standing_rule: bool = False


class LedgerEntry(BaseModel):
    entry_id: str
    case_id: str
    action: str                                        # what
    why: str                                           # evidence
    at: datetime
    undo: str                                          # how to reverse it
    final_at: Optional[datetime] = None                # end of the veto window; None for reads
    undone: bool = False
    tier: Optional[int] = None                         # 0, 1, 2: which tier produced this action
    denied: bool = False                               # a guard hook refused it


class CaseRecord(BaseModel):
    case_id: str
    session_id: str
    gallery_case: Optional[str] = None                 # gallery id when played from the gallery
    facts: HouseholdFacts = Field(default_factory=HouseholdFacts)
    transcript: list[dict] = Field(default_factory=list)   # [{id, role, text, at, tier}]
    questions_asked: int = 0
    tier_counts: dict = Field(default_factory=lambda: {"0": 0, "1": 0, "2": 0})
    engine_result: Optional[EngineResult] = None
    filing_plans: list[FilingPlan] = Field(default_factory=list)
    risk: Optional[RiskReport] = None
    explanation: Optional[Explanation] = None
    cards: list[DecisionCard] = Field(default_factory=list)
    ledger: list[LedgerEntry] = Field(default_factory=list)
    claimed_by: Optional[str] = None                   # coordinator id once claimed; unmasks identifiers
    status: str = "intake"                             # intake | computed | filed | escalated | waiting | closed
    updated_at: Optional[datetime] = None
