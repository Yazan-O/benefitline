"""Guards enforced in code, not in prompts.

- `MaskUntilClaimed`: a family's name, phone, and address are dots on the board until
  the coordinator making the call is the one who claimed that case. The decision is
  per case, read from the stored `CaseRecord.claimed_by`, never from a per-call flag.
- `NoIdentifiersOut`: an intervention that denies an outbound call whose input carries
  an SSN or an A-number anywhere, nested values and split-across-arguments included.
- `TraceRedactor`: identifiers and the case's contact values removed from the log line.
- `LedgerWriter`: one ledger row per tool call, denied calls included.

Registration order matters. Strands runs `AfterToolCallEvent` callbacks in reverse
registration order, so the hook registered FIRST runs LAST and sees the final result.
`TraceRedactor` must therefore be registered before `MaskUntilClaimed`, or it logs the
result as it was before masking. `guards()` builds the list in the right order; use it
rather than assembling the hooks by hand.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Callable, Iterable, NamedTuple, Optional

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry
from strands.interventions import Deny, InterventionHandler, Proceed

MASK = "•••• (claim to reveal)"
MASKED_FIELDS = ("contact_name", "contact_phone", "address")
BOARD_PREFIX = "board_"
REVEAL_TOOL = "reveal_identifiers"
OUTBOUND_TOOLS = ("send_to_family", "send_to_board")

# An identifier is still an identifier when it is typed with spaces, dots, unicode
# dashes, or nothing between the groups, so the separator is optional everywhere.
# The 3/2/4 grouping keeps phone numbers (3/3/4) out; `\d` is unicode-aware, so
# Arabic-Indic digits match too.
_SEP = r"[-.‐-―\s]?"
SSN_PATTERN = re.compile(rf"\b\d{{3}}{_SEP}\d{{2}}{_SEP}\d{{4}}\b")
ANUMBER_PATTERN = re.compile(r"\bA[-#.‐-―\s]?\d{8,9}\b")
CONTACT_MASK = "[REDACTED-CONTACT]"

logger = logging.getLogger("benefitline.trace")

UNDO_TEXT: dict[str, str] = {
    "send_to_family": "send a correction message to the family",
    "send_to_board": "delete the board note",
    "claim_case": "release the case, which masks the identifiers again",
    "reveal_identifiers": "nothing to undo; the reveal is logged",
    "board_get_case": "nothing to undo; this was a read",
    "board_list_cases": "nothing to undo; this was a read",
}
READ_TOOLS = ("board_get_case", "board_list_cases", "reveal_identifiers")


def _tool_name(event: Any) -> str:
    tool_use = getattr(event, "tool_use", None) or {}
    return tool_use.get("name", "") if isinstance(tool_use, dict) else ""


def _tool_input(event: Any) -> dict:
    tool_use = getattr(event, "tool_use", None) or {}
    value = tool_use.get("input", {}) if isinstance(tool_use, dict) else {}
    return value if isinstance(value, dict) else {}


def _coordinator(event: Any) -> Optional[str]:
    """The caller's coordinator id, the only thing a claim can be matched against."""
    state = getattr(event, "invocation_state", None) or {}
    value = state.get("coordinator")
    return value if isinstance(value, str) and value else None


def redact(text: str) -> str:
    """Replace any SSN or A-number in `text` with a marker."""
    text = SSN_PATTERN.sub("[REDACTED-SSN]", text)
    return ANUMBER_PATTERN.sub("[REDACTED-ANUMBER]", text)


