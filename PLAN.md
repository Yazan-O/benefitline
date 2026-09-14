# Benefitline (Good Neighbor track): one text line that tells a family exactly what they qualify for, then files it

**What it is.** A community organization (a mosque or church help desk, a food pantry, a school family liaison, a library) gets one number. A family texts it in any language. Benefitline asks the few questions that matter, runs the real federal and state rules through PolicyEngine, and answers with exact numbers: "Your household qualifies for SNAP at about N per month, Medicaid for the children, WIC for the baby, and the Earned Income Tax Credit at about N. Here is what each one needs from you." Then it drafts each application, tracks the recertification and document deadlines in the background, and escalates to the coordinator only when income verification, immigration status, or a legal question needs a human. The coordinator sees one board: every family, what they qualify for, what is filed, what is waiting on whom.

**The wow.** The judge plays a family by text on their own phone. Three or four questions in, the agent returns a benefit list with amounts computed by a rules engine, not guessed by a model, and shows the rule it used for each. Then the coordinator view shows thirty families handled with two escalations. The unusual part: the model never computes eligibility. It gathers facts, a deterministic engine computes, the model explains.

**Why it matters (sourced, read 2026-09-13).** SNAP reached 88 percent of eligible individuals in FY2022, its highest rate on record, which still leaves roughly one in eight eligible people unenrolled (USDA FNS, Trends in SNAP Participation Rates FY2020 and FY2022). In FY2020 the rate was 81 percent (corrected 2026-09-13 per data/SOURCES.md; the 78 figure was superseded). A New York Health Foundation analysis found more than one in five eligible low-income New Yorkers not enrolled. Participation gaps are widest for working families, immigrants, and older adults, the people who walk into a community help desk. Numbers on the other programs (WIC, EITC, Medicaid churn at recertification) are fetched from the primary source before they enter the video; each carries its URL in `SOURCES.md`.

**Who it is for.** The volunteer or part-time coordinator who already helps families with paperwork and has no way to know all the rules across programs. Secondarily every family that qualifies for something and does not know it.

## The real data

- **PolicyEngine US** (`pip install policyengine-us`, AGPL-3.0, github.com/PolicyEngine/policyengine-us, confirmed 2026-09-13): an open-source microsimulation of the US federal and state tax and benefit system. It runs fully locally, no credentials, no waiting. It models SNAP, Medicaid, WIC, TANF, SSI, EITC, CTC, LIHEAP and more. The hosted API needs credentials by email, so the local package is the route. Oklahoma-specific rule coverage is unverified and is the first thing the data phase checks; if a program's Oklahoma rules are thin, the gallery uses a state where coverage is complete and says which. PolicyEngine already powers MyFriendBen, a production benefits screener, which is the credibility line for judges.
- **Program forms and document lists** from the agencies themselves (Oklahoma DHS SNAP application, SoonerCare, Oklahoma WIC, IRS EITC): the application drafts fill the real forms' fields.
- **Recertification and reporting rules** from the same agencies, encoded as deadline rules with the source URL beside each.

## The demo gallery

Five household stories with a scripted set of text replies, so a judge can play one through in under two minutes, plus an open mode where the judge answers freely.

| # | Household | What the engine should find | Why it is in the gallery |
|---|---|---|---|
| 1 | Single mother, two children, works 30 hours at 14 an hour, Tulsa | SNAP, Medicaid for children, EITC, CTC | The common case, all four programs |
| 2 | Couple, newborn, one income, Arabic speaker | WIC, Medicaid, SNAP | Language scene: whole conversation in Arabic, coordinator board in English |
| 3 | 71-year-old on Social Security, lives alone | SNAP with the elderly medical-expense deduction, LIHEAP | The deduction most seniors never claim; the agent asks one question about medical costs and the SNAP amount changes on screen |
| 4 | Mixed-status family | Children eligible, parents not for some programs | Escalation scene: the agent does not guess immigration rules, it files the children's applications and hands the parents' question to the coordinator with the facts gathered |
| 5 | Family already on SNAP, recertification due in 12 days | Nothing new; a deadline | Background scene: the agent surfaces the deadline, drafts the recertification, and reminds |

Ground truth per case is computed once from PolicyEngine and committed with the engine version; the bench asserts the agent's gathered facts produce the same numbers.

## What the judge sees, scene by scene

