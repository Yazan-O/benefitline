"""Adversarial review bench for the Quiet Core plumbing.

Companion to `test_core_units.py`. Every test here either proves a guard holds under
pressure or pins a defect that the review report names. Tests that pin a defect carry a
`FIXED R-n` comment and assert the repaired behaviour: every defect the report named
has been fixed, and these are the tests that hold the fix in place.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookRegistry  # noqa: E402
from strands.interventions import Deny, Proceed  # noqa: E402

from benefitline import store as store_mod  # noqa: E402
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
    ANUMBER_PATTERN,
    MASK,
    SSN_PATTERN,
    LedgerWriter,
    MaskUntilClaimed,
    NoIdentifiersOut,
    TraceRedactor,
    guards,
    redact,
)
from benefitline.ledger import VETO_MINUTES, Ledger  # noqa: E402
from benefitline.schemas import (  # noqa: E402
    CaseRecord,
    EngineResult,
    Fact,
    FilingPlan,
    HouseholdFacts,
    ProgramResult,
    RiskReport,
)
from benefitline.store import DynamoStore, LocalJsonStore, get_store  # noqa: E402

NOW = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)
NAME = "Amina H."
PHONE = "918-555-0142"
ADDRESS = "120 S Elm, Tulsa OK"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.delenv("BENEFITLINE_TABLE", raising=False)
    monkeypatch.delenv("BENEFITLINE_DEADLINE_RULES", raising=False)
    monkeypatch.setenv("BENEFITLINE_DATA_DIR", str(tmp_path / "data"))


def make_case(case_id: str = "c1", **kw) -> CaseRecord:
    facts = HouseholdFacts(
        state=Fact(value="OK", source_msg_id="m1", quote="we live in Tulsa"),
        household_size=Fact(value=4, source_msg_id="m1", quote="four of us"),
        contact_name=Fact(value=NAME, source_msg_id="m2", quote="this is Amina"),
        contact_phone=Fact(value=PHONE, source_msg_id="m2", quote=PHONE),
        address=Fact(value=ADDRESS, source_msg_id="m3", quote="120 S Elm"),
    )
    case = CaseRecord(case_id=case_id, session_id="s" * 33, facts=facts, **kw)
    case.updated_at = NOW
    return case


def make_risk(escalate: bool = True, held=("medicaid",), safe=("snap",)) -> RiskReport:
    return RiskReport(
        escalate=escalate,
        reasons=["m4: a parent is not a citizen or LPR"],
        question_for_coordinator="File for the children only?",
        programs_safe_to_file=list(safe),
        programs_held=list(held),
    )


def claimed_store(tmp_path, claimed_by=None, extra=()) -> LocalJsonStore:
    """A store holding case c1 (optionally claimed) plus any extra cases."""
    store = LocalJsonStore(tmp_path / "cases")
    case = make_case("c1")
    case.claimed_by = claimed_by
    store.put(case)
    for other_id, other_claim in extra:
        other = make_case(other_id)
        other.claimed_by = other_claim
        store.put(other)
    return store


def after_event(tool_name, state, result, cancel_message=None, tool_input=None):
    return AfterToolCallEvent(
        agent=SimpleNamespace(name="test"),
        selected_tool=None,
        tool_use={
            "toolUseId": "t1",
            "name": tool_name,
            "input": tool_input if tool_input is not None else {"case_id": "c1"},
        },
        invocation_state=state,
        result=result,
        cancel_message=cancel_message,
    )


def before_event(tool_name, state, tool_input):
    return BeforeToolCallEvent(
        agent=SimpleNamespace(name="test"),
        selected_tool=None,
        tool_use={"toolUseId": "t1", "name": tool_name, "input": tool_input},
        invocation_state=state,
    )


def ok(data) -> dict:
    return {"toolUseId": "t1", "status": "success", "content": [{"json": data}]}


# --- 1. mask bypass --------------------------------------------------------


def test_mask_reaches_deeply_nested_identifiers():
    payload = {
        "case_id": "c1",
        "envelope": {
            "page": 1,
            "rows": [
                {
                    "household": {
                        "contact_name": NAME,
                        "contact_phone": PHONE,
                        "address": {"value": ADDRESS, "source_msg_id": "m3", "quote": "120 S Elm"},
                    }
                }
            ],
        },
    }
    event = after_event("board_get_case", {}, ok(payload))
    MaskUntilClaimed().after_tool_call(event)
    rendered = json.dumps(event.result, ensure_ascii=False)
    assert NAME not in rendered and PHONE not in rendered and ADDRESS not in rendered
    household = event.result["content"][0]["json"]["envelope"]["rows"][0]["household"]
    assert household["contact_name"] == MASK
    assert household["address"]["value"] == MASK and household["address"]["quote"] == ""


def test_mask_covers_a_list_of_cases():
    rows = [
        {"case_id": "c1", "status": "open", "claimed_by": None, "open_cards": 1,
         "contact_name": NAME, "contact_phone": PHONE, "address": ADDRESS},
        {"case_id": "c2", "status": "open", "claimed_by": None, "open_cards": 0,
         "contact_name": "Bilal K.", "contact_phone": "918-555-0199", "address": "9 Oak"},
    ]
    event = after_event("board_list_cases", {}, ok({"cases": rows}))
    MaskUntilClaimed().after_tool_call(event)
    rendered = json.dumps(event.result, ensure_ascii=False)
    for leak in (NAME, PHONE, ADDRESS, "Bilal K.", "918-555-0199", "9 Oak"):
        assert leak not in rendered


def test_mask_covers_a_text_block(tmp_path):
    """FIXED R-2: a text block is masked too, by value and as embedded JSON."""
    store = claimed_store(tmp_path)
    result = {
        "toolUseId": "t1",
        "status": "error",
        "content": [
            {"text": f"no case for {NAME} at {ADDRESS} ({PHONE})"},
            {"text": json.dumps({"case_id": "c1", "contact_name": NAME})},
        ],
    }
    event = after_event("board_get_case", {}, result)
    MaskUntilClaimed(store=store).after_tool_call(event)
    rendered = json.dumps(event.result, ensure_ascii=False)
    for leak in (NAME, PHONE, ADDRESS):
        assert leak not in rendered
    assert MASK in event.result["content"][0]["text"]
    assert json.loads(event.result["content"][1]["text"])["contact_name"] == MASK


def test_mask_covers_a_string_result_and_a_dict_without_content(tmp_path):
    """FIXED R-3: a bare string, or a dict with no content list, is masked as well."""
    store = claimed_store(tmp_path)
    event = after_event("board_x", {}, f"{NAME} {PHONE}")
    MaskUntilClaimed(store=store).after_tool_call(event)
    assert NAME not in event.result and PHONE not in event.result
    assert MASK in event.result

    plain = after_event("board_x", {}, {"case_id": "c1", "contact_name": NAME})
    MaskUntilClaimed(store=store).after_tool_call(plain)
    assert plain.result["contact_name"] == MASK


def test_reveal_identifiers_is_cancelled_unclaimed_and_passes_claimed(tmp_path):
    hook = MaskUntilClaimed(store=claimed_store(tmp_path))
    denied: list[tuple[str, str]] = []
    hook.on_denied = lambda name, reason: denied.append((name, reason))

    unclaimed = before_event("reveal_identifiers", {"coordinator": "coord-1"}, {"case_id": "c1"})
    hook.before_tool_call(unclaimed)
    assert unclaimed.cancel_tool
    assert denied and denied[0][0] == "reveal_identifiers"

    owner = MaskUntilClaimed(store=claimed_store(tmp_path / "owned", claimed_by="coord-1"))
    claimed = before_event("reveal_identifiers", {"coordinator": "coord-1"}, {"case_id": "c1"})
    owner.before_tool_call(claimed)
    assert not claimed.cancel_tool

    # the same claimed case, a different coordinator, then a caller with no id at all
    for state in ({"coordinator": "coord-2"}, {}):
        blocked = before_event("reveal_identifiers", state, {"case_id": "c1"})
        owner.before_tool_call(blocked)
        assert blocked.cancel_tool

    other = before_event("board_get_case", {}, {"case_id": "c1"})
    hook.before_tool_call(other)
    assert not other.cancel_tool  # only the reveal is gated before the call


def test_a_claim_on_one_case_does_not_unmask_another(tmp_path):
    """FIXED R-1: the claim is read per case from the store and matched to the caller.

    Coordinator coord-1 has claimed c1. On one board read c1 comes back in the clear and
    c2, which nobody claimed, stays masked; reveal on c2 is still cancelled for that
    same caller.
    """
    store = claimed_store(tmp_path, claimed_by="coord-1", extra=[("c2", None)])
    state = {"coordinator": "coord-1"}
    rows = [
        {"case_id": "c1", "claimed_by": "coord-1", "contact_name": NAME,
         "contact_phone": PHONE, "address": ADDRESS},
        {"case_id": "c2", "claimed_by": None, "contact_name": "Bilal K.",
         "contact_phone": "918-555-0199", "address": "9 Oak"},
    ]
    event = after_event("board_list_cases", state, ok({"cases": rows}), tool_input={})
    MaskUntilClaimed(store=store).after_tool_call(event)
    masked_rows = event.result["content"][0]["json"]["cases"]
    assert masked_rows[0]["contact_name"] == NAME          # the case this caller claimed
    assert masked_rows[1]["contact_name"] == MASK          # every other family
    assert "Bilal K." not in json.dumps(event.result)

    reveal = before_event("reveal_identifiers", state, {"case_id": "c2"})
    MaskUntilClaimed(store=store).before_tool_call(reveal)
    assert reveal.cancel_tool


# --- 2. NoIdentifiersOut ---------------------------------------------------


CAUGHT = [
    "your SSN 123-45-6789 is on file",
    "your SSN 123 45 6789 is on file",
    "your SSN 123.45.6789 is on file",
    "SSN 123456789 on file",
    "case A-123456789 is open",
    "case A# 123456789 is open",
    "case A123456789 is open",
    "case A12345678 is open",
    "الرقم ١٢٣-٤٥-٦٧٨٩",
]
ALLOWED = [
    "call us back on 918-555-0142",
    "call us back on 918 555 0142",
    "no identifiers 12-345-678",
    "your case number is 4471",
]


@pytest.mark.parametrize("text", CAUGHT)
def test_outbound_text_with_an_identifier_is_denied(text):
    for tool in ("send_to_family", "send_to_board"):
        out = NoIdentifiersOut().before_tool_call(
            before_event(tool, {}, {"case_id": "c1", "text": text})
        )
        assert isinstance(out, Deny), f"{tool} let through: {text!r}"


@pytest.mark.parametrize("text", ALLOWED)
def test_ordinary_text_still_proceeds(text):
    out = NoIdentifiersOut().before_tool_call(
        before_event("send_to_family", {}, {"case_id": "c1", "text": text})
    )
    assert isinstance(out, Proceed), f"false positive on {text!r}"


def test_non_outbound_tools_are_not_scanned():
    out = NoIdentifiersOut().before_tool_call(
        before_event("board_get_case", {}, {"case_id": "123-45-6789"})
    )
    assert isinstance(out, Proceed)


def test_redact_matches_the_intervention():
    assert redact("SSN 123-45-6789 and A12345678 here") == (
        "SSN [REDACTED-SSN] and [REDACTED-ANUMBER] here")
    assert redact("A123456789") == "[REDACTED-ANUMBER]"
    assert redact("no identifiers 12-345-678") == "no identifiers 12-345-678"
    assert redact("call 918-555-0142") == "call 918-555-0142"
    assert "[REDACTED" in redact("123 45 6789")
    assert not SSN_PATTERN.search("123456789012")  # no 9-digit hit inside a longer run
    assert ANUMBER_PATTERN.search("A 123456789")


def test_ssn_split_across_two_kwargs_is_caught():
    """FIXED R-4: the string leaves are matched end to end, not one argument at a time."""
    out = NoIdentifiersOut().before_tool_call(
        before_event("send_to_family", {},
                     {"case_id": "c1", "text": "SSN 123-45", "footer": "-6789 thanks"})
    )
    assert isinstance(out, Deny)

    split_anumber = NoIdentifiersOut().before_tool_call(
        before_event("send_to_board", {}, {"case_id": "c1", "text": "case A1234", "tail": "5678"})
    )
    assert isinstance(split_anumber, Deny)


def test_identifier_inside_a_nested_argument_is_caught():
    """FIXED R-5: the whole tool input is walked, nested dicts and lists included."""
    out = NoIdentifiersOut().before_tool_call(
        before_event("send_to_family", {},
                     {"case_id": "c1", "payload": {"body": "SSN 123-45-6789"}})
    )
    assert isinstance(out, Deny)

    listed = NoIdentifiersOut().before_tool_call(
        before_event("send_to_board", {}, {"case_id": "c1", "lines": ["A123456789"]})
    )
    assert isinstance(listed, Deny)

    deep = NoIdentifiersOut().before_tool_call(
        before_event("send_to_family", {},
                     {"case_id": "c1", "blocks": [{"rows": [{"t": "A-123456789"}]}]})
    )
    assert isinstance(deep, Deny)


# --- 3. ledger -------------------------------------------------------------


def test_undo_is_refused_after_the_window_twice_and_on_reads():
    case = make_case()
    write = Ledger.add(case, action="filed snap", why="w", undo="u", now=NOW)
    assert write.final_at == NOW + timedelta(minutes=VETO_MINUTES)

    with pytest.raises(ValueError, match="veto window"):
        Ledger.undo(case, write.entry_id, now=NOW + timedelta(minutes=VETO_MINUTES))

    Ledger.undo(case, write.entry_id, now=NOW + timedelta(minutes=1))
    with pytest.raises(ValueError, match="already undone"):
        Ledger.undo(case, write.entry_id, now=NOW + timedelta(minutes=2))

    read = Ledger.add(case, action="read board", why="w", undo="u", is_read=True, now=NOW)
    assert read.final_at is None
    with pytest.raises(ValueError, match="nothing to undo"):
        Ledger.undo(case, read.entry_id, now=NOW + timedelta(minutes=1))

    with pytest.raises(KeyError):
        Ledger.undo(case, "nope", now=NOW)


def test_clock_injection_and_pending_window():
    case = make_case()
    entry = Ledger.add(case, action="filed snap", why="w", undo="u", now=NOW)
    assert entry.at == NOW
    assert Ledger.pending(case, now=NOW + timedelta(minutes=9))
    assert not Ledger.pending(case, now=NOW + timedelta(minutes=10))
    Ledger.undo(case, entry.entry_id, now=NOW + timedelta(minutes=1))
    assert not Ledger.pending(case, now=NOW + timedelta(minutes=2))


def test_aware_datetimes_survive_a_local_store_round_trip(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    case = make_case()
    Ledger.add(case, action="filed snap", why="w", undo="u", now=NOW)
    store.put(case)
    back = store.get("c1")
    assert back.ledger[0].at == NOW
    assert back.ledger[0].at.tzinfo is not None
    assert back.ledger[0].final_at.utcoffset() == timedelta(0)
    assert back.updated_at == NOW
    with pytest.raises(ValueError, match="veto window"):
        Ledger.undo(back, back.ledger[0].entry_id, now=NOW + timedelta(hours=1))


def test_naive_clock_is_treated_as_utc():
    case = make_case()
    entry = Ledger.add(case, action="a", why="w", undo="u", now=datetime(2026, 9, 13, 15, 0))
    assert entry.at == NOW


# --- 4. cards --------------------------------------------------------------


def test_standing_rule_matches_the_same_pattern_and_not_another(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    first = make_case("case-1")
    card = make_card(first, make_risk(), store=store, now=NOW)
    answer_card(first, card.card_id, OPTIONS[2]["label"], True, store, now=NOW)

    same = make_case("case-2")
    same_card = make_card(same, make_risk(), store=store, now=NOW)
    assert same_card.answered == OPTIONS[2]["label"]

    other_risk = make_risk(held=("snap",), safe=("liheap",))
    assert risk_pattern_key(other_risk) != risk_pattern_key(make_risk())
    different = make_case("case-3")
    assert make_card(different, other_risk, store=store, now=NOW).answered is None


def test_budget_exhaustion_only_auto_answers_cards_without_a_deadline(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    for i in range(5):
        case = make_case(f"case-{i}")
        make_card(case, make_risk(), now=NOW)
        store.put(case)
    assert interrupt_budget(store, per_week=5, now=NOW) == 0

    quiet = make_case("case-quiet")
    quiet_card = make_card(quiet, make_risk(), store=store, per_week=5, now=NOW)
    assert quiet_card.answered == DEFAULT_OPTION

    urgent = make_case("case-urgent")
    urgent.filing_plans = [FilingPlan(program="snap", form_name="F", form_url="u", fields=[],
                                      documents_needed=[], deadline="2026-09-20")]
    assert make_card(urgent, make_risk(), store=store, per_week=5, now=NOW).answered is None


def test_make_card_depletes_the_budget_and_persists(tmp_path):
    """FIXED R-6: every card is persisted, and only cards that interrupted are charged."""
    store = LocalJsonStore(tmp_path / "cases")
    cards = [make_card(make_case(f"case-{i}"), make_risk(), store=store, per_week=5, now=NOW)
             for i in range(8)]
    assert len(store.list()) == 8
    assert interrupt_budget(store, per_week=5, now=NOW) == 0

    # the first five interrupted a coordinator; the rest were auto-answered by the default
    assert [c.answered for c in cards[:5]] == [None] * 5
    assert all(c.answered == DEFAULT_OPTION for c in cards[5:])
    assert all(any(line.startswith("auto:") for line in c.evidence) for c in cards[5:])


def test_answer_card_refuses_an_unknown_option_and_an_unknown_card(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    case = make_case()
    card = make_card(case, make_risk(), store=store, now=NOW)
    with pytest.raises(ValueError):
        answer_card(case, card.card_id, "burn it down", False, store, now=NOW)
    with pytest.raises(KeyError):
        answer_card(case, "nosuchcard", OPTIONS[0]["label"], False, store, now=NOW)
    assert case.cards[0].answered is None


def test_answer_card_refuses_to_re_answer(tmp_path):
    """FIXED R-7: the second tap is refused and the first answer stands."""
    store = LocalJsonStore(tmp_path / "cases")
    case = make_case()
    card = make_card(case, make_risk(), store=store, now=NOW)
    answer_card(case, card.card_id, OPTIONS[0]["label"], False, store, now=NOW)
    with pytest.raises(ValueError, match="already answered"):
        answer_card(case, card.card_id, OPTIONS[1]["label"], False, store, now=NOW)
    assert case.cards[0].answered == OPTIONS[0]["label"]


# --- 5. store --------------------------------------------------------------


def test_concurrent_puts_leave_a_valid_file(tmp_path):
    """FIXED R-8: each writer gets its own temp file, so concurrent puts do not collide."""
    store = LocalJsonStore(tmp_path / "cases")
    store.put(make_case())
    errors: list[Exception] = []

    def writer(coordinator: str) -> None:
        case = make_case()
        case.claimed_by = coordinator
        for _ in range(40):
            try:
                store.put(case)
            except Exception as exc:  # a shared .tmp path can collide
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"coord-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    back = store.get("c1")  # the file must still parse
    assert back is not None
    assert back.claimed_by in {"coord-0", "coord-1"}
    assert errors == [], f"{len(errors)} puts raised, first: {errors[0]!r}"
    assert not list((tmp_path / "cases").glob("*.tmp"))  # no temp file left behind


def test_list_on_an_empty_dir_and_rules_default(tmp_path):
    store = LocalJsonStore(tmp_path / "empty")
    assert store.list() == []
    assert store.rules() == []
    assert store.get("missing") is None
    store.add_rule({"when": "k", "then": "v"})
    store.add_rule({"when": "k", "then": "v"})
    assert store.rules() == [{"when": "k", "then": "v"}]
    assert store.list() == []  # org_rules.json is not a case


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * 81, "sp ace", None])
def test_case_id_is_validated(tmp_path, bad):
    """FIXED R-9: a case id that could leave the data directory is refused."""
    store = LocalJsonStore(tmp_path / "cases")
    with pytest.raises(ValueError, match="invalid case_id"):
        store.get(bad)
    with pytest.raises(ValueError, match="invalid case_id"):
        store._case_path(bad)
    assert store._case_path("case-1_A").parent == (tmp_path / "cases")


class _StubTable:
    def __init__(self, items: dict) -> None:
        self.items = items

    def get_item(self, Key):  # noqa: N803 - boto3 spelling
        item = self.items.get(Key["case_id"])
        return {"Item": item} if item else {}

    def put_item(self, Item):  # noqa: N803 - boto3 spelling
        self.items[Item["case_id"]] = Item

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}


class _StubResource:
    def __init__(self) -> None:
        self.items: dict = {}
        self.meta = SimpleNamespace(client=SimpleNamespace())

    def Table(self, name):  # noqa: N802 - boto3 spelling
        return _StubTable(self.items)


@pytest.fixture()
def stub_boto3(monkeypatch):
    import boto3

    resource = _StubResource()
    monkeypatch.setattr(boto3, "resource", lambda *a, **k: resource)
    return resource


def test_dynamo_store_round_trip_on_a_stub(stub_boto3):
    store = DynamoStore("benefitline-test")
    assert store.get("c1") is None
    case = make_case()
    Ledger.add(case, action="filed snap", why="w", undo="u", now=NOW)
    store.put(case)

    back = store.get("c1")
    assert back.case_id == "c1"
    assert back.ledger[0].at == NOW
    assert back.facts.contact_name.value == NAME

    store.add_rule({"when": "k", "then": "v"})
    store.add_rule({"when": "k", "then": "v"})
    assert store.rules() == [{"when": "k", "then": "v"}]
    assert [c.case_id for c in store.list()] == ["c1"]  # the rules row is skipped


def test_get_store_switches_on_the_env(monkeypatch, tmp_path, stub_boto3):
    monkeypatch.setenv("BENEFITLINE_DATA_DIR", str(tmp_path / "local"))
    local = get_store()
    assert isinstance(local, LocalJsonStore)
    assert local.dir == tmp_path / "local"

    monkeypatch.setenv("BENEFITLINE_TABLE", "benefitline-cases")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    remote = get_store()
    assert isinstance(remote, DynamoStore)
    assert remote.table_name == "benefitline-cases" and remote.region == "us-east-1"

    monkeypatch.delenv("BENEFITLINE_TABLE")
    assert isinstance(get_store(), LocalJsonStore)
    assert isinstance(local, store_mod.Store)  # the Protocol is satisfied


# --- 6. ordering: LedgerWriter + MaskUntilClaimed + TraceRedactor ----------


def _run_after_hooks(providers, event):
    registry = HookRegistry()
    for provider in providers:
        registry.add_hook(provider)
    registry.invoke_callbacks(event)
    return event


def _logged(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def test_the_guard_set_leaks_nothing_to_the_ledger_or_the_log(tmp_path, caplog):
    """FIXED R-10: `guards()` orders the hooks so the log sees the masked result."""
    store = claimed_store(tmp_path)
    payload = {"case_id": "c1", "contact_name": NAME, "contact_phone": PHONE,
               "address": ADDRESS, "ssn": "123-45-6789"}
    event = after_event("board_get_case", {}, ok(payload))

    built = guards(case_id="c1", store=store)
    assert [type(h).__name__ for h in built.hooks] == [
        "TraceRedactor", "MaskUntilClaimed", "LedgerWriter"]
    assert [type(i).__name__ for i in built.interventions] == ["NoIdentifiersOut"]

    with caplog.at_level(logging.INFO, logger="benefitline.trace"):
        _run_after_hooks(list(built.hooks), event)

    row = store.get("c1").ledger[-1]
    assert row.action == "tool board_get_case" and not row.denied
    assert row.final_at is None  # a read carries no veto window
    for leak in (NAME, PHONE, ADDRESS, "123-45-6789"):
        assert leak not in row.why, f"{leak!r} reached the ledger"
    assert row.why == '{"case_id": "c1"}'  # `why` is built from the input, never the result

    logged = _logged(caplog)
    assert "[REDACTED-SSN]" in logged and "123-45-6789" not in logged
    for leak in (NAME, PHONE, ADDRESS):
        assert leak not in logged, f"{leak!r} reached the log"
    assert MASK in logged

    assert event.result["content"][0]["json"]["contact_name"] == MASK  # the agent sees the mask


def test_trace_redactor_scrubs_contact_values_even_without_the_mask(tmp_path, caplog):
    """FIXED R-10: with a store it removes the case's own three values by value."""
    store = claimed_store(tmp_path)
    payload = {"case_id": "c1", "note": f"{NAME} called from {PHONE}"}
    event = after_event("board_get_case", {}, ok(payload))
    with caplog.at_level(logging.INFO, logger="benefitline.trace"):
        _run_after_hooks([TraceRedactor(store=store)], event)   # no mask in the chain
    logged = _logged(caplog)
    assert NAME not in logged and PHONE not in logged
    assert "[REDACTED-CONTACT]" in logged


