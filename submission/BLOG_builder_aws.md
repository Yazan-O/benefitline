# Agents for Humans: the model asks, the rules engine decides

A volunteer at a mosque help desk in Tulsa sits with a family and nine rulebooks. SNAP has
its own income test. Medicaid has another. WIC adds a clinic visit. The tax credits live in
a different agency entirely. The volunteer knows the families and does not know the rules.
People leave with a guess.

The gap is measurable. In FY2022, 88 percent of individuals eligible for SNAP received it,
the highest rate on record.[^1] The same report breaks that number by group: 76 percent
for households with earned income, 50 percent for eligible noncitizens, 55 percent for
elderly individuals.[^2] Those three groups are most of the people who walk into a
community help desk. WIC reached 56.1 percent of eligible individuals in 2023.[^3] The IRS
says four out of five eligible taxpayers receive the Earned Income Tax Credit.[^4]

The target is not another chatbot that talks about benefits. It is a system that says a
number a family can act on, and shows the rule it came from.

I built Benefitline for the AWS Agents for Humans hackathon. A family texts one line. The
agent asks the few questions that matter, runs the real federal and Oklahoma rules, and
answers with dollar amounts and statute links. Then it drafts the applications and hands
the coordinator only the questions a human must answer.

## Why a model must never state an eligibility amount

A language model can produce a SNAP figure that reads correctly and is wrong. There is no
way for the family to tell. The failure is quiet, confident, and costs a household
groceries.

Eligibility is not a judgment call. It is arithmetic over statute. For SNAP: a gross
income test, a net income test, a standard deduction, a shelter deduction and a utility
allowance. Then the maximum allotment minus 30 percent of net income (7 U.S.C. 2017(a)).
That arithmetic already exists as open source. PolicyEngine US is a microsimulation of the
federal and state tax and benefit system. It runs locally with no credentials. It powers
MyFriendBen, a production benefits screener.[^5]

The boundary in Benefitline is hard. The model gathers facts and explains results.
`policyengine-us` 2.0.4 computes every amount. No eligibility number in the product comes
from a language model. The wrapper `src/benefitline/engine.py` says so in its first line,
and it is the only file that imports `policyengine_us`.

## The design

**Tiered triage.** Most turns in a benefits conversation are a number or a yes. Reading
those does not need a model. Tier 0 is plain Python: language detection by script,
single-field parsing, the missing-facts check, the engine call, form filling, deadline
math, and the masks. Tier 1 is Haiku for short replies tier 0 could not parse. Tier 2 is
Sonnet for everything else. The counter is on screen, so a judge can watch how few turns
reach the large model.

From `src/benefitline/triage.py`:

```python
def route(case, message: str) -> int:
    """0 when a short reply fully answers the pending question, 1 for six words or fewer
    that tier 0 could not read, 2 otherwise (DESIGN: "tier 2: everything else")."""
    pending = pending_key(case)
    words = len((message or "").split())
    if (pending and words <= TIER0_MAX_WORDS
            and tier0_parse(pending, message, "probe") is not None):
        return 0
    return 1 if words <= 6 else 2
```

**Intake with a citation on every fact.** The intake agent returns a structured
`IntakeTurn`. Each fact carries the id of the message it came from. Before anything reaches
the engine, one validator drops any fact whose source is not a family message in this
transcript. The same validator runs on the Sonnet path and the Haiku path, so the fallback
model cannot lower the bar.

From `src/benefitline/facts.py`:

```python
def validate_facts(facts: HouseholdFacts, transcript: list[dict]) -> HouseholdFacts:
    """Drop every Fact whose `source_msg_id` is not a family message in this transcript.

    Same bar for both models and for tier 0: a value the family never said does not
    reach the engine. Every drop is logged with the field and the id that failed.
    """
    allowed = {str(e.get("id")) for e in transcript if e.get("role") in FAMILY_ROLES}
    clean = facts.model_copy(deep=True)
    dropped: list[str] = []
    # Before the citation sweep: the ages this reads may themselves be dropped below, and
    # a flag on a baby is wrong whether or not the age behind it survives.
    _clear_infant_caregiver_flags(clean)

    def keep(field: str, f: Optional[Fact]) -> Optional[Fact]:
        if f is None:
            return None
        if f.source_msg_id in allowed:
            return f
        dropped.append(f"{field}(src={f.source_msg_id!r})")
        return None
```

**The engine decides, and the rule travels with the number.** Every PolicyEngine variable
carries a statute or regulation reference. Benefitline carries that URL out with the amount
and prints it under the figure on screen. A judge can tap the SNAP number and land on
7 U.S.C. 2017.

**Three specialists in parallel, then a deterministic judgment.** After the engine runs, a
Strands `Graph` fans out to an explainer, a filing agent, and a risk agent. Graph runs
nodes in parallel (Swarm does not). The fourth node is not an agent. It is deterministic
code that merges the three outputs and decides what happens next. Graph edges fan out on
OR, so the wait for all three is written as a condition on each edge.

From `src/benefitline/specialists.py`:

