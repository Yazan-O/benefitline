"""The action handlers behind the single HTTP entrypoint.

`handle(payload) -> dict` is the whole API. It imports no AgentCore SDK, so an eval
can call it directly; `src/app.py` is a thin wrapper that hands it the request body.

Every response has the same shape:

    {"ok": bool, "case": dict|None, "board": list|None, "gallery": list|None,
     "error": str|None}

Board masking runs through the same `MaskUntilClaimed` path the agent tools use, so
there is one place where a family's name, phone, and address can be revealed, and it
reads the claim from the stored case rather than from anything in the request.

The one non-design action, `board_ask`, answers in the same five keys: its prose
answer and the list of guard denials ride inside `case` as
`{"answer": str, "denied": [str, ...]}`.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import cards, engine, facts, hooks
from .intake import IntakeAgent
from .ledger import Ledger
from .schemas import CaseRecord, EngineResult, ProgramResult
from .store import DynamoStore, LocalJsonStore, Store, check_case_id, get_store

LOG = logging.getLogger(__name__)

MIN_SESSION_ID = 33
ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH_PATH = ROOT / "gallery" / "ground_truth.json"

# The picker copy. `gallery/cases.py` carries engine inputs only, so the family-facing
# label and the languages live here, worded exactly as `web/index.html` prints them.
GALLERY_META: list[dict] = [
    {
        "id": "case1_single_mother_two_kids",
        "name": "Single mother, two children",
        "description": "Tulsa. 30 hours a week at $14 an hour, rent $950.",
        "languages": ["en"],
    },
    {
        "id": "case2_couple_newborn",
        "name": "Couple with a newborn",
        "description": "Tulsa. One income, $32,000 a year, rent $1,050.",
        "languages": ["en"],
    },
    {
        "id": "case3_senior_alone",
        "name": "Senior living alone",
        "description": "71 years old, Social Security $1,250 a month, rent $650.",
        "languages": ["en"],
    },
    {
        "id": "case3b_senior_alone_with_medical",
        "name": "Senior living alone, with medical costs",
        "description": "The same senior, once $180 a month of medical costs and the $202.90 Medicare Part B premium are on the record.",
        "languages": ["en"],
    },
    {
        "id": "case4_mixed_status",
        "name": "Mixed-status family",
        "description": "Two citizen children, two parents without status. $26,000 a year.",
        "languages": ["en"],
    },
    {
        "id": "case5_on_snap_recert_due",
        "name": "Already on SNAP, renewal due",
        "description": "Norman. Parent and a 12-year-old, recertification in 12 days.",
        "languages": ["en"],
    },
]
GALLERY_IDS = [g["id"] for g in GALLERY_META]

ACTIONS = (
    "chat", "start_case", "board", "board_ask", "claim", "answer_card",
    "undo", "gallery", "reset",
)

_intake = IntakeAgent()


# --- response shape ---------------------------------------------------------


def _resp(ok: bool = True, case: Any = None, board: Any = None,
          gallery: Any = None, error: Optional[str] = None) -> dict:
    return {
        "ok": ok,
        "case": case.model_dump(mode="json") if isinstance(case, CaseRecord) else case,
        "board": board,
        "gallery": gallery,
        "error": error,
    }


def _fail(message: str) -> dict:
    return _resp(ok=False, error=message)


# --- helpers ----------------------------------------------------------------


def _ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.exists():
        raise FileNotFoundError(
            f"gallery ground truth not found at {GROUND_TRUTH_PATH}; the picker never "
            "invents an amount. Ship gallery/ground_truth.json with the runtime."
        )
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))


def _mint_case_id(gallery_case: Optional[str]) -> str:
    stem = (gallery_case or "case")[:60]
    return check_case_id(f"{stem}-{uuid.uuid4().hex[:8]}")


def _load(store: Store, case_id: str, session_id: str) -> CaseRecord:
    case = store.get(check_case_id(case_id))
    if case is None:
        raise KeyError(f"no case {case_id}")
    if case.session_id != session_id:
        raise PermissionError(f"case {case_id} belongs to another session")
    return case


def _engine_result(raw: dict) -> EngineResult:
    """`engine.compute` returns a dict keyed by program; the record holds a list."""
    programs = [
        ProgramResult(
            key=key,
            label=value.get("label", key),
            eligible=value.get("eligible"),
            amount=value.get("amount"),
            unit=value.get("unit"),
            eligible_members=value.get("eligible_members", []) or [],
            rules=value.get("rules", []) or [],
            note=value.get("note", "") or "",
        )
        for key, value in raw.get("programs", {}).items()
    ]
    return EngineResult(
        engine=raw["engine"],
        engine_version=raw["engine_version"],
        year=raw["year"],
        state=raw["state"],
        programs=programs,
        snap_detail=raw.get("snap_detail", {}),
        members=raw.get("members", []),
    )


def _say(case: CaseRecord, text: str, tier: int = 0) -> None:
    """Append one agent reply to the transcript and charge it to a tier."""
    case.transcript.append({
        "id": f"m{len(case.transcript) + 1}",
        "role": "agent",
        "text": text,
        "at": datetime.now(timezone.utc).isoformat(),
        "tier": tier,
    })
    case.tier_counts[str(tier)] = case.tier_counts.get(str(tier), 0) + 1


def _specialists(case: CaseRecord, store: Store) -> CaseRecord:
    """Run the specialist graph. A missing module is survivable; a defect is not.

    An ImportError means the graph is not built in this checkout: the case is ledgered
    and answered. Anything else is a defect inside a specialist, so it is logged and
    re-raised; `handle()` turns it into `ok: false` with the exception in `error`,
    which is what makes it visible in dev instead of silently degrading the answer.
    """
    try:
        from .specialists import run_specialists
    except ImportError as exc:
        Ledger.add(
            case,
            action="specialists unavailable",
            why=f"could not import benefitline.specialists ({type(exc).__name__}: {exc})",
            undo="nothing to undo; no specialist wrote to this case",
            is_read=True,
        )
        case.status = "computed"
        store.put(case)
        return case
    try:
        case = run_specialists(case, store)
    except Exception:
        LOG.exception("the specialist graph failed on case %s", case.case_id)
        raise
    if case.status in ("intake", "ready_for_engine"):
        case.status = "computed"
    store.put(case)
    return case


def _board_rows(store: Store, coordinator: Optional[str]) -> list[dict]:
    """Every case on the board, masked unless this coordinator claimed that case."""
    names = {g["id"]: g["name"] for g in GALLERY_META}
    rows: list[dict] = []
    for case in store.list():
        row = case.model_dump(mode="json")
        row["gallery_name"] = names.get(case.gallery_case or "", case.case_id)
        row["masked"] = not (coordinator and case.claimed_by == coordinator)
        rows.append(row)
    masker = hooks.MaskUntilClaimed(store=store)
    resolver = hooks._ClaimResolver(store, coordinator)
    # _mask_tree blanks the three identifier fields per row and, on every other string
    # it meets (transcript text, an explanation, a filing plan), replaces the stored
    # name, phone and address of any case this caller has not claimed.
    return masker._mask_tree(rows, resolver, None)


def _mask_answer(text: str, store: Store, coordinator: Optional[str]) -> str:
    """The same value scrubbing, applied to prose the board agent wrote."""
    masker = hooks.MaskUntilClaimed(store=store)
    resolver = hooks._ClaimResolver(store, coordinator)
    return masker._mask_text(text, resolver, None)


def _delete(store: Store, case: CaseRecord) -> None:
    """The Store protocol has no delete; each backend gets its own removal."""
    if isinstance(store, LocalJsonStore):
        path = store.dir / f"{case.case_id}.json"
        if path.exists():
            path.unlink()
        return
    if isinstance(store, DynamoStore):
        store.table.delete_item(Key={"case_id": case.case_id})
        return
    case.status = "closed"  # an unknown backend: closed cases drop off the board
    store.put(case)


# --- actions ----------------------------------------------------------------


def _act_gallery(payload: dict, store: Store) -> dict:
    truth = _ground_truth()
    out = []
    for meta in GALLERY_META:
        row = dict(meta)
        row["fictional"] = True
        known = truth.get("cases", {}).get(meta["id"], {})
        row["engine"] = truth.get("engine", "")
        row["engine_version"] = truth.get("engine_version", "")
        row["amounts"] = {
            key: {"amount": value.get("amount"), "unit": value.get("unit"),
                  "eligible": value.get("eligible")}
            for key, value in known.get("programs", {}).items()
        }
        out.append(row)
    return _resp(gallery=out)


def _act_start_case(payload: dict, store: Store) -> dict:
    gallery_case = payload.get("gallery_case")
    if gallery_case is not None and gallery_case not in GALLERY_IDS:
        return _fail(f"unknown gallery case {gallery_case!r}")
    case = CaseRecord(
        case_id=_mint_case_id(gallery_case),
        session_id=payload["session_id"],
        gallery_case=gallery_case,
    )
    lang = payload.get("lang")
    if lang in ("en", "ar"):
        case.facts.language = lang
    Ledger.add(
        case,
        action=f"opened case {case.case_id}",
        why=f"gallery case {gallery_case or 'blank'} started from the demo page",
        undo="close the case; nothing has been filed yet",
        is_read=True,
    )
    store.put(case)
    return _resp(case=case)


def _act_chat(payload: dict, store: Store) -> dict:
    case_id = payload.get("case_id")
    if not case_id:
        return _fail("chat needs a case_id; call start_case first")
    case = _load(store, case_id, payload["session_id"])
    message = payload.get("message", "")
    if not message.strip():
        return _fail("chat needs a message")
    if payload.get("lang") in ("en", "ar") and not case.facts.language:
        case.facts.language = payload["lang"]

    case = _intake.step(case, message)
    store.put(case)

    if facts.missing_facts(case.facts):
        return _resp(case=case)

    try:
        raw = engine.compute(facts.to_household(case.facts, case.case_id))
    except engine.EngineInputError as exc:
        # The engine names the fact it needs; the family sees that sentence and nothing
        # is priced. No re-ask loop: the next family message runs intake again.
        _say(case, str(exc), tier=0)
        case.status = "intake"
        Ledger.add(
            case,
            action="held the engine",
            why=f"engine input check: {exc}",
            undo="nothing to undo; no number was produced",
            is_read=True,
        )
        store.put(case)
        return _resp(case=case)

    case.engine_result = _engine_result(raw)
    case.status = "computed"
    store.put(case)
    case = _specialists(case, store)
    return _resp(case=case)


def _act_board(payload: dict, store: Store) -> dict:
    return _resp(board=_board_rows(store, payload.get("coordinator")))


def _act_claim(payload: dict, store: Store) -> dict:
    coordinator = payload.get("coordinator")
    if not isinstance(coordinator, str) or not coordinator.strip():
        return _fail("claim needs a coordinator")
    case_id = payload.get("case_id")
    if not case_id:
        return _fail("claim needs a case_id")
    # The board is shared and masked, but acting on a case is the owning session's
    # right alone: _load refuses a case another browser started.
    case = _load(store, case_id, payload["session_id"])
    case.claimed_by = coordinator
    Ledger.add(
        case,
        action=f"claimed by {coordinator}",
        why="a coordinator took the case, which reveals the identifiers to them alone",
        undo="release the case, which masks the identifiers again",
        is_read=False,
    )
    store.put(case)
    return _resp(case=case, board=_board_rows(store, coordinator))


def _act_answer_card(payload: dict, store: Store) -> dict:
    case_id = payload.get("case_id")
    card_id = payload.get("card_id")
    option = payload.get("option")
    if not case_id or not card_id or not isinstance(option, str):
        return _fail("answer_card needs case_id, card_id, and option")
    case = _load(store, case_id, payload["session_id"])
    try:
        case = cards.answer_card(
            case, card_id, option,
            bool(payload.get("make_standing_rule", False)),
            store,
        )
    except (KeyError, ValueError) as exc:
        return _fail(str(exc).strip("'"))
    return _resp(case=case)


def _act_undo(payload: dict, store: Store) -> dict:
    case_id = payload.get("case_id")
    entry_id = payload.get("entry_id")
    if not case_id or not entry_id:
        return _fail("undo needs case_id and entry_id")
    case = _load(store, case_id, payload["session_id"])
    try:
        Ledger.undo(case, entry_id)
    except (KeyError, ValueError) as exc:
        return _fail(str(exc).strip("'"))
    store.put(case)
    return _resp(case=case)


def _act_reset(payload: dict, store: Store) -> dict:
    session_id = payload["session_id"]
    for case in store.list():
        if case.session_id == session_id:   # another browser's cases are untouched
            _delete(store, case)
    return _resp()


def _act_board_ask(payload: dict, store: Store) -> dict:
    """The coordinator's own agent, with the board tools and the guards on it."""
    message = payload.get("message", "")
    if not message.strip():
        return _fail("board_ask needs a message")
    coordinator = payload.get("coordinator")

    from strands import Agent

    from . import board_tools, models

    denied: list[str] = []
    guards = hooks.guards(
        on_denied=lambda tool, reason: denied.append(f"{tool}: {reason}"),
        case_id=payload.get("case_id"),
        store=store,
        tier=1,
    )
    agent = Agent(
        model=models.cheap(),
        system_prompt=(
            "You are the coordinator's assistant on the Benefitline board. Answer from "
            "the tools only. Never state an eligibility amount that the engine did not "
            "produce, and never repeat a family's name, phone, or address that a tool "
            "returned masked."
        ),
        tools=[
            board_tools.board_list_cases,
            board_tools.board_get_case,
            board_tools.reveal_identifiers,
            board_tools.send_to_family,
            board_tools.send_to_board,
            board_tools.claim_case,
        ],
        hooks=guards.hooks,
        interventions=guards.interventions,
        callback_handler=None,
    )
    result = agent(message, invocation_state={"coordinator": coordinator})
    # The wire contract is exactly five keys, so the answer and any guard denials
    # ride inside `case` (typed dict|null) instead of widening the response.
    return _resp(
        case={
            "answer": _mask_answer(str(result), store, coordinator),
            "denied": denied,
        },
        board=_board_rows(store, coordinator),
    )