def test_guards_ledger_callback_and_denial_callback(tmp_path):
    denied: list[tuple[str, str]] = []
    rows: list = []
    store = claimed_store(tmp_path)
    built = guards(on_denied=lambda n, r: denied.append((n, r)), ledger_cb=rows.append,
                   case_id="c1", store=store)

    reveal = before_event("reveal_identifiers", {"coordinator": "coord-9"}, {"case_id": "c1"})
    for hook in built.hooks:
        if isinstance(hook, MaskUntilClaimed):
            hook.before_tool_call(reveal)
    assert reveal.cancel_tool and denied and denied[0][0] == "reveal_identifiers"

    _run_after_hooks(list(built.hooks),
                     after_event("board_get_case", {}, ok({"case_id": "c1"})))
    assert rows and rows[0].action == "tool board_get_case"


def test_denied_call_is_ledgered_with_a_redacted_reason(tmp_path):
    store = LocalJsonStore(tmp_path / "cases")
    store.put(make_case())
    event = after_event(
        "send_to_family",
        {},
        {"toolUseId": "t1", "status": "error", "content": [{"text": "DENIED"}]},
        cancel_message="DENIED: an SSN appears in `text`",
        tool_input={"case_id": "c1", "text": "your SSN 123-45-6789"},
    )
    _run_after_hooks([LedgerWriter("c1", store=store)], event)
    row = store.get("c1").ledger[-1]
    assert row.denied and row.action == "tool send_to_family denied"
    assert "123-45-6789" not in row.why and "[REDACTED-SSN]" in row.why
    assert row.final_at is None  # a refused call has nothing to veto


