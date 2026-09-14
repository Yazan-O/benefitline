# Oklahoma SNAP paper application: current form numbers and verbatim field labels
Sources fetched 2026-09-13

## Bottom line first

Applying for SNAP on paper in Oklahoma is **not one form, it is a three-form packet**. Form
08MP001E prints this on its own page 1: "For use with Forms 08MP002E, Eligibility Information for
Benefits, and 08MP003E, Rights, Responsibilities, and Signature for Benefits."

- **08MP001E "Request for Benefits"** — still current. Program selection, contact info, expedited
  screening, household roster (demographics only), authorized representative. Rev. **4/14/2026**.
- **08MP002E "Eligibility Information for Benefits"** — income, expenses/deductions, resources.
  Rev. **3/26/2026**.
- **08MP003E "Rights, Responsibilities, and Signature for Benefits"** — declarations, penalty
  warnings, signature. Rev. **4/1/2026**.

A screener that claims to cover "the Oklahoma SNAP application" must cover all three. 08MP001E on
its own carries no income, expense, or deduction fields except the seven-day expedited screening
dollar questions.

Revision dates above are read from each PDF's own printed page footer (e.g. "08MP001E 4/14/2026
Page 1 of 10"), not from a web page.

---

## 1. Form identity and currency

| Form | Title (as printed) | Rev. (printed footer) | Pages | PDF URL |
|---|---|---|---|---|
| 08MP001E | Request for Benefits | 4/14/2026 | 10 | https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/okdhsformresults/08mp001e.pdf |
| 08MP002E | Eligibility Information for Benefits | 3/26/2026 | 11 | https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/okdhsformresults/08mp002e.pdf |
| 08MP003E | Rights, Responsibilities, and Signature for Benefits | 4/1/2026 | 9 | https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/okdhsformresults/08mp003e.pdf |

