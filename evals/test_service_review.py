"""Independent review of the runtime service and the deploy path (phase 4).

Three groups:

* contract  - `service.handle` payload validation, response shape, masking, ownership.
              No model, no network. Tests named `test_defect_*` are failing repros of
              defects in `src/benefitline/service.py`, which this review may not edit.
* runtime   - starts `python src/app.py` on :8080 and calls it over HTTP.
* live      - marked `live`: plays gallery scripts through the real models.

    python -m pytest evals/test_service_review.py -q -m "not live"
    python -m pytest evals/test_service_review.py -q -m live
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from benefitline import service  # noqa: E402
from benefitline.hooks import MASK  # noqa: E402
from benefitline.ledger import Ledger  # noqa: E402
from benefitline.schemas import Fact, RiskReport  # noqa: E402
from benefitline.store import CASE_ID_PATTERN, LocalJsonStore  # noqa: E402

CASE1 = "case1_single_mother_two_kids"
CASE2 = "case2_couple_newborn"
CASE3 = "case3_senior_alone"
CASE3B = "case3b_senior_alone_with_medical"
CASE4 = "case4_mixed_status"
KEYS = {"ok", "case", "board", "gallery", "error"}
SCRIPTS = json.loads((ROOT / "gallery" / "scripts.json").read_text(encoding="utf-8"))
TRUTH = json.loads((ROOT / "gallery" / "ground_truth.json").read_text(encoding="utf-8"))


def sid() -> str:
    return f"{uuid.uuid4()}-benefitline"


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("BENEFITLINE_TABLE", raising=False)
    monkeypatch.setenv("BENEFITLINE_DATA_DIR", str(tmp_path / "cases"))
    return LocalJsonStore(tmp_path / "cases")


def start(session: str, gallery_case: str = CASE1) -> dict:
    payload = {"action": "start_case", "session_id": session}
    if gallery_case:
        payload["gallery_case"] = gallery_case
    out = service.handle(payload)
    assert out["ok"] is True, out["error"]
    return out["case"]


# --- check 1: contract ------------------------------------------------------


DESIGN_ACTIONS = ("chat", "start_case", "board", "claim", "answer_card",
                  "undo", "gallery", "reset")


def test_every_design_action_is_handled():
    """DESIGN.md line 31 lists eight actions; each one is routed, none 'unknown'."""
    for action in DESIGN_ACTIONS:
        out = service.handle({"action": action, "session_id": sid()})
        assert set(out) == KEYS
        assert "unknown action" not in (out["error"] or ""), action


@pytest.mark.parametrize("payload", [
    {"action": "drop_table", "session_id": "x" * 40},
    {"action": None, "session_id": "x" * 40},
    {"action": "gallery", "session_id": "short"},
    {"action": "gallery"},
    {"action": "gallery", "session_id": 12345678901234567890123456789012345},
    {"action": "chat", "session_id": "x" * 40, "message": 42},
    {"action": "chat", "session_id": "x" * 40, "message": {"a": 1}},
    {"action": "chat", "session_id": "x" * 40, "case_id": 7},
    {"action": "claim", "session_id": "x" * 40, "coordinator": []},
    {"action": "undo", "session_id": "x" * 40, "entry_id": 3},
    "not a dict",
    None,
    [1, 2, 3],
])
def test_bad_payloads_answer_ok_false_in_the_five_key_shape(payload):
    out = service.handle(payload)
    assert set(out) == KEYS
    assert out["ok"] is False
    assert isinstance(out["error"], str) and out["error"]


def test_the_five_keys_hold_on_the_exception_path(monkeypatch):
    """A handler that raises still answers in the same shape, with the reason."""
    def boom(payload, store):
        raise RuntimeError("a handler is broken")

    monkeypatch.setitem(service._HANDLERS, "gallery", boom)
    out = service.handle({"action": "gallery", "session_id": sid()})
    assert set(out) == KEYS
    assert out["ok"] is False
    assert out["error"] == "RuntimeError: a handler is broken"


def test_defect_a_store_failure_escapes_handle(monkeypatch):
    """`store = get_store()` sits outside the try (service.py:479-481), so a store
    defect raises out of handle() instead of answering ok:false."""
    def boom():
        raise RuntimeError("dynamodb is unreachable")

    monkeypatch.setattr(service, "get_store", boom)
    out = service.handle({"action": "gallery", "session_id": sid()})
    assert set(out) == KEYS and out["ok"] is False


def test_case_datetimes_serialize_as_iso_strings(local_store):
    session = sid()
    case_id = start(session)["case_id"]
    case = local_store.get(case_id)
    Ledger.add(case, "filed snap", "the family asked", "withdraw it")
    local_store.put(case)
    row = service.handle({"action": "board", "session_id": session})["board"][0]
    for value in (row["updated_at"], row["ledger"][0]["at"], row["ledger"][-1]["at"]):
        assert isinstance(value, str)
        datetime.fromisoformat(value)
    fetched = service.handle({"action": "claim", "session_id": session,
                              "case_id": case_id, "coordinator": "maria"})["case"]
    assert isinstance(fetched["updated_at"], str)
    json.dumps(fetched)  # a datetime object would raise here


def test_case_ids_match_the_store_pattern():
    session = sid()
    for gallery_case in (None, CASE1, CASE4):
        case = start(session, gallery_case)
        assert CASE_ID_PATTERN.match(case["case_id"]), case["case_id"]


def _stuff_identifiers(store, case_id, name="Dana Reyes", phone="(918) 555-0142",
                       address="1412 NW 9th St, Tulsa OK"):
    """Put the three masked fields everywhere a board row can carry prose."""
    from benefitline.schemas import DecisionCard, Explanation, FilingPlan, FormField

    case = store.get(case_id)
    case.facts.contact_name = Fact(value=name, source_msg_id="m1", quote=name)
    case.facts.contact_phone = Fact(value=phone, source_msg_id="m1", quote="")
    case.facts.address = Fact(value=address, source_msg_id="m1", quote="")
    case.transcript.append({"id": "m1", "role": "family", "tier": 0,
                            "text": f"This is {name} at {address}, call {phone}",
                            "at": "2026-09-13T21:00:00+00:00"})
    case.explanation = Explanation(language="en",
                                   message_to_family=f"{name}, you qualify for SNAP.",
                                   per_program=[{"key": "snap", "one_line": f"for {name}",
                                                 "rule_url": "https://example.org"}])
    case.filing_plans = [FilingPlan(program="snap", form_name="OK SNAP", form_url="u",
                                    fields=[FormField(form_field="Applicant",
                                                      value=name, source_msg_id="m1"),
                                            FormField(form_field="Address",
                                                      value=address, source_msg_id="m1")],
                                    documents_needed=[f"photo id for {name}"])]
    case.cards = [DecisionCard(card_id="c1", case_id=case_id,
                               situation=f"{name} at {phone} has no status on file",
                               options=[{"label": "file", "consequence": "x"}],
                               default="file", evidence=[f"m1: {address}"],
                               created_at=datetime.now())]
    Ledger.add(case, action="filed snap", why=f"{name} confirmed at {phone}",
               undo="withdraw")
    store.put(case)
    return name, phone, address


def test_the_board_hides_every_identifier_from_a_non_claimer(local_store):
    session = sid()
    case_id = start(session)["case_id"]
    name, phone, address = _stuff_identifiers(local_store, case_id)

    for coordinator in (None, "sam"):
        payload = {"action": "board", "session_id": session}
        if coordinator:
            payload["coordinator"] = coordinator
        blob = json.dumps(service.handle(payload)["board"], ensure_ascii=False)
        assert name not in blob, f"the family name leaked to {coordinator!r}"
        assert phone not in blob, f"the phone leaked to {coordinator!r}"
        assert address not in blob, f"the address leaked to {coordinator!r}"
        assert MASK in blob


def test_the_claiming_coordinator_sees_only_their_own_case(local_store):
    session = sid()
    mine = start(session, CASE1)["case_id"]
    other = start(session, CASE4)["case_id"]
    name, phone, address = _stuff_identifiers(local_store, mine)
    _stuff_identifiers(local_store, other, "Omar K.", "(918) 555-0199",
                       "77 S Elgin Ave, Tulsa OK")

    assert service.handle({"action": "claim", "session_id": session,
                           "case_id": mine, "coordinator": "maria"})["ok"] is True
    rows = {r["case_id"]: r for r in service.handle(
        {"action": "board", "session_id": session, "coordinator": "maria"})["board"]}
    mine_blob = json.dumps(rows[mine], ensure_ascii=False)
    other_blob = json.dumps(rows[other], ensure_ascii=False)
    assert name in mine_blob and phone in mine_blob and address in mine_blob
    for leaked in ("Omar K.", "(918) 555-0199", "77 S Elgin Ave"):
        assert leaked not in other_blob, leaked


def test_reset_deletes_only_this_sessions_cases():
    mine, theirs = sid(), sid()
    my_case = start(mine)["case_id"]
    their_case = start(theirs, CASE4)["case_id"]
    assert service.handle({"action": "reset", "session_id": mine})["ok"] is True
    left = [c["case_id"] for c in service.handle({"action": "board",
                                                  "session_id": theirs})["board"]]
    assert my_case not in left and their_case in left


def test_chat_refuses_another_sessions_case():
    owner, attacker = sid(), sid()
    case_id = start(owner)["case_id"]
    out = service.handle({"action": "chat", "session_id": attacker,
                          "case_id": case_id, "message": "hello"})
    assert out["ok"] is False and "another session" in out["error"]


def test_defect_claim_reads_another_sessions_case_unmasked(local_store):
    """`_act_claim` (service.py:334) calls store.get directly, never `_load`, so any
    session that can read a case_id off the board takes the case and gets the full
    unmasked record back. `_act_chat` (service.py:286) does check."""
    owner, attacker = sid(), sid()
    case_id = start(owner)["case_id"]
    name, phone, _ = _stuff_identifiers(local_store, case_id)

    out = service.handle({"action": "claim", "session_id": attacker,
                          "case_id": case_id, "coordinator": "mallory"})
    assert out["ok"] is False, (
        f"another session claimed the case and read {name}/{phone} out of "
        f"{json.dumps(out['case'])[:200]}")


def test_defect_answer_card_writes_to_another_sessions_case(local_store):
    """`_act_answer_card` (service.py:355) never checks the session."""
    from benefitline import cards

    owner, attacker = sid(), sid()
    case_id = start(owner)["case_id"]
    case = local_store.get(case_id)
    card = cards.make_card(case, RiskReport(escalate=True, reasons=["m6: no status"],
                                            question_for_coordinator="File for the kids?",
                                            programs_safe_to_file=["snap"],
                                            programs_held=["medicaid"]),
                           store=local_store)
    out = service.handle({"action": "answer_card", "session_id": attacker,
                          "case_id": case_id, "card_id": card.card_id,
                          "option": cards.DEFAULT_OPTION})
    assert out["ok"] is False, "another session answered this case's decision card"


def test_defect_undo_reverses_another_sessions_ledger_row(local_store):
    """`_act_undo` (service.py:374) never checks the session."""
    owner, attacker = sid(), sid()
    case_id = start(owner)["case_id"]
    case = local_store.get(case_id)
    entry = Ledger.add(case, "filed snap", "the family asked", "withdraw it")
    local_store.put(case)
    out = service.handle({"action": "undo", "session_id": attacker,
                          "case_id": case_id, "entry_id": entry.entry_id})
    assert out["ok"] is False, "another session undid this case's filing"


def test_defect_board_ask_answers_with_seven_keys(local_store, monkeypatch):
    """`_act_board_ask` (service.py:432-435) adds `answer` and `denied` to the reply,
    so the response is not the five keys DESIGN.md line 36 fixes. The model is stubbed
    out; only the shape is under test."""
    import strands

    class _Stub:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            return "two cases are waiting"

    monkeypatch.setattr(strands, "Agent", _Stub)
    session = sid()
    start(session)
    out = service.handle({"action": "board_ask", "session_id": session,
                          "message": "what is on the board?"})
    assert set(out) == KEYS, f"extra keys: {sorted(set(out) - KEYS)}"


def test_board_ask_is_not_a_design_action():
    """`board_ask` is routed (service.py:442) but is not one of the eight actions in
    DESIGN.md line 31, and web/index.html never calls it."""
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "board_ask" not in page
    assert "board_ask" not in (ROOT / "src" / "DESIGN.md").read_text(encoding="utf-8")


# --- check 3: the local runtime ---------------------------------------------


PORT = 8080
BASE = f"http://127.0.0.1:{PORT}"
LAUNCHER = ROOT / "_runs" / "2026-09-13_phase4_deploy" / "run_app_on_port.py"


def _post(body, timeout=180, raw=None):
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/invocations", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _port_open(port: int = PORT) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def runtime(tmp_path_factory):
    """`python src/app.py` on :8080, killed at the end of the session.

    When 8080 is already taken (another session's runtime), the same app object is
    started on a free port instead; the entrypoint under test is identical.
    """
    global BASE
    if _port_open():
        port = _free_port()
        command = [sys.executable, str(LAUNCHER), str(port)]
    else:
        port = PORT
        command = [sys.executable, str(ROOT / "src" / "app.py")]
    BASE = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.pop("BENEFITLINE_TABLE", None)
    env["BENEFITLINE_DATA_DIR"] = str(tmp_path_factory.mktemp("runtime_cases"))
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(tmp_path_factory.mktemp("log") / "app.log", "w+", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    deadline = time.time() + 240          # importing policyengine_us alone takes ~40 s
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            pytest.fail(f"src/app.py exited early:\n{log.read()[-2000:]}")
        try:
            with urllib.request.urlopen(f"{BASE}/ping", timeout=2) as res:
                if res.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(1)
    if not ready:
        proc.kill()
        pytest.fail("src/app.py never answered /ping")
    yield proc
    proc.kill()
    proc.wait(timeout=30)


def test_runtime_ping(runtime):
    with urllib.request.urlopen(f"{BASE}/ping", timeout=10) as res:
        assert res.status == 200
        body = json.loads(res.read().decode("utf-8"))
    assert body.get("status") in ("Healthy", "HealthyBusy"), body


def test_runtime_gallery_action(runtime):
    status, text = _post({"action": "gallery", "session_id": sid()})
    assert status == 200
    out = json.loads(text)
    assert set(out) == KEYS and out["ok"] is True
    assert len(out["gallery"]) == 6
    snap = next(g for g in out["gallery"] if g["id"] == CASE1)["amounts"]["snap"]
    assert snap["amount"] == TRUTH["cases"][CASE1]["programs"]["snap"]["amount"] == 632.0


@pytest.mark.parametrize("name,raw", [
    ("not json", b"this is not json at all"),
    ("json array", b"[1,2,3]"),
    ("json string", b'"hello"'),
    ("empty body", b""),
    ("wrong types", json.dumps({"action": 9, "session_id": 9, "message": []}).encode()),
    ("unknown action", json.dumps({"action": "rm -rf", "session_id": "x" * 40}).encode()),
])
def test_runtime_malformed_bodies_never_return_a_stack_trace(runtime, name, raw):
    status, text = _post(None, raw=raw)
    assert status in (200, 400, 415, 422), f"{name}: http {status} / {text[:200]}"
    assert "Traceback" not in text and 'File "' not in text, f"{name} leaked a traceback"
    if status == 200:
        out = json.loads(text)
        assert set(out) == KEYS
        assert out["ok"] is False, f"{name} was accepted: {text[:200]}"


def test_runtime_survives_a_one_megabyte_message(runtime):
    """No case_id, so the request is refused before any model call; what is under test
    is that a 1 MB body comes back as JSON rather than a crash."""
    body = {"action": "chat", "session_id": sid(), "message": "x" * (1024 * 1024)}
    status, text = _post(body, timeout=120)
    assert status == 200, text[:200]
    out = json.loads(text)
    assert set(out) == KEYS and out["ok"] is False
    assert "Traceback" not in text


def test_runtime_five_parallel_sessions_do_not_mix(runtime):
    """Five start_case calls at once: five sessions, five case ids, no crossed rows."""
    sessions = [sid() for _ in range(5)]
    picks = [CASE1, CASE2, CASE3, CASE4, "case5_on_snap_recert_due"]

    def one(pair):
        session, pick = pair
        status, text = _post({"action": "start_case", "session_id": session,
                              "gallery_case": pick}, timeout=120)
        return session, pick, status, json.loads(text)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(one, zip(sessions, picks)))

    ids = set()
    for session, pick, status, out in results:
        assert status == 200 and out["ok"] is True, out
        case = out["case"]
        assert case["session_id"] == session
        assert case["gallery_case"] == pick
        assert case["case_id"].startswith(pick)
        ids.add(case["case_id"])
    assert len(ids) == 5


@pytest.mark.live
def test_runtime_five_parallel_chats_do_not_cross_talk(runtime):
    """Five sessions send their case's opening line at once through one process; the
    module-level IntakeAgent must not mix one household's facts into another."""
    picks = [CASE1, CASE2, CASE3, CASE4, "case5_on_snap_recert_due"]
    sessions = [sid() for _ in picks]
    started = []
    for session, pick in zip(sessions, picks):
        status, text = _post({"action": "start_case", "session_id": session,
                              "gallery_case": pick}, timeout=120)
        assert status == 200
        started.append(json.loads(text)["case"]["case_id"])

    def one(triple):
        session, pick, case_id = triple
        status, text = _post({"action": "chat", "session_id": session,
                              "case_id": case_id, "lang": "en",
                              "message": SCRIPTS["cases"][pick]["opening"]["en"]},
                             timeout=300)
        return pick, case_id, session, status, json.loads(text)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(one, zip(sessions, picks, started)))

    for pick, case_id, session, status, out in results:
        assert status == 200 and out["ok"] is True, out.get("error")
        case = out["case"]
        assert case["case_id"] == case_id and case["session_id"] == session
        assert case["gallery_case"] == pick
        family = [t["text"] for t in case["transcript"] if t["role"] == "family"]
        assert family == [SCRIPTS["cases"][pick]["opening"]["en"]], family