# --- 7. deadlines ----------------------------------------------------------


BARE = """# a comment line
snap | recertification | within 12 months of the filing date | https://example.gov/a | "twelve months"
liheap | application window | opens 30 days after the filing date | https://example.gov/b | "thirty days"
snap | report a change | no fixed period stated | https://example.gov/c | "report changes"
"""

TABLE = """| program | event | rule | source | quote |
| --- | --- | --- | --- | --- |
| snap | recertification | within 12 months of the filing date | https://example.gov/a | "twelve months" |
| liheap | application window | opens 30 days after the filing date | https://example.gov/b | "thirty days" |
| snap | report a change | no fixed period stated | https://example.gov/c | "report changes" |
"""


@pytest.fixture()
def rules_files(tmp_path):
    bare = tmp_path / "bare.md"
    table = tmp_path / "table.md"
    bare.write_text(BARE, encoding="utf-8")
    table.write_text(TABLE, encoding="utf-8")
    return bare, table


def test_both_rule_file_formats_parse_the_same(rules_files, monkeypatch):
    bare, table = rules_files
    from_bare = load_rules(bare)
    from_table = load_rules(table)
    assert from_bare == from_table
    assert len(from_bare) == 3
    assert from_bare[0]["program"] == "snap"
    assert from_bare[0]["rule_url"] == "https://example.gov/a"

    monkeypatch.setenv("BENEFITLINE_DEADLINE_RULES", str(table))
    assert load_rules() == from_table

    monkeypatch.setenv("BENEFITLINE_DEADLINE_RULES", str(table.parent / "gone.md"))
    with pytest.raises(FileNotFoundError, match="never invents"):
        load_rules()