**Currency evidence.** The live OKDHS SNAP services page
(https://oklahoma.gov/okdhs/services/snap.html) links `08mp001e.pdf` as the printable Request for
Benefits form, and the PDF's own footer prints `08MP001E 4/14/2026` on every page. So the
historical number 08MP001E is still the current number.

**Route tried and failed.** The OKDHS forms index at
https://oklahoma.gov/okdhs/searchcenter/okdhsformresults.html is a JavaScript search box with no
static form list; fetching it returns navigation chrome only, no form numbers, titles, or revision
dates. No static forms index with revision dates was reachable. The evidence above (live page link
+ printed PDF footer) is what stands in for it.

## 2. Online application route

- **https://www.okdhslive.org/** — the online route named on the OKDHS SNAP page and linked from
  OHCA's Where to Apply page as `https://okdhslive.org`.
- **Status at fetch time (2026-09-13):** okdhslive.org redirected to
  `https://www.okdhslive.org/TempDown.aspx`, which reads verbatim: "The OKDHSLive! website is
  currently unavailable." and "If you have questions or need assistance and it is between the hours
  of 8am and 5pm you may call (405)522-5050." The page stamps itself `9/13/2026 ... 7:51 PM`.
  This is an outage at fetch time, not evidence of replacement: OHCA's Where to Apply page still
  links okdhslive.org as the OHS application route.
- Screen-reader help number stated on that page: "one, eight, seven, seven, six five three, four
  seven nine eight" (1-877-653-4798).
- **Document upload:** https://changerequest.dhs.ok.gov/UploadDocuments
  (source: https://oklahoma.gov/okdhs/services/snap.html)

---

# 3. Form 08MP001E "Request for Benefits" — verbatim field labels

Source: https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/okdhsformresults/08mp001e.pdf
(extracted with pdfplumber from the downloaded PDF). Section headings below are the form's own.

### Header block (all pages)
`Date` · `Case name` · `Case #` · `County #` · `Supervisor #` · `Worker #`

### Incomplete Application
- `First name` · `M.I.` · `Last name`
- `Mailing address, street or PO Box` · `City` · `State` · `ZIP code`
- `I give OKDHS permission to check the information I give on this form to make sure it is true.`
- `I understand the names and social security numbers I give will be used to obtain information from other state and federal agencies.`
- `I give OKDHS permission to share information with other agencies.`
- `Signature` · `Date`
- `I would like to be contacted by phone. My phone number is:`

### What You Need To Do To Get Started
Programs to check (verbatim labels):
- `Supplemental Nutrition Assistance Program (SNAP)`
- `Child Care Subsidy`
- `Health Care Coverage - SoonerCare (Medicaid)`
- `State Supplemental Payment (SSP)`
- `Temporary Assistance for Needy Families (TANF)`
- `Diversion Assistance`

### Your Rights
Narrative only, no input fields.

### Households Eligible to Get a Food Benefit Decision Faster
Screening questions (verbatim):
- `How much money did you get or will you get this month from working (total amount before taxes?) $`
- `How much other money did you get or will you get from all other sources this month including gambling or lottery winnings (total amount)? $`
- `How much cash do you have? $`
- `How much money do you have in bank accounts? $`
- `How much do you pay for your rent, mortgage, or, if homeless, for sleeping accommodations? $`
- `Do you pay the heating or cooling bill where you live?` Yes/No
- `Are you a seasonal or migrant farm worker?` Yes/No
- `Does anyone in your household receive tribal food commodities?` Yes/No
- `Have you received or do you expect to receive food benefits in another state for this month?` Yes/No
- `If so, which state?`

Stated expedited criteria (verbatim):
- `households with less than $150 gross monthly income and liquid resources less then $100;`
- `households with monthly rent or mortgage and/or utilities which cost more than the combined monthly gross income and liquid resources; and`
- `destitute migrant or seasonal farm worker households with liquid resources less than $100.`

### How Can We Contact You?
- `First name` · `M.I.` · `Last name`
- `Mailing address, street or PO Box` · `City` · `State` · `ZIP code`
- `Street address or directions to your home, if different than mailing address`
- `Phone number where you can be reached` · `Apartment or lot number` · `Email address`
- `Do you need an interpreter?` Yes/No · `If yes, what language do you speak?`

### Schedule My Interview
- `Please put an X in the table for the days and times you are available for your interview:`
- Grid: `Time of day` × `Monday` `Tuesday` `Wednesday` `Thursday` `Friday`; rows `Morning`, `Afternoon`

### What You Will Need to Bring to Your Interview
- `proof of identity, such as driver license or school identification;`
- `Social Security number or card for everyone who wants benefits. If you are only applying for child care benefits, Social Security numbers are not required;`
- `proof of citizenship for everyone who wants benefits;`
- `proof of legal status for anyone who is not a U.S. citizen and wants benefits;`
- `proof of income for everyone living with you, such as pay stubs or award letters;`
- `proof of all resources, such as bank accounts, car titles, or land; and`
- `proof of your need for child care, such as your work or school schedule, and the name of the place you want to use to care for your child.`

### Authorized Representative Information
**Food Benefits**
- `Name` · `Date of Birth` · `Social Security number`
- `Mailing address, street or PO Box` · `City` · `State` · `ZIP code`
- `Phone number` · `Relationship to you`
- `Do you want this person to apply for or renew food benefits on your behalf?` Yes/No
- `Do you want this person to be issued an EBT card in order to buy groceries for you?` Yes/No

**Child Care Subsidy**
- `Name` · `Date of Birth` · `Social Security number`
- `Mailing address, street or PO Box` · `City` · `State` · `ZIP code`
- `Relationship to you` · `Phone number`
- `Do you want this person to apply for or renew child care benefits on your behalf?` Yes/No
- `Do you want this person to be issued an EBT card in order to record your child's attendance at the child care facility for you?` Yes/No
- `Signature` · `Date`

### Tell Us About Everyone Who Lives in the Home Starting With the Adult Head of Household (Applicant)
Repeated blocks `Person One (Applicant)` through `Person Six`. Per-person labels:
- `Self, name of applicant` (Person One) / `Name` (Persons Two–Six)
- `Date of birth` · `Marital status` · `Gender M F`
- `U.S. Citizen?` Yes/No · `Social Security number` · `Alien registration number` · `Hispanic or Latino?` Yes/No
- `Relationship to head of household` · `Relationship to spouse of head of household` (Persons Two–Six only)
- `Race - check all that apply:` `American Indian or Alaska Native; when checked, tribe:` `Asian` `Black or African American` `Native Hawaiian or other Pacific Islander` `White`
- `Name on birth certificate` · `State of birth` · `County of birth`
- `Mother's maiden name as listed on this person's birth certificate:` `First name` `M.I.` `Last name`

Instruction verbatim: "If there are more than six persons in your household, attach another sheet
of paper showing their information."

### If You Need Child Care
- `Are you in danger of losing a job due to a lack of child care?` Yes/No
- `Have you made payment arrangements with the child care provider until a decision can be made on your child care application?` Yes/No
- `Are you starting a new job?` Yes/No `If yes, starting date`
- Per `Parent/caretaker 1` and `Parent/caretaker 2`: `Parent/caretaker name:`; `Reason:` `Work` `School` `Training` `Protective/preventive` `TANF Work` `Other:`; `Days and hours:` Monday–Sunday `from` `to`

### If You Need Food Benefits
- `Have you or any member of your household been convicted of fraudulently receiving duplicate SNAP benefits in any State after September 22, 1996?` Yes/No
- `Have you or any member of your household been convicted of buying or selling SNAP benefits over $500 after September 22, 1996?` Yes/No
- `Have you or any member of your household been convicted of trading SNAP benefits for guns, ammunitions, or explosives after September 22, 1996?` Yes/No

### Application Processing Time Limits (verbatim)
- `TANF - 30-calendar days;`
- `SNAP - 30-calendar days unless you are eligible for expedited services. Expedited services is 7-calendar days;`
- `Child Care Subsidy - 2-business days from the date the interview is completed and required proof is provided;`
- `SSP - 30-calendar days for Aid to the Aged and 60-calendar days for Aid to the Blind or Disabled; and`
- `SoonerCare (Medicaid) for the aged, blind, or disabled - 30-calendar days for Aid to the Aged and 60-calendar days for Aid to the Blind or Disabled.`

### OKDHS use only
`Is the household eligible for expedited food benefits?` Yes/No · `Date received:` · `Date screened:` ·
`Screened by:` · `Interview date:` · `Interviewed by:`

---

# 4. Form 08MP002E "Eligibility Information for Benefits" — verbatim field labels

Source: https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/okdhsformresults/08mp002e.pdf
This is where every income, expense, deduction, and resource field lives.

### Header block
`Date` · `Case name` · `Case #` · `County #` · `Application Date` · `Supervisor #` · `Worker #`

### Tell Us About You and Everyone Else in the Home
Repeated per person (six blocks):
- `Last name` · `First name` · `Middle name`
- `Social Security number` · `If Native American, what tribe?`
- `Ever received Tribal TANF?` Yes/No · `Blind or disabled?` Yes/No
- `Attending School?` Yes/No
- `Last grade completed` · `Full or part time` · `Where attending school?`
- `If child, are immunizations current?` Yes/No · `If no, why?`
- `Military status, check one:` `Active duty military` `Former military` `National Guard/Military Reserve` `None`

**Additional questions**
- `Have you or anyone in your home lived in any other states in the last 12 months?` Yes/No · `If yes, what states?`
- `Did anyone receive benefits while there?` Yes/No · `If yes, who?` · `What states?`
- `Type of benefits:` `Cash` `Medical` `Food` `Child Care` `Tribal food distribution (commodities)` `Other`
- `Date of last benefit:` · `Still receiving?` Yes/No
- `Do you plan to stay in Oklahoma?` Yes/No
- `Are you or is anyone living with you a fleeing felon or a probation/parole violator?` Yes/No

### Tell Us About Your Household's Income
Definition verbatim: "Income is all the money you and the people living with you get." /
"Types of earned income include money you get from working for someone else or working for yourself."

Unearned income types listed verbatim on the form:
`adoption subsidy payments`, `alimony`, `child support`, `contributions`, `dividends`,
`foster care`, `gambling and lottery winnings`, `housing allotment`, `interest`,
`military allotments`, `mineral rights income`, `oil and gas lease income`, `pension`,
`personal loans`, `rental income`, `Social Security`, `Supplemental Security Income (SSI)`,
`State Supplemental Payment (SSP)`, `student income`,
`Temporary Assistance for Needy Families (TANF) or tribal TANF`, `tribal income`,
`unemployment benefits`, `utility allowance`, `Veterans Affairs (VA) benefits`,
`Workers' Compensation`

- `Do you or anyone living with you have any income?` Yes/No
- Repeated income block (three printed): `Name of person getting income` · `Type of income` ·
  `How often received?` · `Amount before taxes` · `Are tips received?` Yes/No · `If yes, how much?` ·
  `Employer` · `Area code` · `Employer phone number` · `Employer address` ·
  `Self-employment gross income last year` · `Self-employment business expenses`

**Terminated income** — "When any earned or unearned income stopped in the last 60 calendar days,
fill out the information below."
- `Name of person with terminated income` · `Source, such as employer name, SSI, or child support` ·
  `Final amount` · `Date received`

### Tell Us About Your Bills and Expenses
**Child care expense**
- `How much do you pay each month for child care?`

**Adult day care expense**
- `How much do you pay each month for day care for an elderly or disabled person who lives with you?`

**Medical expense** — "Tell us the medical costs not paid by insurance for everyone who is disabled
or 60 years of age and older. These costs could be doctor or hospital bills, medicine,
transportation, health insurance premiums, or other medical services."
- `Name` · `Type of Expense` · `Monthly Expense`

**Child support expense**
- `Does anyone in your household PAY court ordered child support?` Yes/No
- `Who pays support?` · `How much?` · `How often?`
- `Who gets support?` · `Phone number of person receiving support`

**Housing expense**
- `Check the box that shows how you pay for housing:` `Rent` `Own or buying` `Does not pay for housing` `Other`
- `Rent or mortgage amount` · `Taxes, when paid separately from mortgage` · `Insurance, when paid separately from mortgage`
- `Whom do you pay for your housing? (name, address, and phone number)`
- Housing help: `Who helps you?` · `Who do they pay?` · `How much?`
- `If you consider yourself homeless, do you have any shelter costs associated with being homeless such as living in a car and having a car payment, giving a friend money to sleep in their home, paying camping fees, or hotel/motel charges?` Yes/No · `If so, how much do you spend for these expenses?`

**Utility expense**
- `Check the box for each expense you have:` `Phone` `Electric` `Garbage/water` `Wood` `Gas/butane/propane`
- `Total amount:`
- Utility help: `Who helps you?` · `Who do they pay?` · `How much?`
- `Enter utility account information if your heating or cooling cost is not included in your rent:`
  - `Natural gas` and `Electric`, each with: `Company name` · `Account number` ·
    `Account name, as shown on your bill` · `When the account is not in your name, explain why` ·
    `Address where the gas or electric meter is located` · `City` · `State` · `ZIP code`

**Other expenses**
- `Check the box for each expense you have:` `Cable` `Car/truck payment/transportation`
  `Credit card payment(s)` `Insurance premium(s)` `Other expenses`
  `Non-food items, such as toiletries or laundry soap`
- `Total amount:`

**Income and expenses comparison**
- `Average monthly income:` · `Average monthly expenses:`
- `When income is less than expenses, explain below how you are paying your bills:`

### Tell Us About Your Resources
Definition verbatim: "A resource is anything anyone owns, owns jointly with someone else, or is
buying that can be sold, traded, or changed into cash. Do not report personal property, such as
jewelry, furniture, household appliances, or clothing."
- Checkboxes: `Checking accounts` `Savings accounts` `Stocks/bonds` `Prepaid burial policies`
  `Life insurance` `Trust funds` `Individual retirement account (IRA)` `Mineral rights` `Livestock`
  `Property other than your home` `Certificate of deposit (CD)` `Land`
  `Cash/OKDHS issued debit card account balance` `Other:`
- Vehicles: `Make` · `Model` · `Year` · `Loan Balance`
- `Is there anyone in your household whose name is listed on any other person's checking or savings account, car title, property deed, or any other resource?` Yes/No · `If yes, explain below:`
- `Has anyone sold, traded, deeded, or given away any resources within the last 60 months?` Yes/No ·
  `What was sold, traded, or given away?` · `When?` · `How much did you get?`

### Tell Us About Your Need for the Following Programs — SNAP portion
- `Who do you want to choose as head of household?`
- `Does everyone in your home buy and prepare food together?` Yes/No
- `Is any household member on strike?` Yes/No · `If yes, who?`

Head-of-household guidance verbatim: choose "adult parent of a child(ren) under 18 years of age;
an adult with parental control or responsibility for the care of a child(ren) under 18 years of age;
a person who is employed for a minimum of 30 hours per week; or a person who is receiving or has
applied for unemployment benefits."

### Tell Us About Your Medical Insurance
- `Is anyone covered by medical insurance? TRICARE, Champus, and VA Aid and Attendance are considered insurance.` Yes/No
- `Has anyone been in an accident in the last 12 months?` Yes/No · `When yes, has legal action been taken or planned?` Yes/No

(The TANF-deprivation, SoonerCare-pregnancy/EPSDT, child-care-provider, and "Other Needed Services"
sections of 08MP002E are not SNAP-relevant and are omitted here.)

---

# 5. Form 08MP003E "Rights, Responsibilities, and Signature for Benefits" — declarations

Source: https://oklahoma.gov/content/dam/ok/en/okdhs/documents/searchcenter/okdhsformresults/08mp003e.pdf

Section headings (the form's own): `General Rights for All Programs` ·
`General Responsibilities for All Programs` · `Child Support Responsibilities` ·
`Read these Statements if You are Applying for Food Benefits` ·
`Read these Statements if You are Applying for Temporary Assistance for Needy Families (TANF)` ·
`Non-Discrimination Statement` · `OKDHS Routing Information`

SNAP-relevant declarations, verbatim:
- `I am certifying under penalty of perjury that every person in my household for whom I am applying for benefits is a United States citizen or an alien in lawful immigration status.`
- `I will be responsible to repay any established overpayment;`
- `I am registering myself and/or any other household members between 16 and 59 years of age for work unless I or other household members meet exemptions criteria. Check No if you do not agree with this statement:` (checkbox `No`)
- `I understand if I check No, required work registrants in my household will not be included in food benefits.`
- `I may be eligible for certain deductions that can increase my SNAP benefits. These include medical expenses for elderly or disabled household members, legally-binding child support, shelter and utility costs, and dependent care expenses. To have these deductions applied, I must report the expense and verify it (if requested). If I fail to report or verify an expense, OKDHS will not include it in my benefit calculation.`
- `food benefits are prorated from the date of application; and`
- `providing requested information, including the SSN of each household member, is voluntary; however, failure to provide this information will result in the denial of food benefits to my household.`

Stated SNAP penalties (verbatim): "one year for the first offense; two years for the second
offense; and permanently for the third offense." Trading for controlled substances: "two years for
the first offense; and permanently for the second offense." Trading for firearms/ammunition/
explosives or trafficking $500 or more: "permanent loss of food benefits for the first offense."

---

## 6. Documents OKDHS states a SNAP applicant needs

Source: https://oklahoma.gov/okdhs/services/snap.html — quoted fragments:
`pay stubs for all checks anyone received in the last 30 days; or statements from employers showing
pay dates and earnings`; `current utility bills`. The page groups requirements under employment,
income, and expenses (housing, utilities, medical for persons over 60 or disabled, court-ordered
child support, child care).

The form's own list is under "What You Will Need to Bring to Your Interview" in section 3 above,
which is the more authoritative version because it is printed on the form.

## 7. Submission

Form 08MP001E page 9, verbatim: "Please give this form to the receptionist or fax or mail it to an
OKDHS office. If you do not know where an OKDHS office is, please visit www.okdhs.org."

Office locator: https://oklahoma.gov/okdhs/contact-us.html

## 8. Not confirmed

- No static OKDHS forms index with revision dates was reachable (the forms search page is
  JavaScript-only). Revision dates here come from each PDF's printed footer instead.
- Whether okdhslive.org has a successor portal: not established. It was down at fetch time and
  OHCA still links it, which points to a temporary outage.
