## Project name

Benefitline

## Elevator pitch

One text line. Every benefit a family qualifies for, with the rule under each.

## About the project

### Inspiration

A volunteer at a mosque help desk in Tulsa sits across from a family with nine program rulebooks to work through. SNAP has one income test. Medicaid has another. WIC adds a clinic visit. The tax credits live in a different agency. The volunteer knows the families and does not know the rules. People leave with a guess.

SNAP reached 88 percent of eligible individuals in FY2022, the highest rate on record ([USDA FNS, Oct 2024](https://www.fns.usda.gov/sites/default/files/resource-files/ops-snap-trendsfy20-fy22-report.pdf)). Break it by group and the gap widens: 76 percent for households with earned income, 55 percent for elderly individuals, 50 percent for eligible noncitizens. WIC reached 56.1 percent of eligible people in 2023 ([USDA FNS](https://www.fns.usda.gov/sites/default/files/resource-files/wic-eer2023-report.pdf)). Four out of five eligible taxpayers receive the EITC ([IRS](https://www.irs.gov/tax-professionals/eitc-central/50-years-of-earned-income-tax-credit)). The rules are public and deterministic. What is missing is somebody to run them.

### What it does

A family sends one text line. Benefitline asks the questions the rules need, one at a time, with a counter on screen. Then it hands the facts to policyengine-us 2.0.4 and shows exact amounts, each with the statute or regulation underneath.

A single mother of two in Tulsa sees SNAP at $632 per month, Medicaid for the children, and WIC at $59.80 per month. On the tax side: the federal EITC at $7,316, the Oklahoma EITC at $269.57, and the Child Tax Credit at $4,400 with $2,901 refundable. Every card links its rule: 7 U.S.C. 2017(a) under SNAP, 7 CFR 246.7(e) under WIC. A card expands to show the arithmetic. Above the cards: "An estimate from the rules as written. The agency makes the decision."

The agent produces a filing plan per program with the real form, document checklist, and deadline. A coordinator sees one board with every case. Contact details stay masked until someone claims a case. The mask is a code hook, not a prompt instruction. The agent interrupts a coordinator once per case with a Decision Card: the question, the options, a default, and the evidence. One tap answers it. Every action is a ledger row with a ten-minute undo window.

The demo is a gallery of five fictional households shown as six cases, because the same senior appears with and without medical costs. One question about medicine moves SNAP from $148 to $298 through the excess medical expense deduction (7 CFR 273.9(d)(3)). Fictional households, real rules.

### How we built it

The model gathers facts. The engine decides. Most turns never reach a model: language detection, number parsing, the engine call, form filling and the masks are deterministic code. In the bench run, four to six turns per case are tier 0 (no model call) and one to three reach Sonnet 4.6.

Strands Agents 1.55.1 is the agent layer. Intake uses structured output: each fact carries the id of the family message it came from, and `validate_facts()` drops any fact the family did not say. Three specialists (Explainer, Filing, Risk) run in parallel as a Strands Graph with an AND-condition judgment node that is pure code. Guards are Strands hooks and one intervention: `MaskUntilClaimed` masks contact details on the board, `NoIdentifiersOut` denies any outbound tool call carrying an SSN or A-number, and `TraceRedactor` scrubs logs. `ModelRouter` holds Sonnet 4.6 primary and Haiku 4.5 fallback with `max_switches=2`.

PolicyEngine US 2.0.4 computes SNAP, Medicaid, WIC, TANF, SSI, EITC, Oklahoma EITC, CTC and the refundable CTC. It runs locally with no credentials. No eligibility number in the product comes from a language model.

One `BedrockAgentCoreApp` on AgentCore Runtime in us-east-1 handles every action. A Lambda Function URL serves the page on GET and SigV4-signs `InvokeAgentRuntime` on POST, so the browser holds no keys. Cases persist in DynamoDB. The bench is Strands Evals 1.2.0: all 12 runs (six households, two repeats) passed with every fact and engine amount matching committed ground truth.

### Challenges we ran into

The defect that mattered most was found by a code review, not by a test. For the mixed-status household, the engine wrapper never set `ssn_card_type`, and PolicyEngine defaults it to `CITIZEN` independently of `immigration_status`. The product was showing $10,716 per year of refundable tax credits the household cannot legally claim. Setting `ssn_card_type` to `NONE` moved EITC from $7,316 to $0 and the refundable CTC from $3,400 to $0. A confident wrong number is worse than no number.

LIHEAP is not modeled for Oklahoma in the engine. The card says "not computed" and links to the agency. A model estimate of a heating benefit would have been worse than nothing.

### Accomplishments that we're proud of

Not one eligibility number comes from a language model. Privacy is a code path: identifiers are masked until a claim, and an outbound SSN is denied by an intervention. The immigration question is never answered by the agent: it files what the children qualify for and hands the parents' situation to a human with the facts already gathered. The engine review caught a five-figure error before anyone saw it on screen.

### What we learned

Structured output with a citation field is a stronger guard than any instruction. Requiring `source_msg_id` on every fact turns "do not make things up" from a request into a filter. Most of an intake conversation needs no model at all. Saying "the engine does not model this here" is a feature: LIHEAP is the most useful card on the senior's screen precisely because it refuses to give a number.

### What's next for Benefitline

More states. The engine covers federal rules and many state programs already; the work per state is the forms, the deadlines, and a coverage audit. Real SMS so a family texts a number instead of opening a link. An MCP surface so a case management system can call the screener directly. Deadline reminders on a real clock with a recertification calendar.

## Built with

python, strands-agents, strands-agents-tools, strands-evals, amazon-bedrock, claude-sonnet-4.6, claude-haiku-4.5, bedrock-agentcore-runtime, aws-lambda, lambda-function-url, amazon-dynamodb, policyengine-us, pydantic, boto3, html, css, javascript, sigv4

## Try it out links

- **Live demo:** https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/
- **GitHub:** https://github.com/Yazan-O/benefitline
- **Gallery:** built into the live demo (tap Gallery at the bottom)

## Image gallery

1. `submission/thumbnail.png` — Benefitline project thumbnail.
2. `submission/architecture.png` — Architecture: Lambda Function URL, AgentCore Runtime, tier router, Strands Graph, DynamoDB, guard hooks.
3. `submission/media/hero_phone.png` — Benefitline on a phone: benefit results with the statute link under each amount.
4. `submission/media/qr.png` — QR code for the live demo, works from any phone camera.
5. `submission/media/flow.gif` — One full session: intake questions, engine results with rule links, and a guard denying a tool call that carried an identifier.

## Video demo link

[VIDEO_URL]

## Submitter Type

Individual

## Country of Residence

United States

## Organization



## Track

Good Neighbor

## Public URL to your code repo

https://github.com/Yazan-O/benefitline

## Architecture diagram

`submission/architecture.png`

## AWS Builder ID

[OWNER FILLS]

## Live demo link

https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/

## Testing instructions

No login and no AWS account needed. Open the link on a phone or any browser.

1. Open https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/ and tap **Gallery** at the bottom.
2. Pick **Case 1** (single mother, two children, Tulsa). Tap **Play script**. The intake runs automatically in six questions with a counter on screen.
3. When the results appear, see SNAP at $632/month, WIC at $59.80/month, EITC at $7,316/year. Tap any dollar amount to open its statute link (SNAP links to 7 U.S.C. 2017(a)).
4. Go back to Gallery. Open **Case 3** (senior living alone, SNAP $148/month), then **Case 3b** (same senior after reporting medical costs, SNAP $298/month). One question about medicine moves the amount through the excess medical expense deduction.
5. Tap **Board** to see the coordinator view. All contact details show as dots. Type any name and tap **Claim** on a case to unmask it. Every other case stays masked.
6. Go back to Gallery and open **Case 4** (mixed-status family). The engine computes SNAP for the children ($546/month) but shows $0 for the federal EITC. The card explains why: the parents have no valid SSN. The agent escalates the parents' situation to a Decision Card for a human coordinator instead of answering the immigration question itself.
7. Scroll the ledger at the bottom of any case. Every action is logged. Tap **Undo** within ten minutes to reverse an action, not just the log entry.

## Bonus blog post URL

[BLOG_URL, post submission/BLOG_builder_aws.md on builder.aws with "Agents for Humans" in the title]
