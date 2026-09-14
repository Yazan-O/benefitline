# Benefitline design contract (read before building any module)

One sentence: the model gathers facts and explains; a deterministic engine decides; a human is
interrupted only through a Decision Card. Every module below is a file in `src/benefitline/`.
Shared types: `schemas.py` (Pydantic). Engine: `engine.py` (done, do not change its numbers).
Reference for Strands APIs: `../02_TOOLS.md`, `../04_MULTI_AGENT.md`, `../03_HOOKS_PLUGINS_INTERVENTIONS.md`,
`../01_AGENT_CORE_CONCEPTS.md` (structured output), `../05_MODEL_PROVIDERS.md`, `../12_AGENTCORE_RUNTIME.md`, `../09_EVALS_SDK.md`.
Verified on this machine 2026-09-13: `from strands import Agent, tool, ToolContext`; `from strands.models import BedrockModel, ModelRouter`;
`from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent, HookProvider, HookRegistry`; `from strands.multiagent import GraphBuilder`;
`from strands.interventions import InterventionHandler, Proceed, Deny, Guide, Transform, Confirm`; `from strands.session import FileSessionManager`.
Both Bedrock models answer from `.env` (load with `python-dotenv`): `MODEL_PRIMARY=us.anthropic.claude-sonnet-4-6`,
`MODEL_FALLBACK=us.anthropic.claude-haiku-4-5-20251001-v1:0`, region `us-east-1`, pass `region_name="us-east-1"` explicitly.
Structured output: `agent(prompt, structured_output_model=Model).structured_output`; never the deprecated `agent.structured_output()`.
Windows dev box: no `python_repl`; set `BYPASS_TOOL_CONSENT=true` for headless runs.

## Modules and owners

| File | Purpose | Contract |
|---|---|---|
| `models.py` | `primary()`, `fallback()`, `router()` (ModelRouter, Sonnet on the us. profile, Sonnet on the global. profile, then Haiku, `max_switches=2`), `cheap()` (Haiku, temp 0, max_tokens 600; a tier-1 turn returns a full IntakeTurn) | pure factories |
| `intake.py` | `IntakeAgent.step(case: CaseRecord, message: str) -> CaseRecord` | one family message in, updated facts and one reply out |
| `triage.py` | `route(case, message) -> int` tier 0/1/2; `tier0_parse(pending_question, message) -> Fact or None` | zero model calls in tier 0 |
| `facts.py` | `missing_facts(facts) -> list[str]` (deterministic, ordered by value to the engine), `validate_facts(facts, transcript) -> HouseholdFacts` (drops any fact whose `source_msg_id` is not a family message; same bar on both models), `to_household(facts) -> engine.Household` | no model |
| `specialists.py` | `run_specialists(case) -> CaseRecord`: Strands `Graph`, three parallel nodes (explainer, filing, risk) then a deterministic `judgment` node with AND condition on all three edges | Graph, never Swarm |
| `forms.py` | `plan_filing(program, facts, engine_result) -> FilingPlan` using field labels from `data/forms/*.md` and rules from `data/deadline_rules.md` | no model; the filing agent only fills free-text fields |
| `hooks.py` | `MaskUntilClaimed` (BeforeToolCall + AfterToolCall on board tools: masks `contact_name`, `contact_phone`, `address` until `case.claimed_by`), `NoIdentifiersOut` (intervention `Deny` on `send_to_family`/`send_to_board` when text matches SSN `\d{3}-\d{2}-\d{4}` or A-number `A\d{8,9}`), `TraceRedactor` (same regexes on every tool result written to logs), `LedgerWriter` (AfterToolCall appends `LedgerEntry`, denied calls included) | enforced in code, not prompts |
| `ledger.py` | `Ledger.add(entry)`, `Ledger.undo(entry_id)`, veto window 10 min for writes, none for reads | in `CaseRecord.ledger` |
| `cards.py` | `make_card(case, risk) -> DecisionCard`; `answer_card(case, card_id, option, make_rule)`; standing rules in `org_rules.json` via `store.py` | one tap answers |
| `store.py` | `Store` protocol: `get(case_id)`, `put(case)`, `list()`, `rules()`, `add_rule()`. `LocalJsonStore(dir)` and `DynamoStore(table)` | the board reads `list()` |
| `deadlines.py` | `deadlines_for(case) -> list[{program, event, due, rule_url}]`, `ics(case) -> str` | from `data/deadline_rules.md` |
| `app.py` (in `src/`) | `BedrockAgentCoreApp` entrypoint; `payload["action"]` in `chat`, `start_case`, `board`, `claim`, `answer_card`, `undo`, `gallery`, `reset`; always `isinstance(payload.get("message",""), str)`; `/ping` served by the SDK | one runtime, one entrypoint |
| `web/index.html` | single file, phone first, SMS look, gallery picker, tier counter, result cards with the rule link, coordinator tab (masked until Claim), ledger with Undo, decision cards, Arabic RTL | calls `POST <API_BASE>` with the payload above |

