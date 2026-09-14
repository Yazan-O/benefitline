# Benefitline

**Tagline:** The model asks. The rules engine decides.

## Inspiration

A community help desk is where families go when they need food or health coverage. A
mosque, a church, a food pantry, a school family liaison. The volunteer at that desk cannot
hold nine program rulebooks in their head.

SNAP reached 88 percent of eligible individuals in FY2022.[^1] The gap is widest for the
households that walk into a help desk: 76 percent participation for households with earned
income, 55 percent for elderly individuals, 50 percent for eligible noncitizens.[^1]
WIC reached 56.1 percent of eligible people in 2023, and 24.0 percent in nonmetro areas
against 61.1 percent in metro areas.[^2]

The rules are public. The arithmetic is deterministic. What is missing is somebody to
run it.

[^1]: USDA FNS, "Trends in Supplemental Nutrition Assistance Program Participation Rates:
Fiscal Year 2020 and Fiscal Year 2022", Oct 2024, Tables 1 and 2.
https://www.fns.usda.gov/sites/default/files/resource-files/ops-snap-trendsfy20-fy22-report.pdf

[^2]: USDA FNS, "National and State-Level Estimates of WIC Eligibility and Program Reach
in 2023", Table 2.
https://www.fns.usda.gov/sites/default/files/resource-files/wic-eer2023-report.pdf

## What it does

A family sends one text line. Benefitline asks the questions the rules need, one at a
time, and shows a counter: "questions asked: 6 of a possible 6."

Then it hands the facts to policyengine-us 2.0.4 and shows exact amounts, each with the
rule underneath. A single mother of two in Tulsa sees SNAP at $632 per month, Medicaid for
the children, and WIC at $59.80 per month. On the tax side: the federal EITC at $7,316,
the Oklahoma EITC at $269.57, and the Child Tax Credit at $4,400 with $2,901 refundable.
Every card links its rule: 7 U.S.C. 2017(a) under SNAP, 7 CFR 246.7(e) under WIC. A card
can be expanded to show the arithmetic: gross $1,820.00, standard deduction -$209.00,
earned income deduction of 20 percent -$364.00. Above the cards it says "An estimate from
the rules as written. The agency makes the decision."

From there the agent produces a filing plan per program: the real form (OKDHS 08MP001E for
SNAP, the SoonerCare online application for Medicaid), the document checklist, and the
deadline.

A coordinator sees one board with every case. Names, phone numbers and addresses stay
masked until somebody types their name and claims the case. The mask is a hook on the tool
result, not an instruction in a prompt.

The agent interrupts the coordinator once per case, with a Decision Card: the question, the
options, a recommended default, the deadline, and the evidence behind it. One tap answers
it. The same answer given twice becomes a standing rule for that organization.

Every action is written to a ledger with a 10-minute undo window. Undo reverses the
action, not just the log line.

The demo is a gallery of five fictional households, shown as six cases, because the senior
appears with and without medical costs. That is the scene where one question about medicine
moves the SNAP amount from $148 to $298 per month through the excess medical expense
deduction (7 CFR 273.9(d)(3)). The page says it plainly: fictional households, real rules.
Every amount comes from policyengine-us run on real Oklahoma inputs.

| Case | Household | SNAP per month |
|---|---|---|
| 1 | Single mother, two children, Tulsa | $632 |
| 2 | Couple with a newborn, Tulsa | $357 |
| 3 | Senior living alone | $148 |
| 3b | Same senior, with medical costs | $298 |
| 4 | Mixed-status family | $546 |
| 5 | Already on SNAP, recertification due | $433 |

## How we built it

**Strands Agents 1.55.1 is the whole agent layer.**

`Agent` with structured output does intake. Each turn returns an `IntakeTurn` with a list
of facts, and every fact carries the id of the family message it came from.
`validate_facts()` drops any fact whose `source_msg_id` is not a real family message before
it is stored. A model cannot invent an income figure and have it survive.

Triage is tiered, and tier 0 makes no model call. Language detection by script,
yes/no/number parsing, the missing-facts check, the engine call, the form fill, the
deadline arithmetic and the masks are all deterministic code. Tier 1 handles replies of six
words or fewer on Haiku 4.5. Tier 2 sends free text to Sonnet 4.6. In the bench run a case
takes four to six tier 0 turns, no tier 1, and one to three tier 2.