# --- check 2: the gallery scripts, with the models --------------------------


def _play(session, case_id, script, lang="en", turns=16):
    replies = {r["asks"]: r[lang] for r in script["replies"]}
    message = script["opening"][lang]
    result = None
    for _ in range(turns):
        result = service.handle({"action": "chat", "session_id": session,
                                 "case_id": case_id, "message": message, "lang": lang})
        assert result["ok"] is True, result["error"]
        if result["case"]["engine_result"]:
            return result
        asks = result["case"]["transcript"][-1].get("asks") or ""
        if asks not in replies:
            pytest.fail(f"the agent asked for {asks!r}; the script answers "
                        f"{sorted(replies)}")
        message = replies[asks]
    pytest.fail("the script ran out of turns before the engine ran")


@pytest.mark.live
def test_live_case1_files_with_the_ground_truth_number():
    session = sid()
    case_id = start(session, CASE1)["case_id"]
    out = _play(session, case_id, SCRIPTS["cases"][CASE1])
    case = out["case"]
    programs = {p["key"]: p for p in case["engine_result"]["programs"]}
    truth = TRUTH["cases"][CASE1]["programs"]["snap"]["amount"]
    assert programs["snap"]["amount"] == truth == 632.0
    assert programs["snap"]["unit"] == "USD/month"
    assert case["explanation"] and case["explanation"]["message_to_family"].strip()
    assert case["filing_plans"], "no filing plan was written"
    assert case["status"] == "filed", case["status"]
    assert [e for e in case["ledger"] if e["final_at"]], "no veto window on any row"
    assert set(out) == KEYS