def test_deadlines_and_ics_for_a_filed_case(rules_files):
    bare, _ = rules_files
    case = make_case()
    case.filing_plans = [FilingPlan(program="snap", form_name="F", form_url="u", fields=[],
                                    documents_needed=[])]
    case.transcript = [
        {"id": "m1", "role": "family", "text": "hi", "at": "2026-09-01T10:00:00+00:00"}
    ]

    rows = deadlines_for(case, path=bare)
    assert [r["program"] for r in rows] == ["snap", "snap"]  # liheap filtered out
    dated = [r for r in rows if r["due"]]
    assert dated[0]["due"] == "2027-08-27"  # 12 "months" == 360 days from 2026-09-01
    assert rows[-1]["due"] == ""            # a rule with no number keeps an empty due

    text = ics(case, path=bare)
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")[:-1]
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    assert sum(1 for line in lines if line == "BEGIN:VEVENT") == 1
    uid = [line for line in lines if line.startswith("UID:")]
    assert uid and uid[0].endswith("@benefitline")
    assert [line for line in lines if line.startswith("DTSTART")] == [
        "DTSTART;VALUE=DATE:20270827"]
    assert [line for line in lines if line.startswith("DTEND")] == [
        "DTEND;VALUE=DATE:20270828"]
    assert any(line.startswith("DTSTAMP:") and line.endswith("Z") for line in lines)


