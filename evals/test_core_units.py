"""Unit bench for the Quiet Core plumbing: store, ledger, cards, hooks, deadlines.

No model call happens here. The one live test is `test_hooks_live.py`.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent  # noqa: E402
from strands.interventions import Deny, Proceed  # noqa: E402

from benefitline.cards import (  # noqa: E402
    DEFAULT_OPTION,
    OPTIONS,
    answer_card,
    interrupt_budget,
    make_card,
    risk_pattern_key,
)
from benefitline.deadlines import deadlines_for, ics, load_rules  # noqa: E402
from benefitline.hooks import (  # noqa: E402
    MASK,
    LedgerWriter,
    MaskUntilClaimed,
    NoIdentifiersOut,
    redact,
)
from benefitline.ledger import Ledger  # noqa: E402
from benefitline.schemas import (  # noqa: E402
    CaseRecord,
    Fact,
    FilingPlan,
    HouseholdFacts,
    RiskReport,
)
from benefitline.store import LocalJsonStore  # noqa: E402

NOW = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Never let a stray env var send the unit suite at real DynamoDB."""
    monkeypatch.delenv("BENEFITLINE_TABLE", raising=False)
    monkeypatch.setenv("BENEFITLINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BENEFITLINE_DEADLINE_RULES", raising=False)


def make_case(case_id: str = "c1", **kw) -> CaseRecord:
    facts = HouseholdFacts(
        state=Fact(value="OK", source_msg_id="m1", quote="we live in Tulsa"),
        household_size=Fact(value=4, source_msg_id="m1", quote="four of us"),
        contact_name=Fact(value="Amina H.", source_msg_id="m2", quote="this is Amina"),
        contact_phone=Fact(value="918-555-0142", source_msg_id="m2", quote="918-555-0142"),
        address=Fact(value="120 S Elm, Tulsa OK", source_msg_id="m3", quote="120 S Elm"),
    )
    case = CaseRecord(case_id=case_id, session_id="s" * 33, facts=facts, **kw)
    case.updated_at = NOW
    return case


def make_risk(escalate: bool = True) -> RiskReport:
    return RiskReport(
        escalate=escalate,
        reasons=["m4: a parent is not a citizen or LPR"],
        question_for_coordinator="File for the children only?",
        programs_safe_to_file=["snap"],
        programs_held=["medicaid"],
    )


# --- store -----------------------------------------------------------------

