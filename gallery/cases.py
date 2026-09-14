"""The five gallery households (case 3 has two variants). Every household is fictional and is
labeled so on screen. Income and expense levels are typical for Tulsa, OK; the sources for the
typical levels are in data/SOURCES.md. Amounts in USD."""
from benefitline.engine import Household, Person

CASES = [
    Household(
        case_id="case1_single_mother_two_kids",
        members=[
            Person(age=29, employment_income=14 * 30 * 52, is_tax_unit_head=True),  # 21,840/yr
            Person(age=7),
            Person(age=4),
        ],
        rent=950, utility_expense=180,
    ),
    Household(
        case_id="case2_couple_newborn",
        members=[
            Person(age=31, employment_income=32_000, is_tax_unit_head=True),
            Person(age=28, is_breastfeeding=True, is_tax_unit_spouse=True),
            Person(age=0),
        ],
        rent=1_050, utility_expense=200,
    ),
    Household(
        case_id="case3_senior_alone",
        members=[Person(age=71, social_security_retirement=1_250 * 12, is_tax_unit_head=True)],
        rent=650, utility_expense=140,
    ),
    Household(
        case_id="case3b_senior_alone_with_medical",
        members=[Person(age=71, social_security_retirement=1_250 * 12, is_tax_unit_head=True,
                        other_medical_expenses=180 * 12,
                        medical_expense_health_insurance_premiums=202.90 * 12)],  # 2026 Part B standard premium (SOURCES)
        rent=650, utility_expense=140,
    ),
    Household(
        case_id="case4_mixed_status",
        members=[
            Person(age=38, employment_income=26_000, immigration_status="UNDOCUMENTED", ssn_card_type="NONE",
                   weekly_hours_worked=40, is_tax_unit_head=True),
            Person(age=35, immigration_status="UNDOCUMENTED", ssn_card_type="NONE", is_tax_unit_spouse=True),
            Person(age=9),   # US-born citizen
            Person(age=3),   # US-born citizen
        ],
        rent=900, utility_expense=170,
    ),
    Household(
        case_id="case5_on_snap_recert_due",
        members=[
            Person(age=44, employment_income=19_000, is_tax_unit_head=True),
            Person(age=12),
        ],
        rent=800, utility_expense=160,
    ),
]