@pytest.mark.live
def test_live_case4_escalates_with_one_card_that_can_be_answered_and_undone():
    from benefitline import cards

    session = sid()
    case_id = start(session, CASE4)["case_id"]
    out = _play(session, case_id, SCRIPTS["cases"][CASE4])
    case = out["case"]
    assert case["status"] == "escalated", case["status"]
    assert len(case["cards"]) == 1, [c["card_id"] for c in case["cards"]]
    card = case["cards"][0]

    answered = service.handle({"action": "answer_card", "session_id": session,
                               "case_id": case_id, "card_id": card["card_id"],
                               "option": cards.DEFAULT_OPTION})
    assert answered["ok"] is True, answered["error"]
    assert next(c for c in answered["case"]["cards"]
                if c["card_id"] == card["card_id"])["answered"] == cards.DEFAULT_OPTION

    rows = [e for e in answered["case"]["ledger"] if not e["undone"] and e["final_at"]]
    assert rows, "answering the card wrote no undoable ledger row"
    entry = rows[-1]
    undone = service.handle({"action": "undo", "session_id": session,
                             "case_id": case_id, "entry_id": entry["entry_id"]})
    assert undone["ok"] is True, undone["error"]
    by_id = {e["entry_id"]: e for e in undone["case"]["ledger"]}
    assert by_id[entry["entry_id"]]["undone"] is True
    assert undone["case"]["ledger"][-1]["action"].startswith("undo: ")