def test_a_case_with_no_facts_and_no_transcript(rules_files):
    """FIXED R-11: no anchor date and no program means no deadline, not an invented one."""
    bare, _ = rules_files
    case = CaseRecord(case_id="empty", session_id="s" * 33, facts=HouseholdFacts())
    case.updated_at = NOW

    assert deadlines_for(case, path=bare) == []
    assert "BEGIN:VEVENT" not in ics(case, path=bare)

    # a program but still no anchor date: nothing is dated from the wall clock
    case.filing_plans = [FilingPlan(program="snap", form_name="F", form_url="u", fields=[],
                                    documents_needed=[])]
    assert deadlines_for(case, path=bare) == []

    # an anchor, and now only the case's own programs are matched
    case.transcript = [{"id": "m1", "role": "family", "text": "hi",
                        "at": "2026-09-01T10:00:00+00:00"}]
    rows = deadlines_for(case, path=bare)
    assert {r["program"] for r in rows} == {"snap"}


def test_programs_come_from_the_engine_and_from_what_the_family_already_has(rules_files):
    """FIXED R-11: eligible engine programs and `already_receives` both name a program."""
    bare, _ = rules_files
    case = make_case()
    case.transcript = [{"id": "m1", "role": "family", "text": "hi",
                        "at": "2026-09-01T10:00:00+00:00"}]
    case.facts.already_receives = [Fact(value="liheap", source_msg_id="m1", quote="we get LIHEAP")]
    assert {r["program"] for r in deadlines_for(case, path=bare)} == {"liheap"}

    case.engine_result = EngineResult(
        engine="policyengine-us", engine_version="2.0.4", year=2026, state="OK",
        programs=[
            ProgramResult(key="snap", label="SNAP", eligible=True, amount=291.0,
                          unit="USD/month"),
            ProgramResult(key="wic", label="WIC", eligible=False, amount=None, unit=None),
        ],
    )
    assert {r["program"] for r in deadlines_for(case, path=bare)} == {"liheap", "snap"}


