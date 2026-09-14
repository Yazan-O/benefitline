# Phase 2 done-check: intake bench (Strands Evals)

Phase 2 done-check: PASS (all 12 runs <= 6 questions, facts and engine numbers match)

Run started 2026-09-14 11:42 America/Chicago, table written 2026-09-14 11:50. Every cell below comes from a run file this bench run wrote (`bench_run` stamp in each `run_*.json`); earlier files are shelved under `stale_*/`; no number is transcribed from anywhere else.

| case | language | questions asked | facts matched | engine match | tier 0/1/2 | judge | run-to-run identical |
|---|---|---|---|---|---|---|---|
| case1_single_mother_two_kids | en | 6 | 37/37 | pass | 4/0/3 | 0.67 | yes |
| case2_couple_newborn | en | 6 | 37/37 | pass | 4/0/3 | 0.67 | yes |
| case3_senior_alone | en | 5 | 15/15 | pass | 5/0/1 | 0.67 | no |
| case3b_senior_alone_with_medical | en | 6 | 15/15 | pass | 6/0/1 | 0.67 | yes |
| case4_mixed_status | en | 5 | 48/48 | pass | 4/0/2 | 0.67 | no |
| case5_on_snap_recert_due | en | 6 | 26/26 | pass | 5/0/2 | 0.67 | yes |

Columns: **questions asked** counts agent replies containing a question mark (Latin or Arabic); **facts matched** is `facts.facts_match` over state, rent, utilities, member count and eleven fields per member; **engine match** compares `engine.compute(to_household(...))` program amounts against `gallery/ground_truth.json` at two decimal places, all programs; **tier 0/1/2** is the case record's `tier_counts`; **judge** is a Haiku rating of one-question-at-a-time / plain language / no legal advice, 0-1; **run-to-run identical** compares the two repeats on questions_asked, turns, tier_counts, facts_mismatches, engine_match, still_missing, bad_citations, dollar_figures, wrong_language (not transcript bytes: the user simulator is a separate LLM and its wording varies even at temperature 0).

## Failure detail

### case1_single_mother_two_kids / en

### case2_couple_newborn / en

### case3_senior_alone / en

### case3b_senior_alone_with_medical / en

### case4_mixed_status / en

### case5_on_snap_recert_due / en

## Reproduce

```
cd D:\PHD_Take2\03_Competitions\amazon_agents\benefitline
python evals/bench.py
```

strands-agents-evals 1.2.0; strands-agents 1.55.1; policyengine-us 2.0.4. Credentials from `.env`, `BYPASS_TOOL_CONSENT=true`, region us-east-1. Raw per-run JSON: `_runs/2026-09-13_phase2_bench/`.

## What each check can kill

- questions asked: the done-check itself (six questions or fewer, nothing still missing).
- facts matched: intake silently mis-reading or dropping a value the family gave.
- engine match: a fact that survives `facts_match` but moves a benefit amount.
- citations: a fact stored without a family message behind it. `validate_facts` already drops those inside `step()`, so this is a regression guard, not independent evidence.
- no dollar figure: an amount leaking out of the model. Regex `(\$\s*\d)|(\d[\d,]*(?:\.\d+)?\s*(?:dollars?|USD|بالشهر\s*دولار|دولار))` over agent replies only.
- language: an agent reply that is not in the family's language (every gallery script is English, so an Arabic-script reply is the failure).
- judge: wording quality, which no deterministic check covers.

## Notes on the harness

- The user simulator is a hand-built `ActorProfile` passed to `ActorSimulator(...)` directly, not `from_case_for_user_simulator`, which would invent the family's facts in an extra uncontrolled model call. The profile carries the household exactly as `gallery/cases.py` defines it, including its zeros: without "nobody is pregnant" and "you pay nothing for medicine" the simulator volunteered a $50 medicine bill and a $170 Medicare premium the fixture does not have, and the bench then scored intake against an invented household. The full list of differences from `../09_EVALS_SDK.md` is at the top of `evals/bench.py`.
- The simulator's own `stop` signal is recorded but does not end the conversation: per the done-check a run ends when `facts.missing_facts` is empty or the turns run out.
- The judge rubric was revised once, before every run in the table above, after Haiku scored 0.01-0.67 by counting the intake question "is everyone in the home a US citizen or permanent resident?" as legal advice and "I will check what you qualify for" as stating an amount. `src/DESIGN.md` mandates that citizenship question, so the rubric now carves both out. The revision touches the judge column only; the PASS/FAIL verdict is decided by the questions, facts and engine columns, which no rubric affects.
