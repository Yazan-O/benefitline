"""The API surface: payload validation, the picker, the board, and the demo path.

Everything here runs against a LocalJsonStore in a tmp directory and calls no model.
The one test that does call a model is marked `live`:

    python -m pytest evals/test_service_units.py -m live
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from benefitline import service  # noqa: E402
from benefitline.hooks import MASK  # noqa: E402
from benefitline.ledger import Ledger  # noqa: E402
from benefitline.schemas import Fact  # noqa: E402
from benefitline.store import LocalJsonStore  # noqa: E402

CASE1 = "case1_single_mother_two_kids"


def sid() -> str:
    return f"{uuid.uuid4()}-benefitline"


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    """Every test gets its own case directory and never reaches DynamoDB."""
    monkeypatch.delenv("BENEFITLINE_TABLE", raising=False)
    monkeypatch.setenv("BENEFITLINE_DATA_DIR", str(tmp_path / "cases"))
    return LocalJsonStore(tmp_path / "cases")


# --- validation -------------------------------------------------------------


def test_short_session_id_is_refused():
    out = service.handle({"action": "gallery", "session_id": "too-short"})
    assert out["ok"] is False
    assert "session_id" in out["error"]
    assert set(out) == {"ok", "case", "board", "gallery", "error"}


def test_non_string_message_is_refused():
    out = service.handle({"action": "chat", "session_id": sid(), "message": 42})
    assert out["ok"] is False
    assert out["error"] == "message must be a string"


def test_unknown_action_is_refused():
    out = service.handle({"action": "drop_table", "session_id": sid()})
    assert out["ok"] is False and "unknown action" in out["error"]


# --- gallery ----------------------------------------------------------------


def test_gallery_lists_six_cases_with_ground_truth():
    out = service.handle({"action": "gallery", "session_id": sid()})
    assert out["ok"] is True
    rows = out["gallery"]
    assert len(rows) == 6
    truth = json.loads((ROOT / "gallery" / "ground_truth.json").read_text(encoding="utf-8"))
    for row in rows:
        assert {"id", "name", "description", "languages"} <= set(row)
        assert row["fictional"] is True
        expected = truth["cases"][row["id"]]["programs"]
        assert row["amounts"]["snap"]["amount"] == expected["snap"]["amount"]
    assert next(r for r in rows if r["id"] == CASE1)["amounts"]["snap"]["amount"] == 632.0


# --- board and claim --------------------------------------------------------


def start(session: str, gallery_case: str = CASE1) -> dict:
    out = service.handle({"action": "start_case", "session_id": session,
                          "gallery_case": gallery_case})
    assert out["ok"] is True, out["error"]
    return out["case"]


def with_contact(store: LocalJsonStore, case_id: str) -> None:
    case = store.get(case_id)
    case.facts.contact_name = Fact(value="Dana R.", source_msg_id="m1", quote="Dana R.")
    case.facts.contact_phone = Fact(value="(918) 555-0142", source_msg_id="m1", quote="")
    store.put(case)


def test_board_masks_identifiers_until_the_claiming_coordinator_asks(local_store):
    session = sid()
    case_id = start(session)["case_id"]
    with_contact(local_store, case_id)

    anonymous = service.handle({"action": "board", "session_id": session})["board"][0]
    assert anonymous["masked"] is True
    assert anonymous["facts"]["contact_name"]["value"] == MASK

    claimed = service.handle({"action": "claim", "session_id": session,
                              "case_id": case_id, "coordinator": "maria"})
    assert claimed["ok"] is True

    mine = service.handle({"action": "board", "session_id": session,
                           "coordinator": "maria"})["board"][0]
    assert mine["masked"] is False
    assert mine["facts"]["contact_name"]["value"] == "Dana R."
    assert mine["facts"]["contact_phone"]["value"] == "(918) 555-0142"

    theirs = service.handle({"action": "board", "session_id": session,
                             "coordinator": "sam"})["board"][0]
    assert theirs["masked"] is True
    assert theirs["facts"]["contact_name"]["value"] != "Dana R."


def test_board_row_carries_the_gallery_name(local_store):
    session = sid()
    start(session)
    row = service.handle({"action": "board", "session_id": session})["board"][0]
    assert row["gallery_name"] == "Single mother, two children"


# --- undo -------------------------------------------------------------------


def test_undo_inside_the_window_reverses_the_entry(local_store):
    session = sid()
    case_id = start(session)["case_id"]
    case = local_store.get(case_id)
    entry = Ledger.add(case, "filed snap", "the family asked", "withdraw the application")
    local_store.put(case)

    out = service.handle({"action": "undo", "session_id": session,
                          "case_id": case_id, "entry_id": entry.entry_id})
    assert out["ok"] is True
    rows = {e["entry_id"]: e for e in out["case"]["ledger"]}
    assert rows[entry.entry_id]["undone"] is True
    assert out["case"]["ledger"][-1]["action"] == "undo: filed snap"

    again = service.handle({"action": "undo", "session_id": session,
                            "case_id": case_id, "entry_id": entry.entry_id})
    assert again["ok"] is False


def test_undo_of_an_unknown_entry_fails_softly(local_store):
    session = sid()
    case_id = start(session)["case_id"]
    out = service.handle({"action": "undo", "session_id": session,
                          "case_id": case_id, "entry_id": "nope"})
    assert out["ok"] is False and out["case"] is None


# --- answer_card ------------------------------------------------------------


def test_answering_a_card_twice_is_refused(local_store):
    from benefitline import cards
    from benefitline.schemas import RiskReport

    session = sid()
    case_id = start(session)["case_id"]
    case = local_store.get(case_id)
    risk = RiskReport(escalate=True, reasons=["a parent has no status (m6)"],
                      question_for_coordinator="File for the children only?",
                      programs_safe_to_file=["snap"], programs_held=["medicaid"])
    card = cards.make_card(case, risk, store=local_store)

    first = service.handle({"action": "answer_card", "session_id": session,
                            "case_id": case_id, "card_id": card.card_id,
                            "option": cards.DEFAULT_OPTION})
    assert first["ok"] is True
    answered = next(c for c in first["case"]["cards"] if c["card_id"] == card.card_id)
    assert answered["answered"] == cards.DEFAULT_OPTION

    second = service.handle({"action": "answer_card", "session_id": session,
                             "case_id": case_id, "card_id": card.card_id,
                             "option": cards.DEFAULT_OPTION})
    assert second["ok"] is False and "already answered" in second["error"]


# --- reset ------------------------------------------------------------------


def test_reset_removes_only_this_session(local_store):
    mine, theirs = sid(), sid()
    my_case = start(mine)["case_id"]
    their_case = start(theirs)["case_id"]

    out = service.handle({"action": "reset", "session_id": mine})
    assert out["ok"] is True

    left = [c["case_id"] for c in service.handle({"action": "board",
                                                  "session_id": theirs})["board"]]
    assert my_case not in left
    assert their_case in left


# --- the demo path, with the model -----------------------------------------


@pytest.mark.live
def test_case1_script_reaches_the_ground_truth_snap_number():
    """Case 1 played end to end: the script answers whatever the agent asked next."""
    scripts = json.loads((ROOT / "gallery" / "scripts.json").read_text(encoding="utf-8"))
    script = scripts["cases"][CASE1]
    replies = {r["asks"]: r["en"] for r in script["replies"]}
    session = sid()
    case_id = start(session)["case_id"]

    message = script["opening"]["en"]
    result = None
    for _ in range(14):
        result = service.handle({"action": "chat", "session_id": session,
                                 "case_id": case_id, "message": message, "lang": "en"})
        assert result["ok"] is True, result["error"]
        if result["case"]["engine_result"]:
            break
        asks = result["case"]["transcript"][-1].get("asks") or ""
        assert asks in replies, f"the agent asked for {asks!r}, which the script does not answer"
        message = replies[asks]

    assert result["case"]["engine_result"], "the script ended before the engine ran"
    programs = {p["key"]: p for p in result["case"]["engine_result"]["programs"]}
    truth = json.loads((ROOT / "gallery" / "ground_truth.json").read_text(encoding="utf-8"))
    expected = truth["cases"][CASE1]["programs"]["snap"]
    assert programs["snap"]["amount"] == expected["amount"] == 632.0
    assert programs["snap"]["unit"] == "USD/month"


def test_claiming_one_case_leaves_the_other_masked(local_store):
    """A claim unmasks that case only: the sibling row on the same board stays masked."""
    session = sid()
    mine = start(session, CASE1)["case_id"]
    other = start(session, "case4_mixed_status")["case_id"]
    with_contact(local_store, mine)
    with_contact(local_store, other)

    claimed = service.handle({"action": "claim", "session_id": session,
                              "case_id": mine, "coordinator": "maria"})
    assert claimed["ok"] is True

    rows = {r["case_id"]: r for r in service.handle(
        {"action": "board", "session_id": session, "coordinator": "maria"})["board"]}
    assert rows[mine]["masked"] is False
    assert rows[mine]["facts"]["contact_name"]["value"] == "Dana R."
    assert rows[other]["masked"] is True
    assert rows[other]["facts"]["contact_name"]["value"] == MASK
    assert rows[other]["facts"]["contact_phone"]["value"] != "(918) 555-0142"


def test_the_board_never_echoes_what_the_family_typed_about_themselves(local_store):
    """Name and address typed into a transcript turn are scrubbed while unclaimed."""
    session = sid()
    case_id = start(session)["case_id"]
    case = local_store.get(case_id)
    case.facts.contact_name = Fact(value="Dana Reyes", source_msg_id="m1", quote="Dana Reyes")
    case.facts.address = Fact(value="1412 NW 9th St, Tulsa OK", source_msg_id="m1", quote="")
    case.transcript.append({"id": "m1", "role": "family", "tier": 0,
                            "text": "This is Dana Reyes at 1412 NW 9th St, Tulsa OK",
                            "at": "2026-09-13T21:00:00+00:00"})
    local_store.put(case)

    anonymous = json.dumps(service.handle({"action": "board", "session_id": session})["board"],
                           ensure_ascii=False)
    assert "Dana Reyes" not in anonymous
    assert "1412 NW 9th St" not in anonymous
    assert MASK in anonymous

    service.handle({"action": "claim", "session_id": session,
                    "case_id": case_id, "coordinator": "maria"})
    mine = json.dumps(service.handle({"action": "board", "session_id": session,
                                      "coordinator": "maria"})["board"], ensure_ascii=False)
    assert "Dana Reyes" in mine and "1412 NW 9th St" in mine


def test_a_specialist_defect_reaches_the_error_field(local_store, monkeypatch):
    """Only ImportError is survivable; a real failure surfaces instead of degrading."""
    from benefitline import specialists

    def boom(case, store=None):
        raise RuntimeError("a specialist is broken")

    monkeypatch.setattr(specialists, "run_specialists", boom)
    case = local_store.get(start(sid())["case_id"])
    with pytest.raises(RuntimeError):
        service._specialists(case, local_store)
