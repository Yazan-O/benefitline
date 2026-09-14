"""Deadlines read from `data/deadline_rules.md`, never invented.

File format, one rule per line, five pipe-separated fields:

    program | event | deadline rule | source URL | quote

`deadline rule` carries the number the math uses, e.g. "within 30 days of the filing
date". A rule with no number is still surfaced, with `due` left empty, so a
coordinator sees it and no date is guessed.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .schemas import CaseRecord

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "data" / "deadline_rules.md"
_PERIOD = re.compile(r"(\d+)\s*(day|days|week|weeks|month|months)", re.IGNORECASE)

# Oklahoma writes its periods as "30-calendar days" / "30 calendar days", which `_PERIOD`
# reads as no period at all. Respelling it is spelling, not substance: the quote and the
# URL are passed through untouched.
_QUALIFIED_PERIOD = re.compile(
    r"(\d+)[-\s](?:calendar|business|working)[-\s](day|days|month|months)", re.IGNORECASE
)


def rules_path(path: str | os.PathLike[str] | None = None) -> Path:
    """The rules file: the argument, else BENEFITLINE_DEADLINE_RULES, else data/."""
    if path is not None:
        return Path(path)
    env = os.environ.get("BENEFITLINE_DEADLINE_RULES")
    return Path(env) if env else DEFAULT_RULES_PATH


def load_rules(path: str | os.PathLike[str] | None = None) -> list[dict]:
    """Parse the rules file. Missing file is an error; rules are never invented."""
    target = rules_path(path)
    if not target.exists():
        raise FileNotFoundError(
            f"deadline rules file not found at {target}. "
            "Benefitline never invents a deadline; add the sourced rules file "
            "(program | event | deadline rule | source URL | quote) or set "
            "BENEFITLINE_DEADLINE_RULES to its path."
        )
    out: list[dict] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        while parts and parts[0] == "":       # markdown tables carry edge pipes
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) < 5:
            continue
        if parts[0].lower() == "program" or set(parts[0]) <= {"-", ":"}:
            continue
        out.append(
            {
                # The file names programs `SNAP`/`LIHEAP`; every caller matches on the engine
                # key (`snap`), so the two spellings are reconciled once, here.
                "program": parts[0].lower(),
                "event": parts[1],
                "rule": _QUALIFIED_PERIOD.sub(r"\1 \2", parts[2]),
                "rule_url": parts[3],
                "quote": parts[4],
            }
        )
    return out


def _offset(rule_text: str) -> Optional[timedelta]:
    m = _PERIOD.search(rule_text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("day"):
        return timedelta(days=n)
    if unit.startswith("week"):
        return timedelta(weeks=n)
    return timedelta(days=30 * n)


def _filing_date(case: CaseRecord) -> Optional[date]:
    """When the case opened, or None when nothing on the case dates it.

    Fixed, so a due date does not drift with later activity, and never the wall clock:
    a case with no anchor gets no computed deadline rather than an invented one.
    """
    first = case.transcript[0].get("at") if case.transcript else None
    if isinstance(first, str):
        try:
            return datetime.fromisoformat(first).date()
        except ValueError:
            pass
    elif isinstance(first, datetime):
        return first.date()
    if case.ledger:
        return min(e.at for e in case.ledger).date()
    return None


def _case_programs(case: CaseRecord) -> list[str]:
    """The programs this case is actually about: filed, found eligible, or already held."""
    keys = [p.program for p in case.filing_plans]
    if case.engine_result:
        keys += [p.key for p in case.engine_result.programs if p.eligible]
    keys += [str(f.value) for f in case.facts.already_receives]
    seen: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.append(key)
    return seen


def deadlines_for(
    case: CaseRecord,
    rules: Optional[list[dict]] = None,
    path: str | os.PathLike[str] | None = None,
) -> list[dict]:
    """`[{program, event, due, rule_url, quote}]` for this case, earliest first.

    A rule applies only to a program this case names. A case with no anchor date and no
    recertification date gets nothing back; no deadline is guessed from the wall clock.
    """
    loaded = rules if rules is not None else load_rules(path)
    programs = _case_programs(case)
    filed = _filing_date(case)
    out: list[dict] = []

    recert = case.facts.recert_due_date
    if recert is not None:
        out.append(
            {
                "program": programs[0] if programs else "enrollment",
                "event": "recertification due",
                "due": str(recert.value),
                "rule_url": "",
                "quote": recert.quote,
            }
        )

    if filed is None or not programs:
        return out

    for rule in loaded:
        if rule["program"] not in programs:
            continue
        delta = _offset(rule["rule"])
        out.append(
            {
                "program": rule["program"],
                "event": rule["event"],
                "due": (filed + delta).isoformat() if delta else "",
                "rule_url": rule["rule_url"],
                "quote": rule["quote"],
            }
        )
    return sorted(out, key=lambda d: (d["due"] == "", d["due"]))


def _fold(line: str) -> list[str]:
    """RFC 5545 caps a content line at 75 octets; continuations start with one space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return [line]
    out: list[str] = []
    chunk = bytearray()
    limit = 75
    for char in line:
        raw = char.encode("utf-8")
        if len(chunk) + len(raw) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = bytearray()
            limit = 74          # a continuation line spends one octet on its leading space
        chunk += raw
    out.append(chunk.decode("utf-8"))
    return [out[0]] + [" " + part for part in out[1:]]


def _ics_escape(text: str) -> str:
    out = text.replace("\\", "\\\\")
    out = out.replace(";", "\\;").replace(",", "\\,")
    return out.replace("\n", "\\n")


def ics(
    case: CaseRecord,
    rules: Optional[list[dict]] = None,
    path: str | os.PathLike[str] | None = None,
) -> str:
    """A VCALENDAR with one all-day VEVENT per dated deadline on this case."""
    items = [d for d in deadlines_for(case, rules=rules, path=path) if d["due"]]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Benefitline//Deadlines//EN",
        "CALSCALE:GREGORIAN",
    ]
    for item in items:
        day = item["due"].replace("-", "")
        end = (date.fromisoformat(item["due"]) + timedelta(days=1)).isoformat().replace("-", "")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid5(uuid.NAMESPACE_URL, case.case_id + item['program'] + item['event']).hex}@benefitline",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{_ics_escape(item['program'] + ': ' + item['event'])}",
            f"DESCRIPTION:{_ics_escape(item['quote'] + (' ' + item['rule_url'] if item['rule_url'] else ''))}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"