1. **Open (20 s).** A stack of program brochures on a folding table. "One volunteer, forty families, nine programs, nine rulebooks."
2. **Text in (20 s).** Judge scans the QR, opens the chat (web chat that looks and works like SMS; real SMS in the video if the number is live). Types "I need help with food for my kids."
3. **Questions (30 s).** Household size, ages, income, rent, state. Four questions, plain words, one at a time. The agent never asks what it does not need; a counter shows "questions asked: 4 of a possible 6."
4. **Engine (20 s).** "Computing with PolicyEngine, federal and Oklahoma rules, version X." Results appear as cards with amounts and, under each, the rule it applied. A judge can tap the rule.
5. **Language (15 s).** Case 2 in Arabic, side by side with the coordinator's English board.
6. **The deduction (20 s).** Case 3: "Do you pay for any medicine or doctor visits yourself?" The SNAP amount changes on screen. Caption: "the deduction most seniors never claim."
7. **Filing (20 s).** The agent fills the real SNAP application fields from the conversation, shows the document checklist, sets the deadline, sends the family a one-line summary in their language.
8. **Escalation (20 s).** Case 4: the Decision Card to the coordinator with the facts, the two programs already filed for the children, and the one question only a human should answer. Address and identifiers stay masked on the coordinator's board until the coordinator claims the case (a policy on the tool, not a prompt).
9. **Background (20 s).** Case 5 fast-forward: recertification due, drafted, reminded, filed; a family that stopped replying is escalated after two nudges.
10. **Bench, diagram, endpoint (30 s).**
11. **Pitch close (30 s).**

## Architecture

```
SMS / web chat ──▶ Intake agent (any language; structured output: HouseholdFacts, each fact with the message it came from)
                        │
                        ▼
              Tier 0: deterministic "what is still missing" check (no model)
                        │
                        ▼
              PolicyEngine tool (local, deterministic): eligibility and amounts per program
                        │
                        ▼
     Strands Graph, parallel:
       ├─ Explainer agent (amounts and rules in the family's language)
       ├─ Filing agent (form field mapping, document checklist, deadlines)
       └─ Risk agent (immigration, income verification, legal → escalate)
                        │
                        ▼
     Coordinator board, Decision Cards, ledger, calendar feed; MCP server exposing screen_household()
```

- **The point of the design, said plainly in the README:** the model gathers and explains, the engine decides. No eligibility number in the product comes from a language model.
- **Strands pieces, by name:** `Agent` with structured output for HouseholdFacts and FilingPlan; `@tool` for `policyengine_compute`, `missing_facts`, `fill_form`, `deadline_add`, `escalate`; `Graph` for the three parallel specialists; hooks: a `BeforeToolCall` hook that masks address and identifier fields in any tool result bound for the coordinator board until the case is claimed, a PII-redaction hook on traces, and a hook that refuses to send any message containing an SSN or A-number; session manager per family; AgentCore Memory for the organization's standing rules ("always check LIHEAP in winter"); AgentCore Policy (Cedar) on the escalate and address tools if the Gateway route is taken (`../16_AGENTCORE_POLICY_SECURITY.md`), which turns the privacy rule into an enforced policy and is a strong Technical point.
- **Tiered routing:** the missing-facts check and language detection run without a model; a cheap model handles yes/no replies; the full model handles free text.
- **Same-bar fallback:** Sonnet primary, Haiku fallback, one `validate_facts()` that requires every fact to cite the message it came from before the engine runs.
- **Evals:** deterministic bench on the five cases (facts gathered equal ground-truth facts, engine numbers equal committed numbers), plus a Strands Evals user simulator that plays each household with paraphrased answers and in two languages, scoring how many questions the agent needed.

## Build phases

1. **Engine phase.** Install PolicyEngine, compute the five cases, check Oklahoma coverage program by program. Done-check: a script prints amounts per program per case with the engine version; any gap is written down with the fallback state.
2. **Intake phase.** HouseholdFacts schema with message citations, missing-facts logic, language handling. Done-check: the user simulator completes all five cases in under six questions each, in English and Arabic.
3. **Explain and file phase.** Explainer, filing agent with real form fields, deadlines, coordinator board, masks. Done-check: scenes 2 through 9 run on a phone from the QR.
4. **Deploy phase.** AgentCore Runtime, live demo link, optional SMS number, optional Gateway with Cedar policy on the address tool. Done-check: the judge URL works from cellular; if Cedar is in, a denied address read is shown in the trace.
5. **Ship phase.** Video, README with bench table and SOURCES.md, diagram, MIT license in About (note PolicyEngine is AGPL and is used as a dependency, which the README states), builder.aws post ("Agents for Humans: the model asks, the rules engine decides"), Devpost form.

## Cut list

Gateway and Cedar (keep the hook-based mask), real SMS (keep web chat), the MCP server, case 5. Never cut: the engine on camera with the rule under each amount, the Arabic scene, the deduction scene, the escalation scene.

## Risks

- **Oklahoma rule coverage in PolicyEngine is unverified.** Checked first; fall back to a fully covered state and say so.
- **Wrong benefit numbers would be worse than none.** The numbers come from the engine, the bench pins them, and every amount is labeled "estimate, the agency decides."
- **Immigration questions.** The agent never answers them; case 4 shows the handoff, which is the honest and the impressive behavior.
- **License.** PolicyEngine is AGPL-3.0; Benefitline calls it as a library and ships under MIT with the dependency declared. If a reviewer objects, the engine runs as a separate service with its own AGPL notice.