_HANDLERS = {
    "chat": _act_chat,
    "start_case": _act_start_case,
    "board": _act_board,
    "board_ask": _act_board_ask,
    "claim": _act_claim,
    "answer_card": _act_answer_card,
    "undo": _act_undo,
    "gallery": _act_gallery,
    "reset": _act_reset,
}


# --- entry ------------------------------------------------------------------


def handle(payload: dict) -> dict:
    """Validate the request body, run one action, and always answer in the same shape."""
    if not isinstance(payload, dict):
        return _fail("payload must be a JSON object")

    action = payload.get("action")
    if not isinstance(action, str) or action not in _HANDLERS:
        return _fail(f"unknown action {action!r}; expected one of {', '.join(sorted(ACTIONS))}")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or len(session_id) < MIN_SESSION_ID:
        return _fail(
            f"session_id must be a string of at least {MIN_SESSION_ID} characters "
            "(AgentCore rejects a shorter runtime session id)"
        )

    message = payload.get("message", "")
    if not isinstance(message, str):
        return _fail("message must be a string")

    for key in ("case_id", "card_id", "coordinator", "entry_id", "gallery_case", "option", "lang"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            return _fail(f"{key} must be a string")

    try:
        # Inside the try: building the store touches DynamoDB and can fail, and a
        # browser must get the structured error rather than a 500.
        store = get_store()
        return _HANDLERS[action](payload, store)
    except PermissionError as exc:
        return _fail(str(exc))
    except KeyError as exc:
        return _fail(str(exc).strip("'"))
    except ValueError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - the browser gets the reason, not a 500
        return _fail(f"{type(exc).__name__}: {exc}")
