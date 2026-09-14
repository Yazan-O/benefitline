"""Every action the agent takes is a row: what, why, when, and how to reverse it.

Writes sit behind a veto window (10 minutes) before they are final; reads are exempt
and carry no window. Undo is only allowed inside the window, and it appends its own
reversal row rather than deleting history.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .schemas import CaseRecord, LedgerEntry

VETO_MINUTES = 10


def now_utc() -> datetime:
    """Timezone-aware UTC. Every stored datetime in the ledger uses this."""
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Ledger:
    """Helpers over `CaseRecord.ledger`. Stateless; the case carries the rows."""

    @staticmethod
    def add(
        case: CaseRecord,
        action: str,
        why: str,
        undo: str,
        tier: Optional[int] = None,
        is_read: bool = False,
        denied: bool = False,
        now: Optional[datetime] = None,
    ) -> LedgerEntry:
        at = _aware(now or now_utc())
        entry = LedgerEntry(
            entry_id=uuid.uuid4().hex[:12],
            case_id=case.case_id,
            action=action,
            why=why,
            at=at,
            undo=undo,
            final_at=None if is_read else at + timedelta(minutes=VETO_MINUTES),
            tier=tier,
            denied=denied,
        )
        case.ledger.append(entry)
        case.updated_at = at
        return entry

    @staticmethod
    def pending(case: CaseRecord, now: Optional[datetime] = None) -> list[LedgerEntry]:
        """Rows still inside their veto window and not already undone."""
        at = _aware(now or now_utc())
        return [
            e
            for e in case.ledger
            if e.final_at is not None and not e.undone and at < _aware(e.final_at)
        ]

    @staticmethod
    def undo(case: CaseRecord, entry_id: str, now: Optional[datetime] = None) -> LedgerEntry:
        """Reverse a row inside its veto window; append the reversal as its own row."""
        at = _aware(now or now_utc())
        target = next((e for e in case.ledger if e.entry_id == entry_id), None)
        if target is None:
            raise KeyError(f"no ledger entry {entry_id} on case {case.case_id}")
        if target.undone:
            raise ValueError(f"ledger entry {entry_id} was already undone")
        if target.final_at is None:
            raise ValueError(f"ledger entry {entry_id} is a read and has nothing to undo")
        if at >= _aware(target.final_at):
            raise ValueError(
                f"veto window for {entry_id} closed at {_aware(target.final_at).isoformat()}"
            )
        target.undone = True
        return Ledger.add(
            case,
            action=f"undo: {target.action}",
            why=f"coordinator vetoed entry {entry_id} inside the {VETO_MINUTES} minute window",
            undo="nothing to undo; this row reverses another",
            tier=target.tier,
            is_read=True,
            now=at,
        )