def test_local_json_store_round_trip(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    case = make_case("case-a")
    Ledger.add(case, "filed snap", "m1", "withdraw the application", now=NOW)
    store.put(case)

    back = store.get("case-a")
    assert back is not None
    assert back.model_dump(mode="json") == case.model_dump(mode="json")
    assert store.get("missing") is None
    assert [c.case_id for c in store.list()] == ["case-a"]

    store.add_rule({"when": "k", "then": "v"})
    store.add_rule({"when": "k", "then": "v"})
    assert store.rules() == [{"when": "k", "then": "v"}]
    assert [c.case_id for c in store.list()] == ["case-a"]  # org_rules.json is not a case


# --- ledger ----------------------------------------------------------------

def test_ledger_veto_window_and_undo():
    case = make_case()
    read = Ledger.add(case, "read the board", "coordinator opened it", "n/a", is_read=True, now=NOW)
    write = Ledger.add(case, "filed snap", "m1", "withdraw the application", now=NOW)

    assert read.final_at is None
    assert write.final_at == NOW + timedelta(minutes=10)
    assert [e.entry_id for e in Ledger.pending(case, now=NOW)] == [write.entry_id]
    assert Ledger.pending(case, now=NOW + timedelta(minutes=11)) == []

    reversal = Ledger.undo(case, write.entry_id, now=NOW + timedelta(minutes=5))
    assert write.undone is True
    assert reversal.action == "undo: filed snap"
    assert case.ledger[-1] is reversal

    with pytest.raises(ValueError):
        Ledger.undo(case, write.entry_id, now=NOW + timedelta(minutes=6))

    late = Ledger.add(case, "filed medicaid", "m2", "withdraw it", now=NOW)
    with pytest.raises(ValueError):
        Ledger.undo(case, late.entry_id, now=NOW + timedelta(minutes=11))
    with pytest.raises(KeyError):
        Ledger.undo(case, "nope", now=NOW)


# --- cards -----------------------------------------------------------------

def test_make_card_fields():
    case = make_case()
    case.filing_plans = [
        FilingPlan(program="snap", form_name="F", form_url="u", fields=[], documents_needed=[],
                   deadline="2026-10-01"),
        FilingPlan(program="liheap", form_name="F", form_url="u", fields=[], documents_needed=[],
                   deadline="2026-09-20"),
    ]
    risk = make_risk()
    card = make_card(case, risk, now=NOW)

    assert len(card.situation.splitlines()) == 2
    assert [o["label"] for o in card.options] == [o["label"] for o in OPTIONS]
    assert all(o["consequence"] for o in card.options)
    assert card.default == DEFAULT_OPTION == card.options[0]["label"]
    assert card.deadline == "2026-09-20"  # earliest filing deadline
    assert "m4: a parent is not a citizen or LPR" in card.evidence
    assert f"pattern:{risk_pattern_key(risk)}" in card.evidence
    assert case.cards == [card]


def test_answer_card_stores_standing_rule_that_answers_the_next_case(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    risk = make_risk()

    first = make_case("case-1")
    card = make_card(first, risk, now=NOW)
    answer_card(first, card.card_id, OPTIONS[2]["label"], make_standing_rule=True,
                store=store, now=NOW)

    assert store.rules() == [{"when": risk_pattern_key(risk), "then": OPTIONS[2]["label"]}]
    assert store.get("case-1").cards[0].answered == OPTIONS[2]["label"]

    second = make_case("case-2")
    stale = make_card(second, make_risk(), now=NOW)  # an older card already open
    second_card = make_card(second, make_risk(), store=store, now=NOW)
    assert second_card.answered == OPTIONS[2]["label"]
    assert stale.answered is None  # the rule answers the new card, not an older one
    assert any("standing rule" in e.action for e in second.ledger)

    with pytest.raises(ValueError):
        answer_card(first, card.card_id, "not an option", False, store, now=NOW)


def test_interrupt_budget_exhaustion(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    assert interrupt_budget(store, per_week=5, now=NOW) == 5

    for i in range(5):
        case = make_case(f"case-{i}")
        make_card(case, make_risk(), now=NOW)
        store.put(case)
    assert interrupt_budget(store, per_week=5, now=NOW) == 0

    quiet = make_case("case-quiet")
    quiet_card = make_card(quiet, make_risk(), store=store, per_week=5, now=NOW)
    assert quiet_card.answered == DEFAULT_OPTION
    assert any("interrupt budget" in e.why for e in quiet.ledger)

    urgent = make_case("case-urgent")
    urgent.filing_plans = [
        FilingPlan(program="snap", form_name="F", form_url="u", fields=[], documents_needed=[],
                   deadline="2026-09-20")
    ]
    urgent_card = make_card(urgent, make_risk(), store=store, per_week=5, now=NOW)
    assert urgent_card.deadline == "2026-09-20"
    assert urgent_card.answered is None  # deadline cards still surface

    # last week's cards do not count against this week
    assert interrupt_budget(store, per_week=5, now=NOW + timedelta(days=7)) == 5


# --- hooks -----------------------------------------------------------------

def board_result() -> dict:
    return {
        "toolUseId": "t1",
        "status": "success",
        "content": [
            {
                "json": {
                    "case_id": "c1",
                    "contact_name": "Amina H.",
                    "contact_phone": "918-555-0142",
                    "address": {"value": "120 S Elm", "source_msg_id": "m3", "quote": "120 S Elm"},
                }
            }
        ],
    }


def after_event(
    tool_name: str, state: dict, result: dict, cancel_message: str | None = None
) -> AfterToolCallEvent:
    return AfterToolCallEvent(
        agent=SimpleNamespace(name="test"),
        selected_tool=None,
        tool_use={"toolUseId": "t1", "name": tool_name, "input": {"case_id": "c1"}},
        invocation_state=state,
        result=result,
        cancel_message=cancel_message,
    )


def before_event(tool_name: str, state: dict, tool_input: dict) -> BeforeToolCallEvent:
    return BeforeToolCallEvent(
        agent=SimpleNamespace(name="test"),
        selected_tool=None,
        tool_use={"toolUseId": "t1", "name": tool_name, "input": tool_input},
        invocation_state=state,
    )


def claimed_store(tmp_path, claimed_by=None) -> LocalJsonStore:
    """A store holding case c1, optionally claimed by a coordinator."""
    store = LocalJsonStore(tmp_path / "cases")
    case = make_case("c1")
    case.claimed_by = claimed_by
    store.put(case)
    return store


def test_mask_until_claimed_masks_and_unmasks(tmp_path):
    hook = MaskUntilClaimed(store=claimed_store(tmp_path))

    event = after_event("board_get_case", {}, board_result())
    hook.after_tool_call(event)
    payload = event.result["content"][0]["json"]
    assert payload["contact_name"] == MASK
    assert payload["contact_phone"] == MASK
    assert payload["address"]["value"] == MASK
    assert payload["address"]["quote"] == ""
    assert payload["case_id"] == "c1"

    # the claim lives on the stored case and must match the caller's coordinator id
    owner = MaskUntilClaimed(store=claimed_store(tmp_path / "owned", claimed_by="coord-1"))
    claimed = after_event("board_get_case", {"coordinator": "coord-1"}, board_result())
    owner.after_tool_call(claimed)
    revealed = claimed.result["content"][0]["json"]
    assert revealed["contact_name"] == "Amina H."
    assert revealed["address"]["value"] == "120 S Elm"

    stranger = after_event("board_get_case", {"coordinator": "coord-2"}, board_result())
    owner.after_tool_call(stranger)
    assert stranger.result["content"][0]["json"]["contact_name"] == MASK

    other = after_event("send_to_family", {}, board_result())
    hook.after_tool_call(other)
    assert other.result["content"][0]["json"]["contact_name"] == "Amina H."


def test_mask_until_claimed_cancels_reveal_before_claim(tmp_path):
    denied: list[tuple[str, str]] = []
    hook = MaskUntilClaimed(
        on_denied=lambda name, reason: denied.append((name, reason)),
        store=claimed_store(tmp_path),
    )

    event = before_event("reveal_identifiers", {"coordinator": "coord-1"}, {"case_id": "c1"})
    hook.before_tool_call(event)
    assert event.cancel_tool
    assert denied and denied[0][0] == "reveal_identifiers"

    owner = MaskUntilClaimed(store=claimed_store(tmp_path / "owned", claimed_by="coord-1"))
    ok = before_event("reveal_identifiers", {"coordinator": "coord-1"}, {"case_id": "c1"})
    owner.before_tool_call(ok)
    assert not ok.cancel_tool

    wrong = before_event("reveal_identifiers", {"coordinator": "coord-2"}, {"case_id": "c1"})
    owner.before_tool_call(wrong)
    assert wrong.cancel_tool


@pytest.mark.parametrize("tool_name", ["send_to_family", "send_to_board"])
@pytest.mark.parametrize("text", ["your SSN 123-45-6789 is on file", "case A123456789 is open"])
def test_no_identifiers_out_denies(tool_name, text):
    action = NoIdentifiersOut().before_tool_call(
        before_event(tool_name, {}, {"case_id": "c1", "text": text})
    )
    assert isinstance(action, Deny)
    assert action.reason


def test_no_identifiers_out_allows_clean_text():
    handler = NoIdentifiersOut()
    clean = handler.before_tool_call(
        before_event("send_to_family", {}, {"case_id": "c1", "text": "Your SNAP is filed."})
    )
    assert isinstance(clean, Proceed)
    unwatched = handler.before_tool_call(
        before_event("board_get_case", {}, {"case_id": "c1", "text": "SSN 123-45-6789"})
    )
    assert isinstance(unwatched, Proceed)


def test_redact():
    assert redact("SSN 123-45-6789 and A12345678 here") == (
        "SSN [REDACTED-SSN] and [REDACTED-ANUMBER] here"
    )
    assert redact("A123456789") == "[REDACTED-ANUMBER]"
    assert redact("no identifiers 12-345-678") == "no identifiers 12-345-678"


def test_ledger_writer_records_calls_and_denials(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    store.put(make_case("c1"))
    writer = LedgerWriter("c1", store=store)

    writer.after_tool_call(after_event("board_get_case", {}, board_result()))
    row = store.get("c1").ledger[-1]
    assert row.action == "tool board_get_case"
    assert row.denied is False
    assert row.final_at is None  # a read carries no veto window

    denied_event = after_event(
        "send_to_family", {}, board_result(), cancel_message="DENIED: an SSN appears in text"
    )
    writer.after_tool_call(denied_event)
    denied_row = store.get("c1").ledger[-1]
    assert denied_row.denied is True
    assert "DENIED" in denied_row.why


# --- deadlines --------------------------------------------------------------

RULES_FIXTURE = """# program | event | deadline rule | source URL | quote
snap | interview | within 30 days of the filing date | https://example.gov/snap | The agency must act within 30 days.
liheap | documents due | within 2 weeks of the filing date | https://example.gov/liheap | Proof is due two weeks after filing.
snap | report a change | no fixed deadline | https://example.gov/snap-change | Report changes when they happen.
"""


@pytest.fixture()
def rules_file(tmp_path) -> Path:
    path = tmp_path / "deadline_rules.md"
    path.write_text(RULES_FIXTURE, encoding="utf-8")
    return path


def test_load_rules_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        load_rules(tmp_path / "nope.md")
    assert "never invents" in str(err.value)


def test_deadlines_for_and_ics(rules_file):
    case = make_case()
    case.filing_plans = [
        FilingPlan(program="snap", form_name="F", form_url="u", fields=[], documents_needed=[]),
        FilingPlan(program="liheap", form_name="F", form_url="u", fields=[], documents_needed=[]),
    ]
    case.transcript = [{"id": "m1", "role": "family", "text": "hi", "at": NOW.isoformat()}]
    items = deadlines_for(case, path=rules_file)
    dated = [d for d in items if d["due"]]
    assert {d["due"] for d in dated} == {"2026-10-13", "2026-09-27"}

    # later activity must not move a due date
    Ledger.add(case, "filed snap", "m1", "withdraw it", now=NOW + timedelta(days=3))
    assert deadlines_for(case, path=rules_file) == items

    assert [d["due"] for d in dated] == sorted(d["due"] for d in dated)
    assert any(d["due"] == "" for d in items)  # the rule with no number keeps no invented date

    text = ics(case, path=rules_file)
    assert text.startswith("BEGIN:VCALENDAR")
    assert "VERSION:2.0" in text
    assert text.count("BEGIN:VEVENT") == len(dated)
    assert text.count("END:VEVENT") == len(dated)
    assert text.rstrip().endswith("END:VCALENDAR")
    assert "UID:" in text and "DTSTAMP:" in text


def test_load_rules_accepts_a_markdown_table(tmp_path):
    path = tmp_path / "table_rules.md"
    path.write_text(
        "| program | event | deadline rule | source URL | quote |\n"
        "|---|---|---|---|---|\n"
        "| snap | interview | within 30 days of the filing date | https://x/y | Act in 30 days. |\n",
        encoding="utf-8",
    )
    rules = load_rules(path)
    assert [r["program"] for r in rules] == ["snap"]
    assert rules[0]["rule_url"] == "https://x/y"


def test_deadlines_use_recert_date_when_present(rules_file):
    case = make_case()
    case.facts.recert_due_date = Fact(
        value="2026-11-30", source_msg_id="m5", quote="my renewal is November 30"
    )
    items = deadlines_for(case, path=rules_file)
    assert any(
        d["event"] == "recertification due" and d["due"] == "2026-11-30" for d in items
    )
