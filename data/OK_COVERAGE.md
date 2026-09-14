# Oklahoma rule coverage in policyengine-us 2.0.4 (checked 2026-09-13)

Method: grep of the installed package for `OK:` keys in parameter files and for `gov/states/ok`
variables, then a live run of the six gallery households (`python src/compute_gallery.py`).
Bottom line: every program in the gallery except LIHEAP is computed for Oklahoma by the engine.
LIHEAP is flagged, not computed.

| Program | Oklahoma coverage in the engine | Evidence (package path) | Benefitline behavior |
|---|---|---|---|
| SNAP | Full: federal formula plus OK standard utility allowance (412/month FY2026), OK medical-expense standard (0, so actual expenses are used), OK utility rules | `parameters/gov/usda/snap/income/deductions/utility/standard/main.yaml` (OK key), `.../excess_medical_expense/standard.yaml` (OK key), `variables/gov/usda/snap/*` | Computed; amount and rule shown |
| Medicaid (SoonerCare) | Full: OK income limits per category (adult 138% FPL from 2021-07-01, infant, young child, older child, parent, pregnant, senior or disabled, medically needy), OK immigration rule | `parameters/gov/hhs/medicaid/eligibility/categories/*/income_limit.yaml` (OK keys), `.../undocumented_immigrant.yaml` | Eligibility per member; no dollar amount (coverage, not cash) |
| CHIP | **Not modeled as a separate program for Oklahoma**: `chip/child/income_limit.yaml` has `OK: -.inf`; children are covered under Medicaid (210% FPL) | `parameters/gov/hhs/chip/child/income_limit.yaml` | Not shown (dropped from the program list 2026-09-14 after review) |
| WIC | Federal program; income test by FPL; food-package values federal. `is_wic_at_nutritional_risk` defaults True, so every WIC amount is conditional on a clinic finding (7 CFR 246.7(e)); labeled so on screen | `variables/gov/usda/wic/*`, `parameters/gov/usda/wic/*` | Computed per member |
| TANF | Full Oklahoma program: payment standard, countable income, work-expense and dependent-care deductions, resource test (OAC 340:10-3-59) | `variables/gov/states/ok/dhs/tanf/*`, `parameters/gov/states/ok/dhs/tanf/*` | Computed |
| EITC (federal) | Federal | `variables/gov/irs/credits/earned_income/eitc.py` | Computed, annual |
| Oklahoma EITC | Full: 5% of the federal EITC recomputed with frozen tax-year-2020 federal parameters, prorated by the OK AGI share (`ok_eitc.py` docstring); Benefitline zeroes it when `eitc_eligible` is False because the OK variable does not check SSNs | `variables/gov/states/ok/tax/income/credits/eitc/ok_eitc.py` | Computed, annual |
| CTC (federal) and refundable part | Federal | `variables/gov/irs/credits/ctc/*` | Computed, annual |
| SSI | Federal | `variables/gov/ssa/ssi/ssi.py` | Computed |
| LIHEAP | **Not modeled for Oklahoma.** The engine models LIHEAP for DC, Illinois, Massachusetts and Riverside County, CA | `variables/gov/states/dc/doee/liheap/*`, `variables/gov/states/il/dceo/liheap/*`, `variables/gov/states/ma/doer/liheap/*`, `variables/gov/local/ca/riv/liheap/*`; no `ok` LIHEAP files | Flagged "screen with Oklahoma DHS"; no amount, no model guess |
| Child care subsidy (OK CCS) | Full Oklahoma program present (not in the gallery) | `variables/gov/states/ok/dhs/ccs/*` | Not used in v1; listed in README as available |

## Decision
Oklahoma stays the gallery state. No fallback state is needed. LIHEAP (case 3) is shown as a
deterministic flag with the agency link, labeled "not computed by the engine".

## Engine notes that affect the gallery
- `rent` is a person-level input in PolicyEngine; Benefitline assigns household rent to the head.
- Utilities are entered as both `utility_expense` and `heating_cooling_expense` on the SPM unit so the SNAP standard utility allowance applies (Oklahoma uses the SUA whenever a heating or cooling cost exists).
- Case 4 (mixed status): undocumented parents fail the SNAP and Medicaid immigration gates (`is_snap_immigration_status_eligible`, `is_medicaid_immigration_status_eligible` both False); the children pass. The engine prorates the ineligible parents' income for SNAP. Benefitline never reasons about immigration status in the model; it reports the engine's per-member gates and escalates the parents' question.
- Case 3 versus 3b: adding 180/month medical costs and the 2026 Part B standard premium of 202.90/month moves the SNAP allotment from 148 to 298 per month (the maximum for one person) through the excess medical expense deduction (7 CFR 273.9(d)(3)); this is the deduction scene.

## Review fixes (2026-09-14, from `_runs/2026-09-13_phase1_engine/REVIEW_engine.md`)
- `ssn_card_type` is a separate PolicyEngine input from `immigration_status`. Undocumented members are entered with `ssn_card_type="NONE"`; case 4 then gets EITC 0 (was 7,316), CTC 1,000 (two 500 other-dependent credits, was 4,400), refundable CTC 0 (was 3,400). SNAP unchanged at 546.
- `weekly_hours_worked` is an explicit input; a childless adult 18-59 with no hours is excluded by the SNAP work rule (HR1 ABAWD, no OK county waived), so the intake must ask hours before pricing such a household. `snap_excluded_member` is reported per member.
- `compute()` validates: one head, at most one spouse, at least one member, state OK, non-negative rent; it raises `EngineInputError` naming the missing fact.
- Assets are assumed zero. Harmless for OK SNAP (BBCE, `asset_limit.yaml` `OK: .inf`); load-bearing for the senior non-MAGI Medicaid pathway (case 3), labeled on screen as "if countable assets are under the limit".
