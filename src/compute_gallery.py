"""Phase 1 done-check: compute every gallery case, print amounts per program with the engine
version, and write gallery/ground_truth.json (the bench pins these numbers)."""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "gallery"))
from benefitline.engine import compute, ENGINE, ENGINE_VERSION, household_to_dict  # noqa: E402
from cases import CASES  # noqa: E402


def main():
    print(f"engine: {ENGINE} {ENGINE_VERSION}")
    results = {}
    for h in CASES:
        t0 = time.time()
        r = compute(h)
        r["household"] = household_to_dict(h)
        results[h.case_id] = r
        print(f"\n== {h.case_id}  (state {h.state}, {len(h.members)} members, {time.time() - t0:.1f}s)")
        for p in r["programs"].values():
            amt = "" if p["amount"] is None else f"{p['amount']:>10.2f} {p['unit']}"
            mem = f"  members={p['eligible_members']}" if "eligible_members" in p else ""
            print(f"  {p['label']:<40} eligible={str(p['eligible']):<5} {amt}{mem}")
        for p in r["not_modeled"].values():
            print(f"  {p['label']:<40} NOT MODELED for OK -> flagged for agency screening")
        sd = r["snap_detail"]
        print(f"  snap detail: gross={sd['snap_gross_income']} net={sd['snap_net_income']} "
              f"max={sd['snap_max_allotment']} shelter_ded={sd['snap_excess_shelter_expense_deduction']} "
              f"medical_ded={sd['snap_excess_medical_expense_deduction']} SUA={sd['snap_utility_allowance']}")
    out = os.path.join(HERE, "..", "gallery", "ground_truth.json")
    with open(out, "w") as f:
        json.dump({"engine": ENGINE, "engine_version": ENGINE_VERSION, "cases": results}, f, indent=2, default=str)
    print(f"\nwrote {os.path.relpath(out)}")


if __name__ == "__main__":
    main()