## API payloads (web <-> app)
Request: `{"action": str, "session_id": str (>=33 chars, browser-minted uuid4 + "-benefitline"), "case_id"?: str, "message"?: str, "gallery_case"?: str, "card_id"?: str, "option"?: str, "coordinator"?: str, "entry_id"?: str, "lang"?: str}`
Response: `{"ok": bool, "case": CaseRecord-as-dict | null, "board": [CaseRecord-summary...] | null, "gallery": [...] | null, "error": str | null}`.
`chat` returns the updated case, whose `transcript[-1]` is the agent reply and whose `tier_counts` feed the counter.
When `missing_facts` is empty after a step, the app runs `engine.compute` then `run_specialists` in the same request and the case comes back with `engine_result`, `explanation`, `filing_plans`, `risk`, and any `cards`.

## Tiering (Quiet Core)
Tier 0 (no model): language detection by script (Arabic block => "ar"), yes/no/number parsing when the pending question expects one field, `missing_facts`, engine call, form fill, deadline math, masks. Tier 1 (Haiku): messages of 6 words or fewer that tier 0 could not parse. Tier 2 (Sonnet): everything else. Every transcript entry records its tier; `tier_counts` shows on screen.

## Intake behavior
System prompt states: ask one question at a time, only from the missing-facts list handed to you, in the family's language; never state an eligibility amount; never answer immigration or legal questions (set `needs_human`). The model returns `IntakeTurn`; `validate_facts` runs before the facts are stored. Questions are ordered: state (default OK if the family names an Oklahoma city), household size and ages, monthly income (and whose), rent, utilities, then only-if-relevant: pregnancy or breastfeeding when a woman 15-50 is present, medical costs when a member is 60+ or disabled, immigration status only if the family raises it or a program gate needs it (the agent asks "Is everyone in the home a US citizen or permanent resident?" once, at the end, and does not follow up on details).

## Specialists
- explainer: Sonnet via router; input = facts + engine_result + org rules; output `Explanation` in `facts.language`; every per_program line carries the engine's rule URL; amounts labeled "estimate, the agency decides".
- filing: Sonnet via router; input = facts + engine_result + `forms.plan_filing` drafts; output list[FilingPlan] with only free-text fields written by the model; every field carries `source_msg_id`.
- risk: Haiku; output `RiskReport`; escalates on: any member not CITIZEN/LPR (parents' programs held, children's filed), income the family cannot document (self-employment, cash), any legal question, no reply after two nudges. Never decides immigration eligibility.
- judgment (deterministic `MultiAgentBase` node): if `risk.escalate` make one `DecisionCard`, mark `status="escalated"`, file the safe programs; else ledger the filings with a 10-minute veto window, `status="filed"`.

## Gallery (`gallery/`)
`cases.py` (engine inputs), `ground_truth.json` (engine output), `scripts.json` (scripted English family replies per case, each reply tagged with the fact it carries; `run_intake.py --free` plays an unscripted family). The bench asserts facts gathered == case inputs and engine numbers == ground truth.

## Non-negotiables
No eligibility amount from a model. No identifier leaves the agent (SSN, A-number). Board identifiers masked until claim. Fictional households labeled on screen. Every number in the UI carries its rule URL. No TODOs in shipping paths.