```python
def all_three_complete(state) -> bool:
    """The AND join. Python graph edges fan out on OR, so the wait is written as a condition."""
    return {"explainer", "filing", "risk"}.issubset(set(state.results))


def build_graph(case: CaseRecord, payload: dict, collector: dict,
                store: Optional[Store] = None):
    builder = GraphBuilder()
    builder.add_node(ExplainerNode("explainer", 2, case, payload, collector), "explainer")
    builder.add_node(FilingNode("filing", 2, case, payload, collector), "filing")
    builder.add_node(RiskNode("risk", 1, case, payload, collector), "risk")
    builder.add_node(JudgmentNode(case, collector, store=store), "judgment")
    for node in ("explainer", "filing", "risk"):
        builder.set_entry_point(node)
        builder.add_edge(node, "judgment", condition=all_three_complete)
    return builder.build()
```

**One interrupt, and a ledger for everything else.** If the risk node escalates, the
judgment node writes one Decision Card: the situation in two lines, two or three options
with consequences, a default, and a link to the evidence. One tap answers it. A repeated
answer becomes a standing rule. Everything the agent does without asking becomes a ledger
row with a ten-minute veto window, so a coordinator can undo a filing before it is final.

**Guards in code, not in prompts.** A prompt that says "never reveal the address" is a
wish. Benefitline enforces it with Strands hooks and an intervention. `MaskUntilClaimed`
masks contact details on the coordinator board until that coordinator claims the case.
`NoIdentifiersOut` denies any outbound call whose arguments contain an SSN or A-number,
nested values included. `TraceRedactor` strips the same patterns from logs. `LedgerWriter`
records every call, denied ones included.

## What a review of the engine caught

The most useful hour of the build was a review of the engine wrapper against PolicyEngine's
own source. It came back "FIX REQUIRED" on the mixed-status demo household.

From `_runs/2026-09-13_phase1_engine/REVIEW_engine.md`: "The mixed-status case (case 4)
shows $10,716/year of refundable tax credits the household cannot legally claim, because
the wrapper never sets `ssn_card_type` and PolicyEngine defaults it to `CITIZEN`
independently of `immigration_status`."

The mechanism is exact. `filer_meets_eitc_identification_requirements` reads
`ssn_card_type`, not `immigration_status`, and the CTC adult SSN requirement turns on from
2025-01-01. The wrapper was setting immigration status and assuming the rest followed. It
does not follow. A lawful permanent resident has a valid SSN. The two inputs cannot be
derived from each other, so intake collects the card type as its own fact.

With the input set, case 4's EITC goes from 7,316 to 0 and its refundable CTC from 3,400
to 0. SNAP stays at 546 per month. The defect was in a deterministic file, a reviewer
could find it by reading statute, and the fix is pinned by committed ground truth. A wrong
number produced inside a model would have shipped.

## Limits

Oklahoma only, because rule coverage was verified program by program for one state. LIHEAP
is not modeled by the engine for Oklahoma, so it is a flag with the agency link and no
amount. WIC amounts are conditional on a clinic nutritional risk finding. Assets are
assumed zero, which is harmless for Oklahoma SNAP and load-bearing for the senior Medicaid
pathway (labeled on screen). Every amount is labeled an estimate, and the agency decides.

## Running it

The demo households are fictional and their numbers come from the engine.

```
python -m pytest evals/ -q                   # the bench against committed ground truth
python src/app.py                            # local AgentCore runtime on :8080
python deploy/deploy.py                      # DynamoDB, runtime, Lambda Function URL
```

Live demo: https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/

Code: https://github.com/Yazan-O/benefitline (MIT)

Built for the **AWS Agents for Humans** hackathon, Good Neighbor track.

## License

Benefitline is MIT. `policyengine-us` is AGPL-3.0 and is used as a separate, unmodified
library installed from PyPI. Notes on the network use clause and the full third-party
license list are in `submission/LICENSE_NOTES.md`.

[^1]: USDA FNS, "Trends in Supplemental Nutrition Assistance Program Participation Rates:
Fiscal Year 2020 and Fiscal Year 2022", Oct 2024, Table 1 p.9 and exec summary p.xiii.
https://www.fns.usda.gov/sites/default/files/resource-files/ops-snap-trendsfy20-fy22-report.pdf
[^2]: Same report, Table 2 p.10, FY2022 column.
https://www.fns.usda.gov/sites/default/files/resource-files/ops-snap-trendsfy20-fy22-report.pdf
[^3]: USDA FNS, "National and State-Level Estimates of WIC Eligibility and Program Reach
in 2023", Table 2.
https://www.fns.usda.gov/sites/default/files/resource-files/wic-eer2023-report.pdf
[^4]: IRS, EITC Central, "50 years of Earned Income Tax Credit".
https://www.irs.gov/tax-professionals/eitc-central/50-years-of-earned-income-tax-credit
[^5]: PolicyEngine, "MyFriendBen Launches in North Carolina, Using PolicyEngine API",
2025-04-05.
https://www.policyengine.org/us/research/myfriendben-nc
