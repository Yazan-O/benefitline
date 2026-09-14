"""Phase 2 done-check: play a gallery family's script through IntakeAgent and score it.

    python src/run_intake.py --case case1_single_mother_two_kids --lang en
    python src/run_intake.py --case case2_couple_newborn --lang en
    python src/run_intake.py --case case1_single_mother_two_kids --lang en --free

The agent chooses what to ask; the script answers whatever it asked, matched on the
`asks` key the reply carries. An unscripted question is reported, not guessed at.
PASS = zero fact mismatches against gallery/cases.py and no more than 6 questions asked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "gallery"))

from benefitline.facts import facts_match, missing_facts, to_household  # noqa: E402
from benefitline.intake import IntakeAgent  # noqa: E402
from benefitline.schemas import CaseRecord  # noqa: E402
from cases import CASES  # noqa: E402

SCRIPTS = os.path.join(HERE, "..", "gallery", "scripts.json")
MAX_QUESTIONS = 6
MAX_TURNS = 14


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _show(role: str, tier, text: str) -> None:
    who = "family" if role == "family" else "agent "
    print(f"  [{who} t{tier}] {text}")


def _new_case(case_id: str, gallery_case: str | None = None) -> CaseRecord:
    return CaseRecord(case_id=case_id,
                      session_id=f"{uuid.uuid4()}-benefitline",
                      gallery_case=gallery_case)


def play(case_id: str, lang: str) -> int:
    household = next((h for h in CASES if h.case_id == case_id), None)
    if household is None:
        print(f"unknown case {case_id!r}; known: {[h.case_id for h in CASES]}")
        return 2
    with open(SCRIPTS, encoding="utf-8") as f:
        script = json.load(f)["cases"][case_id]
    replies = {r["asks"]: r[lang] for r in script["replies"]}

    agent = IntakeAgent()
    case = _new_case(case_id, gallery_case=case_id)
    print(f"== {case_id}  lang={lang}")

    message = script["opening"][lang]
    unscripted = ""
    for _ in range(MAX_TURNS):
        _show("family", "?", message)
        case = agent.step(case, message)
        fam, reply = case.transcript[-2], case.transcript[-1]
        _show("family", fam["tier"], "(routed)")
        _show("agent", reply["tier"], reply["text"])
        asks = reply.get("asks") or ""
        if not asks:
            break
        if asks not in replies:
            unscripted = asks
            break
        message = replies[asks]

    still_missing = missing_facts(case.facts)
    mismatches = facts_match(case.facts, household)
    got = to_household(case.facts, case_id)

    print(f"\n  status           : {case.status}")
    print(f"  questions asked  : {case.questions_asked}")
    print(f"  tier counts      : {case.tier_counts}")
    print(f"  gathered         : state={got.state} members={len(got.members)} "
          f"rent={got.rent} utilities={got.utility_expense}")
    for i, p in enumerate(got.members):
        print(f"    member[{i}] age={p.age} employment={p.employment_income} "
              f"ss={p.social_security_retirement} preg={p.is_pregnant} bf={p.is_breastfeeding} "
              f"imm={p.immigration_status} med={p.other_medical_expenses} "
              f"prem={p.medical_expense_health_insurance_premiums} "
              f"head={p.is_tax_unit_head} spouse={p.is_tax_unit_spouse}")
    if got.citations.get("immigration_unknown_members"):
        print(f"  flagged for risk : members {got.citations['immigration_unknown_members']} "
              f"gated conservatively (status not established)")
    if still_missing:
        print(f"  still missing    : {still_missing}")
    if unscripted:
        print(f"  UNSCRIPTED       : agent asked for {unscripted!r}, which the script does not answer")
    if mismatches:
        print(f"  mismatches ({len(mismatches)}):")
        for m in mismatches:
            print(f"    - {m}")
    else:
        print("  mismatches       : none")

    ok = (not mismatches and not still_missing and not unscripted
          and case.questions_asked <= MAX_QUESTIONS)
    verdict = "PASS" if ok else "FAIL"
    print(f"  {verdict} {case_id} lang={lang} questions={case.questions_asked} "
          f"tiers={case.tier_counts} mismatches={len(mismatches)}")
    return 0 if ok else 1


def free(case_id: str, lang: str) -> int:
    agent = IntakeAgent()
    case = _new_case(case_id or "free_case")
    case.facts.language = lang
    print("free mode: type the family's messages, blank line or Ctrl-D to stop")
    for line in sys.stdin:
        message = line.strip()
        if not message:
            break
        case = agent.step(case, message)
        reply = case.transcript[-1]
        _show("agent", reply["tier"], reply["text"])
        if case.status == "ready_for_engine":
            print("  all facts gathered; ready for the engine")
            break
    print(f"  questions asked : {case.questions_asked}")
    print(f"  tier counts     : {case.tier_counts}")
    print(f"  still missing   : {missing_facts(case.facts)}")
    return 0


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="play a gallery intake script")
    ap.add_argument("--case", default="case1_single_mother_two_kids")
    ap.add_argument("--lang", default="en", choices=["en", "ar"])
    ap.add_argument("--free", action="store_true", help="read family messages from stdin")
    args = ap.parse_args()
    return free(args.case, args.lang) if args.free else play(args.case, args.lang)


if __name__ == "__main__":
    raise SystemExit(main())