def test_ics_of_a_case_with_no_dated_deadline_is_an_empty_calendar(tmp_path):
    only_undated = tmp_path / "undated.md"
    only_undated.write_text(
        'snap | report a change | no fixed period stated | https://example.gov/c | "q"\n',
        encoding="utf-8",
    )
    case = make_case()
    text = ics(case, path=only_undated)
    assert "BEGIN:VEVENT" not in text
    assert text == (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Benefitline//Deadlines//EN\r\n"
        "CALSCALE:GREGORIAN\r\nEND:VCALENDAR\r\n"
    )


def test_ics_folds_long_lines():
    """FIXED R-12: every content line is folded at 75 octets, continuations start with a space."""
    case = make_case()
    case.filing_plans = [FilingPlan(program="snap", form_name="F", form_url="u", fields=[],
                                    documents_needed=[])]
    case.transcript = [{"id": "m1", "role": "family", "text": "hi",
                        "at": "2026-09-01T10:00:00+00:00"}]
    long_rule = [{"program": "snap", "event": "recertification " + "x" * 200,
                  "rule": "within 12 months", "rule_url": "https://example.gov/a",
                  "quote": "\u0623\u0648\u0643\u0644\u0627\u0647\u0648\u0645\u0627 " * 20}]
    text = ics(case, rules=long_rule)
    lines = text.split("\r\n")[:-1]
    assert max(len(line.encode("utf-8")) for line in lines) <= 75
    assert any(line.startswith(" ") for line in lines)   # something actually folded

    # unfolding puts the original content back, multi-byte characters intact
    unfolded: list[str] = []
    for line in lines:
        if line.startswith(" "):
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    assert unfolded[0] == "BEGIN:VCALENDAR" and unfolded[-1] == "END:VCALENDAR"
    assert any(line.startswith("SUMMARY:snap: recertification " + "x" * 200)
               for line in unfolded)
    assert sum(1 for line in unfolded if line == "BEGIN:VEVENT") == 1