`ModelRouter` holds Sonnet 4.6 as primary, the same Sonnet 4.6 on Bedrock's other inference
profile second (the daily token quota is metered per profile), and Haiku 4.5 last, with
`max_switches=2`.
The fallback is held to the same bar: the same `validate_facts()` runs on whichever model
answered.

`GraphBuilder` runs three specialists in parallel: Explainer on Sonnet, Filing on Sonnet,
Risk on Haiku. Each edge into the judgment node carries an AND condition,
`all_three_complete`, so judgment starts only when all three have landed. The judgment node
is deterministic and makes no model call.

Guards are hooks and one intervention, enforced in code. `MaskUntilClaimed` masks
identifiers in any tool result bound for the board. `NoIdentifiersOut` denies any outbound
message matching an SSN or A-number pattern, returning `Deny`: a refusal in code, not a
request in a prompt. `TraceRedactor` scrubs traces and `LedgerWriter` records every action.

**PolicyEngine decides.** policyengine-us 2.0.4 runs locally with no credentials. It
computes SNAP, Medicaid, WIC, TANF, SSI, EITC, the Oklahoma EITC, CTC and the refundable
CTC for Oklahoma. Ground truth for all six cases is committed in
`gallery/ground_truth.json` with the engine version. No eligibility number in the product
comes from a language model.

**AWS.** One `BedrockAgentCoreApp` entrypoint on AgentCore Runtime in us-east-1 handles
every action: `chat`, `start_case`, `board`, `board_ask`, `claim`, `answer_card`, `undo`,
`gallery`, `reset`. A Lambda Function URL sits in front of it. The Lambda serves
`web/index.html` on GET and SigV4-signs `InvokeAgentRuntime` on POST, so the browser holds
no keys and the Lambda itself makes no model call. Cases persist in DynamoDB through
`DynamoStore`, with `LocalJsonStore` as the offline path.

**What is measured, and by whom.** The bench is Strands Evals 1.2.0. A user simulator
plays each household with paraphrased answers, and every run is scored by six deterministic
checks plus a Haiku judge on wording. The six checks are: questions asked, facts matched
against ground truth field by field, engine output compared with
`gallery/ground_truth.json` to two decimals, every fact cited to a family message, no
dollar figure in any model reply, and a reply in the language the family wrote in. The
engine's own output is the answer key, so a paraphrase that moves an amount is caught.

Bench verdict: PASS. Run 2026-09-14 11:42 America/Chicago, all 12 runs (six households,
two repeats) within six questions with every fact and engine amount matching ground truth.
The morning run before it failed three of six cases; each defect got a code validator and
the rerun is the table in `evals/RESULTS.md`.

## Challenges

**The defect that mattered most was found by a review of the engine wrapper, not by a
test.** For the mixed-status household, the wrapper never set `ssn_card_type`, and
PolicyEngine defaults it to `CITIZEN` independently of `immigration_status`. The product
was showing $10,716 per year of refundable tax credits that household cannot legally claim.
Setting `ssn_card_type` to `NONE` for undocumented members moved EITC from $7,316 to $0
and the refundable CTC from $3,400 to $0. SNAP stayed at $546, because the children are
eligible and the engine prorates the parents' income. What is left is $1,000, two Credits
for Other Dependents at $500 each under 26 USC 24(h)(4), and that assumes ITINs. A
confident wrong number is worse than no number.

**The SNAP work requirement for adults without dependents.** A childless adult aged 18 to
59 with no work hours is excluded from SNAP, and no Oklahoma county is waived. Weekly hours
worked had to become an explicit intake question. The engine now refuses to price such a
household and names the missing fact instead of assuming zero.

**LIHEAP is not modeled for Oklahoma.** policyengine-us models LIHEAP for DC, Illinois,
Massachusetts and Riverside County, CA. The card reads "not computed by the engine, screen with the agency"
and links to Oklahoma DHS. A model estimate of a heating benefit would have been worse than
nothing.

**Holding both models to one bar.** It is tempting to let the fallback model be looser.
Instead the validation runs after the model rather than inside it, so Sonnet and Haiku are
checked by the same function against the same schema.

## Accomplishments