@pytest.mark.live
def test_live_case3_then_the_medical_answers_move_148_to_298():
    session = sid()
    case_id = start(session, CASE3)["case_id"]
    out = _play(session, case_id, SCRIPTS["cases"][CASE3])
    first = {p["key"]: p for p in out["case"]["engine_result"]["programs"]}
    assert first["snap"]["amount"] == TRUTH["cases"][CASE3]["programs"]["snap"]["amount"] == 148.0

    extra = SCRIPTS["cases"][CASE3B]["replies"]
    for key in ("medical_expenses", "health_premiums"):
        text = next(r["en"] for r in extra if r["asks"] == key)
        out = service.handle({"action": "chat", "session_id": session,
                              "case_id": case_id, "message": text, "lang": "en"})
        assert out["ok"] is True, out["error"]
    after = {p["key"]: p for p in out["case"]["engine_result"]["programs"]}
    expected = TRUTH["cases"][CASE3B]["programs"]["snap"]["amount"]
    assert after["snap"]["amount"] == expected == 298.0


@pytest.mark.live
def test_live_case2_english_script_files_the_ground_truth_number():
    session = sid()
    case_id = start(session, CASE2)["case_id"]
    out = _play(session, case_id, SCRIPTS["cases"][CASE2])
    replies = [t for t in out["case"]["transcript"] if t["role"] == "agent"]
    assert replies, "the agent never replied"
    last = replies[-1]["text"]
    assert not re.search(r"[؀-ۿ]", last), f"the reply is not English: {last[:120]!r}"
    programs = {p["key"]: p for p in out["case"]["engine_result"]["programs"]}
    assert programs["snap"]["amount"] == TRUTH["cases"][CASE2]["programs"]["snap"]["amount"]
