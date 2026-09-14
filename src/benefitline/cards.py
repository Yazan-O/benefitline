"""The Decision Card: the only way a coordinator is interrupted.

One tap answers it. A repeated answer becomes a standing rule in the org rules, so
the same question is not asked twice. A per-week interrupt budget forces ranking:
when the budget is spent, only deadline-driven cards surface and the rest are
auto-answered with their default and written to the ledger.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .ledger import Ledger, now_utc
from .schemas import CaseRecord, DecisionCard, RiskReport
from .store import Store

OPTIONS: list[dict] = [
    {
        "label": "File children's programs now, hold the parents' question for the coordinator",
        "consequence": "The children's benefits start on time; the parents' part waits for a human.",
    },
    {
        "label": "Hold everything until I call the family",
        "consequence": "Nothing is filed, and any deadline on this case keeps running.",
    },
    {
        "label": "Refer to legal aid",
        "consequence": "The case leaves the queue and goes to a legal aid partner today.",
    },
]
DEFAULT_OPTION: str = OPTIONS[0]["label"]

_PATTERN_PREFIX = "pattern:"
_AUTO_PREFIX = "auto:"   # marks a card the agent answered itself; it interrupted nobody


def risk_pattern_key(risk: RiskReport) -> str:
    """A key that repeats across households, so a standing rule can match a later case.

    Built only from the shape of the risk (does it escalate, which programs were held),
    never from the reason text, which cites message ids unique to one case.
    """
    held = ",".join(sorted(risk.programs_held)) or "none"
    safe = ",".join(sorted(risk.programs_safe_to_file)) or "none"
    return f"escalate={str(risk.escalate).lower()}|held={held}|safe={safe}"


def _earliest_deadline(case: CaseRecord) -> Optional[str]:
    dates = [p.deadline for p in case.filing_plans if p.deadline]
    return min(dates) if dates else None


def _situation(case: CaseRecord, risk: RiskReport) -> str:
    size = case.facts.household_size.value if case.facts.household_size else "unknown"
    state = case.facts.state.value if case.facts.state else "unknown"
    held = ", ".join(risk.programs_held) or "none"
    safe = ", ".join(risk.programs_safe_to_file) or "none"
    line1 = f"Household of {size} in {state}: {safe} are safe to file, {held} are held."
    line2 = risk.question_for_coordinator or (
        risk.reasons[0] if risk.reasons else "The risk specialist asked for a human."
    )
    return f"{line1}\n{line2}"


def _engine_lines(case: CaseRecord, risk: RiskReport) -> list[str]:
    """One citation per program the risk names, taken from the engine's own output."""
    if case.engine_result is None:
        return []
    named = set(risk.programs_held) | set(risk.programs_safe_to_file)
    lines: list[str] = []
    for program in case.engine_result.programs:
        if program.key not in named:
            continue
        rule = program.rules[0] if program.rules else ""
        amount = f"{program.amount} {program.unit}" if program.amount is not None else "no amount"
        lines.append(f"engine: {program.key} eligible={program.eligible} {amount} {rule}".strip())
    return lines


