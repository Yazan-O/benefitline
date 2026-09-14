# Benefitline video shot list, 50 second cut

Total 0:50. Five shots. Generation, screen capture and editing are owned by the video
session; this file is the shot list, the tags, the voice lines and the sourced numbers.

Each shot is tagged **GENERATED** (Google Flow, Veo) or **REAL** (screen capture of the
running product, with the live link visible in the browser chrome).
Generated footage totals 10 seconds, open and close only. Real product footage is 40 seconds.

Three product moments survive the cut. Everything else is dropped:

- **R1, the engine on camera.** The rule link under each amount, tapped on screen. This is
  the claim the whole product rests on, that no model states a dollar figure.
- **R2, the medical deduction.** SNAP moving from 148 to 298 a month after one more question,
  uncut. This is the moment a judge sees money appear from a rule nobody asked about.
- **R3, the masked Decision Card.** Identifiers as dots, one tap to claim, the software
  refusing to answer the immigration question itself.

Dropped from the long cut, which is kept at
`_runs/2026-09-14_video_50s/VIDEO_SCRIPT_long.md`: the QR and cellular open, the intake and
counter, case 2 and WIC, the filing and ledger screens, case 5 and the guard refusal, the
architecture and bench, and the LIHEAP and AGPL honesty screen.

Rules this shot list obeys:

- Every dollar figure spoken is an engine number from `gallery/ground_truth.json`. No
  participation statistic is spoken in this cut.
- No LIHEAP amount is said or shown.
- The households are fictional. That is said out loud once, in G1.
- No model states a dollar figure. That is the closing line.
- All speech is English. No generated clip contains on-screen text, a logo, a readable
  document, or a readable dollar amount.
- Durations were set from the word count of each voice-over line at 2.6 words per second,
  then rounded up. The voice-over is 124 words, which is 48 seconds of speech.

## Shot list

| # | Start | Dur | Tag | What is on screen | Voice-over, verbatim | Source for every number |
|---|---|---|---|---|---|---|
| G1 | 0:00 | 0:07 | GENERATED | Open. A volunteer and a family at a folding table in a church hall, papers between them. Prompt G1 in [`FLOW_PROMPTS.md`](FLOW_PROMPTS.md). | "A volunteer in Tulsa sits an hour with one family. These households are fictional; the rules are real." | No numbers. |
| R1 | 0:07 | 0:14 | REAL | Case 1 results on the live page, the URL readable in the address bar. `https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/` Slow scroll over the cards, then a finger taps the RULE link under the SNAP amount, the statute page opens with its URL visible, then back. | "The model never says a number. It hands the facts to policyengine-us, and the engine prices them. Six hundred and thirty two dollars a month in SNAP, with the rule under it that you can tap." | engine: `gallery/ground_truth.json` case 1, SNAP 632.00 USD/month. Rule link target is the `rules` URL on the same record, 7 USC 2017(a). |
| R2 | 0:21 | 0:16 | REAL | Case 3 results, the SNAP card at $148 a month. The family answers the medical question. The card changes to $298 a month. Hold on the breakdown row "excess medical" and the citation 7 CFR 273.9(d)(3). Do not cut inside the change. | "One more question: what do you pay out of pocket for medicine. A household with someone over sixty deducts it. One hundred and forty eight dollars becomes two hundred and ninety eight, the most a one person household can get." | engine: ground_truth.json case3_senior_alone SNAP 148.00 USD/month, and case3b_senior_alone_with_medical SNAP 298.00 USD/month with `snap_excess_medical_expense_deduction 347.90`, `snap_net_income 0.0` and `snap_max_allotment 298.00`. Citation 7 CFR 273.9(d)(3), `data/OK_COVERAGE.md` line 30. The 2026 Part B premium of 202.90 is an input, not spoken. |
| R3 | 0:37 | 0:10 | REAL | Coordinator board, every identifier showing "•••• (claim to reveal)". The case 4 Decision Card, three options and a deadline. The coordinator taps Claim once and the contact details appear. | "A mixed status family. Names stay dots until a coordinator claims the case. The children qualify. The parents' question goes to a human." | engine: ground_truth.json case4_mixed_status, SNAP 546.00 USD/month for the two citizen children, shown not spoken. Masking: `src/benefitline/hooks.py` `MaskUntilClaimed`. |
| G2 | 0:47 | 0:03 | GENERATED | Close. The volunteer closing a laptop at the empty folding table. Prompt G2 in [`FLOW_PROMPTS.md`](FLOW_PROMPTS.md). | "The model asks. The rules engine decides." | No numbers. |

## Capture notes

1. Both REAL shots keep the browser address bar in frame with the live URL readable. Record
   after the deployment exists. `https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/`
2. R1 needs one genuine tap on a RULE link. The link text reads RULE; the statute URL only
   appears once the page opens, so the tap and the loaded page must both be in the shot.
   Pre-load the target page in a second tab so the tap is instant.
3. R2 must not be cut between 148 and 298.
4. R3's Claim is one tap, in one take. Do not cut between the masked row and the revealed one.

## UI states to pre-stage

1. Case 1 results, scrolled to the SNAP card, with the RULE link in frame and the statute page
   pre-loaded in a second tab.
2. Case 3 results at 148 dollars, with the medical question next in the queue.
3. Case 3b results at 298 dollars, the breakdown open to the excess medical row reading 347.90.
4. Case 4 board view with every identifier masked and the coordinator name field filled but
   not submitted.
5. Case 4 Decision Card open, three options, evidence chips, deadline visible.

## Numbers deliberately not spoken

- The Medicare Part B premium. Case 3b uses the sourced 2026 standard premium of 202.90
  (`data/SOURCES.md`, Part B row). It is an input rather than an engine result, so the
  voice-over says "what you pay out of pocket" and names no premium.
- Any SNAP, WIC or EITC participation percentage. They are sourced in `README.md`, and there
  is no room for them in 50 seconds.
- A bench pass count. The bench is being rerun after an intake fix; results land in
  `evals/RESULTS.md`. **[pending: evals/RESULTS.md]**
- Fourteen dollars an hour, the wage in case 1. It is a chosen demo wage, not a measured
  statistic.

## Placeholders

- The live Function URL, readable in every REAL shot. `https://2rngqllksv5sxof5hp55ekaowi0lsvzw.lambda-url.us-east-1.on.aws/`