- Not one eligibility number in the product is produced by a language model. Every amount
  is engine output with its statute or regulation link on screen.
- `python -m pytest evals/ -q -m "not live"` returned 262 passed on 2026-09-14 with no model
  calls; the full suite with live Bedrock tests ran 314 tests the same day, and the three
  that failed were fixed at the root and passed on rerun.
- Privacy is a code path. Identifiers are masked until a claim, and an outbound SSN or
  A-number is denied by an intervention.
- The immigration question is never answered by the agent. It files what the children
  qualify for and hands the parents' question to a human with the facts already gathered.
- Six gallery cases with committed ground truth, so a regression in intake shows up as a
  changed dollar amount.
- The engine review caught a five-figure error before anyone saw it on screen.

## What we learned

Structured output plus a citation field is a stronger guard than any instruction. Requiring
`source_msg_id` on every fact turns "do not make things up" from a request into a filter.

Most of an intake conversation needs no model at all. Counting the tiers made that
concrete: most turns in a case are tier 0.

A rules engine is an opinionated collaborator. `ssn_card_type` defaulting independently of
`immigration_status` is defensible inside PolicyEngine and wrong for this use, and only
reading the variable found it. Wrapping an engine means owning its defaults.

A user simulator finds different bugs than a scripted transcript. Paraphrase moves income
from one member to another, or reads "I get some help with medicine" as a dollar amount.
Those runs and their mismatches are recorded in `evals/RESULTS.md` rather than smoothed
over.

Saying "the engine does not model this here" is a feature. LIHEAP is the most useful card
on the senior's screen precisely because it refuses to give a number.

## What's next for Benefitline

- More states. The engine already covers the federal rules and many state programs; the
  work per state is the forms, the deadline rules, and a coverage audit like
  `data/OK_COVERAGE.md`.
- Oklahoma child care subsidy. The full Oklahoma program is already in policyengine-us at
  `variables/gov/states/ok/dhs/ccs/` and is not in the v1 gallery.
- An MCP surface exposing `screen_household()`, so an existing case management system can
  call the screener directly.
- Real SMS, so a family texts a number instead of opening a link.
- Deadline reminders on a real clock, with a recertification calendar feed a coordinator
  can subscribe to.

## Built with

`python` `strands-agents` `strands-agents-tools` `strands-evals` `amazon-bedrock`
`claude-sonnet-4.6` `claude-haiku-4.5` `bedrock-agentcore-runtime` `aws-lambda`
`lambda-function-url` `amazon-dynamodb` `policyengine-us` `pydantic` `boto3` `html` `css`
`javascript` `sigv4`

## Try it

**Live demo:** https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/

Scan the QR code on the submission page or at the end of the video, open it on a phone,
and pick a household from the gallery. Play the scripted replies or type your own. Nothing
is uploaded and no account is needed.

**Run locally, no AWS account, no build step:**

```
git clone https://github.com/Yazan-O/benefitline.git
cd benefitline
pip install -r requirements.txt
python -m http.server 8000 --directory web
```

Open `http://localhost:8000/`. The page also opens from the filesystem. In mock mode it
serves the committed gallery, so every amount on screen is real engine output from
`gallery/ground_truth.json`.

To recompute the engine numbers:

```
python src/compute_gallery.py
```

To run the tests and the user simulator bench (the bench needs AWS credentials in `.env`
and Bedrock model access):

```
python -m pytest evals/ -q
python evals/bench.py
```

## Licensing and data

Benefitline is MIT licensed, copyright 2026 Mohamad Yazan Sadoun. Its source is published
in full.

It calls policyengine-us 2.0.4, which is AGPL-3.0. Benefitline installs the released
wheel from PyPI and does not modify it, and only `src/benefitline/engine.py` imports it.
The engine version is printed beside every number in the product, and the corresponding
source at the pinned version is at https://github.com/PolicyEngine/policyengine-us. The
rule for this repository is that the engine is never patched locally. Every third-party
license is recorded in `submission/LICENSE_NOTES.md`.

Every number in this submission traces to a row in `data/SOURCES.md`, with its URL and the
date it was fetched. The demo households are fictional and are labeled fictional on screen.
No real family's data is in this repository. All amounts are estimates and the agency makes
the decision.

## Track

Good Neighbor.