def week_start(now: Optional[datetime] = None) -> datetime:
    """Monday 00:00 UTC of the week containing `now`."""
    at = now or now_utc()
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    monday = at - timedelta(days=at.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def was_auto_answered(card: DecisionCard) -> bool:
    """True when the agent answered the card itself, so no human was interrupted."""
    return any(line.startswith(_AUTO_PREFIX) for line in card.evidence)


def interrupt_budget(store: Store, per_week: int = 5, now: Optional[datetime] = None) -> int:
    """How many Decision Cards may still interrupt a coordinator this week.

    Only cards that actually reached a human count. A card answered by a standing rule
    or by the spent-budget default never interrupted anyone, so it is not charged.
    """
    start = week_start(now)
    used = 0
    for case in store.list():
        for card in case.cards:
            if was_auto_answered(card):
                continue
            created = card.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= start:
                used += 1
    return max(0, per_week - used)


def make_card(
    case: CaseRecord,
    risk: RiskReport,
    store: Optional[Store] = None,
    per_week: int = 5,
    now: Optional[datetime] = None,
) -> DecisionCard:
    """Build the one card for this case and attach it to the record.

    With a store in hand, a standing rule answers it silently, and an exhausted
    interrupt budget auto-answers any card that carries no deadline.
    """
    at = now or now_utc()
    deadline = _earliest_deadline(case)
    evidence = list(risk.reasons) + _engine_lines(case, risk)
    evidence.append(f"{_PATTERN_PREFIX}{risk_pattern_key(risk)}")
    card = DecisionCard(
        card_id=uuid.uuid4().hex[:12],
        case_id=case.case_id,
        situation=_situation(case, risk),
        options=[dict(o) for o in OPTIONS],
        default=DEFAULT_OPTION,
        deadline=deadline,
        evidence=evidence,
        created_at=at,
    )
    case.cards.append(card)

    if store is None:
        return card

    if apply_standing_rules(case, risk, store, card=card, now=at) is None:
        if deadline is None and interrupt_budget(store, per_week=per_week, now=at) <= 0:
            card.answered = card.default
            card.evidence.append(f"{_AUTO_PREFIX}budget-default")
            Ledger.add(
                case,
                action=f"answered card {card.card_id} with the default",
                why=(f"interrupt budget of {per_week} cards for this week is spent and the "
                     "card has no deadline"),
                undo=f"clear the answer on card {card.card_id} and surface it to the coordinator",
                is_read=False,
                now=at,
            )
    store.put(case)   # a surfaced card must be visible to the budget straight away
    return card


def answer_card(
    case: CaseRecord,
    card_id: str,
    option: str,
    make_standing_rule: bool,
    store: Store,
    now: Optional[datetime] = None,
) -> CaseRecord:
    """Record the coordinator's tap, ledger it, and optionally store a standing rule."""
    at = now or now_utc()
    card = next((c for c in case.cards if c.card_id == card_id), None)
    if card is None:
        raise KeyError(f"no card {card_id} on case {case.case_id}")
    labels = [o.get("label") for o in card.options]
    if option not in labels:
        raise ValueError(f"option {option!r} is not one of {labels}")
    if card.answered is not None:
        raise ValueError(
            f"card {card_id} was already answered with {card.answered!r}; "
            "undo that answer before recording another"
        )

    card.answered = option
    card.make_standing_rule = make_standing_rule
    Ledger.add(
        case,
        action=f"answered card {card_id}",
        why=f"coordinator chose: {option}",
        undo=f"clear the answer on card {card_id} and re-open it",
        is_read=False,
        now=at,
    )

    if make_standing_rule:
        pattern = _card_pattern(card)
        if pattern:
            store.add_rule({"when": pattern, "then": option})

    store.put(case)
    return case


def _card_pattern(card: DecisionCard) -> Optional[str]:
    for line in card.evidence:
        if line.startswith(_PATTERN_PREFIX):
            return line[len(_PATTERN_PREFIX) :]
    return None


def apply_standing_rules(
    case: CaseRecord,
    risk: RiskReport,
    store: Store,
    card: Optional[DecisionCard] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Auto-answer a card when a stored rule matches its risk pattern.

    `card` names the card to answer; without it the newest open card is used.
    Returns the option that answered it, or None when no rule matched.
    """
    at = now or now_utc()
    key = risk_pattern_key(risk)
    match = next((r for r in store.rules() if r.get("when") == key), None)
    if match is None:
        return None
    option = match.get("then")
    if not option:
        return None
    if card is None:
        card = next((c for c in reversed(case.cards) if c.answered is None), None)
    if card is None or card.answered is not None:
        return None
    card.answered = option
    card.evidence.append(f"{_AUTO_PREFIX}standing-rule")
    Ledger.add(
        case,
        action=f"answered card {card.card_id} by standing rule",
        why=f"a standing rule for {key} says: {option}",
        undo=f"clear the answer on card {card.card_id} and delete the standing rule for {key}",
        is_read=False,
        now=at,
    )
    store.put(case)
    return option
