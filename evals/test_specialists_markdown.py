"""D12: the family reads a text message, not markdown.

Live version 6 (submission/media/hero_phone.png) showed the explainer's message arriving with
"#" headings, "---" rules, "**bold**" and emoji, which a phone renders as raw punctuation.
The guarantee is code-owned, like the amount check: every narrative field is scrubbed in the
validator before it is merged, whatever the prompt asked for. Offline, no Bedrock call.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benefitline import forms, specialists                          # noqa: E402
from benefitline.schemas import (                                   # noqa: E402
    CaseRecord, Explanation, FilingPlan, FormField, HouseholdFacts,
)
from test_forms_units import case1_facts, engine_result             # noqa: E402
from test_specialists_live import TRANSCRIPT                        # noqa: E402

MARKDOWN = """# Your benefits

---

**SNAP**: about $291 per month. *The agency decides.*

- These are estimates 🎉
> Call us back 😀
"""

MARKERS = ("#", "**", "---", "*", "🎉", "😀", ">")


def _case() -> CaseRecord:
    return CaseRecord(
        case_id="case1_single_mother_two_kids",
        session_id="d12",
        gallery_case="case1_single_mother_two_kids",
        facts=case1_facts(),
        transcript=list(TRANSCRIPT),
        engine_result=engine_result("case1_single_mother_two_kids"),
        status="computed",
    )


def test_markdown_never_reaches_the_family_message():
    case = _case()
    snap = next(p for p in case.engine_result.programs if p.key == "snap")
    raw = Explanation(
        language="en",
        message_to_family=MARKDOWN,
        per_program=[{"key": "snap", "one_line": "## **SNAP**: about "
                      f"{specialists._fmt_amount(snap.amount)} per month. 🎉",
                      "rule_url": snap.rules[0]}],
    )
    cleaned = specialists.clean_explanation(raw, case)
    text = cleaned.message_to_family
    assert text, "the scrub emptied the message"
    for marker in MARKERS:
        assert marker not in text, f"{marker!r} survived into the family message: {text!r}"
    one_line = cleaned.per_program[0]["one_line"]
    for marker in MARKERS:
        assert marker not in one_line, f"{marker!r} survived into the per-program line"
    assert "SNAP" in one_line and "estimates" in text


def test_markdown_never_reaches_a_filing_field():
    case = _case()
    draft = forms.plan_filing("snap", case.facts, case.engine_result, case=case)
    label = forms.free_text_fields("snap")[0]
    written = FilingPlan(
        program="snap", form_name=draft.form_name, form_url=draft.form_url,
        fields=[FormField(form_field=label,
                          value="**A friend** helps with the rent 🎉\n- every month [from: m4]",
                          source_msg_id="m4")],
        documents_needed=list(draft.documents_needed), submit_route=draft.submit_route,
    )
    merged = specialists._merge_free_text(draft, written, {"m4"})
    value = next(f.value for f in merged.fields if f.form_field == label)
    for marker in ("**", "🎉", "- "):
        assert marker not in value, f"{marker!r} survived into the filing field: {value!r}"
    assert "A friend helps with the rent" in value