def _string_leaves(node: Any) -> Iterable[str]:
    """Every string anywhere in a nested structure, in order."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _string_leaves(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _string_leaves(item)


def _identifier_values(case: Any) -> list[str]:
    """The three masked values as stored on a case, longest first."""
    facts = getattr(case, "facts", None)
    if facts is None:
        return []
    values = []
    for field in MASKED_FIELDS:
        fact = getattr(facts, field, None)
        if fact is not None and isinstance(fact.value, str) and fact.value.strip():
            values.append(fact.value)
    return sorted(values, key=len, reverse=True)


class _ClaimResolver:
    """Answers 'may this caller see case X unmasked?', reading each case once."""

    def __init__(self, store: Any, coordinator: Optional[str]) -> None:
        self.store = store
        self.coordinator = coordinator
        self._cases: dict[str, Any] = {}
        self._unclaimed: dict[Optional[str], list[Any]] = {}

    def case(self, case_id: Optional[str]):
        if not case_id or not isinstance(case_id, str):
            return None
        if case_id not in self._cases:
            try:
                self._cases[case_id] = self.store.get(case_id)
            except (ValueError, OSError):  # a bad or unreadable id stays masked
                self._cases[case_id] = None
        return self._cases[case_id]

    def claimed(self, case_id: Optional[str]) -> bool:
        if not self.coordinator:
            return False
        case = self.case(case_id)
        return case is not None and case.claimed_by == self.coordinator

    def unclaimed_cases(self, case_id: Optional[str]) -> list[Any]:
        """Cases whose values must be scrubbed from free text."""
        if case_id in self._unclaimed:
            return self._unclaimed[case_id]
        if case_id:
            case = self.case(case_id)
            cases = [case] if case is not None else []
        else:
            try:
                cases = list(self.store.list())
            except (ValueError, OSError):
                cases = []
        out = [c for c in cases if not self.coordinator or c.claimed_by != self.coordinator]
        self._unclaimed[case_id] = out
        return out


class MaskUntilClaimed(HookProvider):
    """Identifiers unmask only for the coordinator who claimed that particular case.

    The caller identifies itself with `invocation_state["coordinator"]`; the claim
    itself lives on the stored case (`CaseRecord.claimed_by`, set by `claim_case`).
    A claim on one case therefore reveals nothing about any other case.
    """

    def __init__(self, on_denied: Optional[Callable[[str, str], None]] = None,
                 store: Any = None) -> None:
        self.on_denied = on_denied
        self._store = store

    @property
    def store(self):
        if self._store is None:
            from .store import get_store

            self._store = get_store()
        return self._store

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        if _tool_name(event) != REVEAL_TOOL:
            return
        case_id = _tool_input(event).get("case_id")
        resolver = _ClaimResolver(self.store, _coordinator(event))
        if resolver.claimed(case_id):
            return
        reason = (
            f"identifiers on case {case_id} stay masked until the coordinator making "
            "this call has claimed that case"
        )
        event.cancel_tool = reason
        if self.on_denied is not None:
            self.on_denied(REVEAL_TOOL, reason)

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        if not _tool_name(event).startswith(BOARD_PREFIX):
            return
        resolver = _ClaimResolver(self.store, _coordinator(event))
        context = _tool_input(event).get("case_id")
        event.result = self._mask_result(copy.deepcopy(event.result), resolver, context)

    # --- masking ----------------------------------------------------------

    def _mask_result(self, result: Any, resolver: _ClaimResolver, context) -> Any:
        if isinstance(result, str):
            return self._mask_text(result, resolver, context)
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            masked = dict(result)
            masked["content"] = [self._mask_block(b, resolver, context)
                                 for b in result["content"]]
            return masked
        return self._mask_tree(result, resolver, context)

    def _mask_block(self, block: Any, resolver: _ClaimResolver, context) -> Any:
        if not isinstance(block, dict):
            return self._mask_result(block, resolver, context)
        out = dict(block)
        if "json" in out:
            out["json"] = self._mask_tree(out["json"], resolver, context)
        if isinstance(out.get("text"), str):
            out["text"] = self._mask_text(out["text"], resolver, context)
        return out

    def _mask_tree(self, node: Any, resolver: _ClaimResolver, context) -> Any:
        if isinstance(node, dict):
            case_id = node.get("case_id") if isinstance(node.get("case_id"), str) else context
            visible = resolver.claimed(case_id)
            out: dict = {}
            for key, value in node.items():
                if key in MASKED_FIELDS and value is not None and not visible:
                    out[key] = self._mask_value(value)
                else:
                    out[key] = self._mask_tree(value, resolver, case_id)
            return out
        if isinstance(node, list):
            return [self._mask_tree(item, resolver, context) for item in node]
        if isinstance(node, str):
            return self._mask_text(node, resolver, context)
        return node

    @staticmethod
    def _mask_value(value: Any) -> Any:
        if isinstance(value, dict) and "value" in value:  # a Fact-shaped value
            masked = dict(value)
            masked["value"] = MASK
            masked["quote"] = ""
            return masked
        return MASK

    def _mask_text(self, text: str, resolver: _ClaimResolver, context) -> str:
        """Mask identifiers in free text: JSON inside it by field, otherwise by value."""
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if parsed is not None:
                return json.dumps(
                    self._mask_tree(parsed, resolver, context), ensure_ascii=False
                )
        out = text
        for case in resolver.unclaimed_cases(context):
            for value in _identifier_values(case):
                out = out.replace(value, MASK)
        return out


class NoIdentifiersOut(InterventionHandler):
    """No SSN and no A-number leaves the agent, on any outbound tool.

    The whole tool input is walked, nested dicts and lists included, and the string
    leaves are also matched end to end, so an identifier split across two arguments is
    caught as well as one sitting in a single field.
    """

    name = "no-identifiers-out"
    on_error = "deny"

    def before_tool_call(self, event: BeforeToolCallEvent):
        if _tool_name(event) not in OUTBOUND_TOOLS:
            return Proceed()
        leaves = list(_string_leaves(_tool_input(event)))
        candidates = leaves + [" ".join(leaves), "".join(leaves)]
        for text in candidates:
            if SSN_PATTERN.search(text):
                return Deny(reason="an SSN appears in the message; no identifier leaves the agent")
            if ANUMBER_PATTERN.search(text):
                return Deny(
                    reason="an A-number appears in the message; no identifier leaves the agent"
                )
        return Proceed()


class TraceRedactor(HookProvider):
    """Log every tool result with identifiers gone. Never mutates the result.

    Register it BEFORE `MaskUntilClaimed` so that, under the SDK's reverse ordering for
    `AfterToolCallEvent`, it runs last and logs the masked result. With a store in hand
    it also scrubs the case's stored contact name, phone, and address by value.
    """

    def __init__(self, log: Optional[logging.Logger] = None, level: int = logging.INFO,
                 store: Any = None) -> None:
        self.log = log or logger
        self.level = level
        self.store = store

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def _contact_values(self, case_id: Optional[str]) -> list[str]:
        if self.store is None:
            return []
        try:
            cases = [self.store.get(case_id)] if case_id else list(self.store.list())
        except (ValueError, OSError):
            return []
        values: list[str] = []
        for case in cases:
            if case is not None:
                values.extend(_identifier_values(case))
        return sorted(set(values), key=len, reverse=True)

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        result = getattr(event, "result", None)
        try:
            rendered = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            rendered = str(result)
        rendered = redact(rendered)
        for value in self._contact_values(_tool_input(event).get("case_id")):
            rendered = rendered.replace(value, CONTACT_MASK)
        self.log.log(self.level, "tool=%s result=%s", _tool_name(event), rendered)


class LedgerWriter(HookProvider):
    """One ledger row per tool call. A cancelled or denied call is a `denied=True` row."""

    def __init__(self, case_id: str, store: Any = None, tier: Optional[int] = None,
                 on_entry: Optional[Callable[[Any], None]] = None) -> None:
        self.case_id = case_id
        self._store = store
        self.tier = tier
        self.on_entry = on_entry

    @property
    def store(self):
        if self._store is None:
            from .store import get_store

            self._store = get_store()
        return self._store

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        from .ledger import Ledger

        name = _tool_name(event)
        if not name:
            return
        case = self.store.get(self.case_id)
        if case is None:
            return
        cancel_message = getattr(event, "cancel_message", None)
        denied = cancel_message is not None
        why = json.dumps(_tool_input(event), ensure_ascii=False, default=str)[:300]
        if denied:
            why = f"{redact(why)} | refused: {cancel_message}"
        action = f"tool {name} denied" if denied else f"tool {name}"
        entry = Ledger.add(
            case,
            action=action,
            why=redact(why),
            undo=UNDO_TEXT.get(name, f"reverse the effect of {name} by hand"),
            tier=self.tier,
            is_read=denied or name in READ_TOOLS,
            denied=denied,
        )
        self.store.put(case)
        if self.on_entry is not None:
            self.on_entry(entry)


class Guards(NamedTuple):
    """What an Agent needs: `hooks=guards.hooks, interventions=guards.interventions`."""

    hooks: list[HookProvider]
    interventions: list[InterventionHandler]


def guards(
    on_denied: Optional[Callable[[str, str], None]] = None,
    ledger_cb: Optional[Callable[[Any], None]] = None,
    case_id: Optional[str] = None,
    store: Any = None,
    tier: Optional[int] = None,
) -> Guards:
    """Build the guard set in the order the SDK requires.

    `TraceRedactor` is registered first so that, with `AfterToolCallEvent` callbacks
    running in reverse, it runs last and logs a masked result. `MaskUntilClaimed`
    follows, then `LedgerWriter` (added only when a `case_id` names the case whose
    ledger the rows belong to; `ledger_cb` is called with each row it writes).
    `on_denied` is called when the reveal guard cancels a call.
    """
    hooks: list[HookProvider] = [
        TraceRedactor(store=store),
        MaskUntilClaimed(on_denied=on_denied, store=store),
    ]
    if case_id is not None:
        hooks.append(LedgerWriter(case_id, store=store, tier=tier, on_entry=ledger_cb))
    return Guards(hooks=hooks, interventions=[NoIdentifiersOut()])
