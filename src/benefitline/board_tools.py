"""The coordinator board tools. These are the calls the guard hooks watch.

Every tool reads and writes through `get_store()` and returns the Strands
ToolResult shape `{"status": ..., "content": [{"json": ...}]}`. Ledger rows for these
calls are written once, by the `LedgerWriter` hook, not by the tools themselves.
"""
from __future__ import annotations

from strands import tool

from .ledger import now_utc
from .store import get_store


def _ok(data) -> dict:
    return {"status": "success", "content": [{"json": data}]}


def _err(message: str) -> dict:
    return {"status": "error", "content": [{"text": message}]}


def _identifiers(case) -> dict:
    facts = case.facts
    return {
        "contact_name": facts.contact_name.value if facts.contact_name else None,
        "contact_phone": facts.contact_phone.value if facts.contact_phone else None,
        "address": facts.address.value if facts.address else None,
    }


@tool
def board_list_cases() -> dict:
    """List every case on the coordinator board with its status and contact fields."""
    store = get_store()
    rows = []
    for case in store.list():
        rows.append(
            {
                "case_id": case.case_id,
                "status": case.status,
                "claimed_by": case.claimed_by,
                "open_cards": sum(1 for c in case.cards if c.answered is None),
                **_identifiers(case),
            }
        )
    return _ok({"cases": rows})


@tool
def board_get_case(case_id: str) -> dict:
    """Read one case from the board.

    Args:
        case_id: the case to read.
    """
    store = get_store()
    case = store.get(case_id)
    if case is None:
        return _err(f"no case {case_id}")
    return _ok(
        {
            "case_id": case.case_id,
            "status": case.status,
            "claimed_by": case.claimed_by,
            "language": case.facts.language,
            "programs": [p.program for p in case.filing_plans],
            **_identifiers(case),
        }
    )


@tool
def reveal_identifiers(case_id: str) -> dict:
    """Reveal the family's name, phone, and address for a claimed case.

    Args:
        case_id: the case whose identifiers to reveal.
    """
    store = get_store()
    case = store.get(case_id)
    if case is None:
        return _err(f"no case {case_id}")
    return _ok({"case_id": case_id, **_identifiers(case)})


@tool
def send_to_family(case_id: str, text: str) -> dict:
    """Send a text message to the family on this case.

    Args:
        case_id: the case to message.
        text: the message body, in the family's language.
    """
    store = get_store()
    case = store.get(case_id)
    if case is None:
        return _err(f"no case {case_id}")
    at = now_utc()
    case.transcript.append(
        {"id": f"m{len(case.transcript) + 1}", "role": "agent", "text": text, "at": at.isoformat(), "tier": None}
    )
    store.put(case)
    return _ok({"case_id": case_id, "sent": True, "text": text})


@tool
def send_to_board(case_id: str, text: str) -> dict:
    """Post a note for the coordinators on this case.

    Args:
        case_id: the case to annotate.
        text: the note for the board.
    """
    store = get_store()
    case = store.get(case_id)
    if case is None:
        return _err(f"no case {case_id}")
    case.transcript.append(
        {"id": f"b{len(case.transcript) + 1}", "role": "board", "text": text,
         "at": now_utc().isoformat(), "tier": None}
    )
    store.put(case)
    return _ok({"case_id": case_id, "posted": True, "text": text})


@tool
def claim_case(case_id: str, coordinator: str) -> dict:
    """Claim a case, which unmasks its identifiers for that coordinator.

    Args:
        case_id: the case to claim.
        coordinator: the coordinator's id.
    """
    store = get_store()
    case = store.get(case_id)
    if case is None:
        return _err(f"no case {case_id}")
    case.claimed_by = coordinator
    store.put(case)
    return _ok({"case_id": case_id, "claimed_by": coordinator})
