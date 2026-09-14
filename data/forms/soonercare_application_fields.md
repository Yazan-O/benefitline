# SoonerCare (Oklahoma Medicaid) application routes and the information OHCA states an applicant needs
Sources fetched 2026-09-13

## Bottom line first

- **mysoonercare.org is still live but is no longer the application portal itself.** It 200s and
  redirects to `https://oklahoma.gov/ohca/individuals/mysoonercare.html`, an OHCA content page.
- **The actual online application portal is `https://www.apply.okhca.org/`** (200 at fetch time,
  landing on `Default.aspx`; the sign-in page is `https://www.apply.okhca.org/Site/UserAccountLogin.aspx`
  and the entry link on the OHCA page is `https://www.apply.okhca.org/Site/Rights.aspx`).
- **There is no OHCA-numbered paper SoonerCare application.** The "form" OHCA offers for mail-in is
  the federal CMS Marketplace application, not a state form. Details in section 3.
- Aged, blind, disabled, nursing home, TEFRA and waiver applicants do **not** apply through OHCA;
  they apply through Oklahoma Human Services (OHS/OKDHS). Section 4.

---

## 1. Routes and URLs

| Route | Who it is for | URL |
|---|---|---|
| Online portal (OHCA) | Children, pregnant women, low-income non-disabled adults with children, family planning, behavioral health, adults 19-64 not Medicare-eligible, Insure Oklahoma employees, out-of-state foster-care alumni | https://www.apply.okhca.org/ |
| MySoonerCare landing page | Info hub; mysoonercare.org redirects here | https://oklahoma.gov/ohca/individuals/mysoonercare.html |
| Where to Apply (route chooser) | All | https://oklahoma.gov/ohca/individuals/mysoonercare/apply-for-soonercare-online/where-to-apply.html |
| Prepare for Application | All | https://oklahoma.gov/ohca/individuals/mysoonercare/apply-for-soonercare-online/prepare-for-application.html |
| Apply for Benefits (portal entry page) | All | https://oklahoma.gov/ohca/individuals/mysoonercare/apply-for-soonercare-online/apply-for-soonercare.html |
| OHS route | Age 65+, blind, disabled, TEFRA, nursing home, Medicare cost-sharing, HCBS waiver, OHS custody, TB | https://okdhslive.org (linked from Where to Apply) |
| Long-term care | Nursing home / LTC expenses | https://oklahoma.gov/ohca/individuals/programs/long-term-services-and-supports/applying-for-help-with-long-term-care-expenses.html |
| SoonerSelect | Managed care plan selection | https://oklahoma.gov/ohca/soonerselect/apply.html |
| Insure Oklahoma | Employed / self-employed premium assistance | http://www.insureoklahoma.org/ |
| SoonerCare Supplemental | Supplemental coverage | https://oklahoma.gov/ohca/individuals/soonercare-supplemental.html |

SoonerCare Helpline stated on OHCA pages: **1-800-987-7767** (also written "(800) 987-7767").

Note on okdhslive.org: at fetch time on 2026-09-13 it redirected to
`https://www.okdhslive.org/TempDown.aspx`, which reads "The OKDHSLive! website is currently
unavailable." with the page's own stamp `9/13/2026 ... 7:51 PM`. OHCA still links it as the OHS
route, so this reads as a temporary outage, not a replaced route.

## 2. Information OHCA states an applicant needs

Source: https://oklahoma.gov/ohca/individuals/mysoonercare/apply-for-soonercare-online/prepare-for-application.html
Page heading verbatim: **"Important Information Before Starting"**, then
**"Before you start, you need to have the following information available:"**

Quoted verbatim from the page source:

1. `You and your spouse's taxable income.`
2. `Social Security numbers and birthdates of people in your home.`
3. `Current or recent health insurance information.`
4. `Identity and citizenship information, or alien registration information.`
5. `Income information including employer name, address and phone number, of all household members who are employed.`
6. `Amount of money received from other types of income.`
7. `Expected date of delivery and number of babies of any pregnant household member.`
8. `Current health insurance information for all household members with health insurance including company name, policy or group number, type of coverage, effective date, policy holder's name and ID.`

Also stated verbatim on the same page:
- `Allow yourself plenty of time to complete the application since you will be entering detailed personal information.`
- `In order to save an unfinished application and return to it, you must create a user account. You will be able to do this once you start your application. You may also use your account to check the status of your application, report any changes, upload documents, or resubmit your application.`
- `If you have questions about the types of documentation you may need to show proof of your eligibility, see the document verification guide.`
- `If you need more help, please call (800) 987-7767.`

**Document Verification Guide** (acceptable proof per request type):
https://oklahoma.gov/content/dam/ok/en/okhca/docs/individuals/mysoonercare-portal/Document%20Verification%20Guide.pdf

Separate from the applicant-requirements list above, the MySoonerCare page also links OHCA
**verification forms** (Income Verification Form, No Income Attestation Form, Self-Employment/Cash
Income Statement, 12 Month Profit and Loss Worksheet, Lottery/Gambling Winnings Monthly Income).
These are proof documents submitted after or alongside an application, not items on the "before you
start" list. Source: https://oklahoma.gov/ohca/individuals/mysoonercare.html

## 3. Paper application: no state form number

OHCA's Where to Apply page, verbatim: "You can use a form to apply for SoonerCare health insurance
for yourself and everyone in your immediate family who lives with you. You may download the English
or Spanish application, fill it out and mail it in. If you need help filling out the form, call the
SoonerCare Helpline at 1-800-987-7767."

- English: https://oklahoma.gov/content/dam/ok/en/okhca/documents/marketplace-application-for-family.pdf
- Spanish: https://oklahoma.gov/content/dam/ok/en/okhca/documents/marketplace-consumer-application-family-spanish.pdf

The English PDF was downloaded and read. Its printed identity is **not** an OHCA form number — it is
the federal CMS form: `Form Approved / OMB No. 0938-1191`, title `Application for Health Coverage &
Help Paying Costs`, with `Expires: 09/30/2022` printed on page 1 and `Apply faster online at
HealthCare.gov`. 15 pages. Its help line is the federal `1-800-318-2596`, not OHCA's.

So: **no SoonerCare-specific state paper form number exists.** The mail-in option OHCA points to is
an expired-stamped federal Marketplace application. Flag this for the product: pointing a user at
that PDF is a worse path than the online portal.

## 4. Aged / blind / disabled and other separate routes

Source: https://oklahoma.gov/ohca/individuals/mysoonercare/apply-for-soonercare-online/where-to-apply.html

Verbatim categories that apply through OHS instead of OHCA:
`Age 65 or over` · `Blind (any age)` · `Disabled adults` ·
`Disabled children who do not qualify for Social Security Income because of their parents income
and/or resources (TEFRA)` · `Individuals who reside in nursing homes, yet qualify for SoonerCare` ·
`Individuals with Medicare coverage that need assistance to pay premiums, coinsurance and/or
deductibles` · `Home and community-based waiver populations.` · `Children in the custody of OHS` ·
`Individuals who receive treatment for Tuberculosis (TB).`

Verbatim instruction: "You may apply at your local county OHS office or download the PS-1 - Request
for Services application from the OHS website. The OHS workers will assist you in gathering the
needed information such as your income, assets, family size and, if available, recent medical
information."

**Caveat on PS-1.** That form number is quoted from OHCA describing an OKDHS form. No PS-1 form was
found on any okdhs / oklahoma.gov okdhs domain. The current OKDHS intake form is 08MP001E "Request
for Benefits" (rev. 4/14/2026), which explicitly lists `Health Care Coverage - SoonerCare (Medicaid)`
among its checkable programs. Treat "PS-1" as a stale reference on the OHCA page and use 08MP001E.
See `ok_snap_application_fields.md` in this folder.

Breast/cervical cancer: verbatim, "Once you have been screened and found to be in need of further
diagnosis and/or treatment for breast or cervical cancer, you or your screener can call the Oklahoma
State Department of Health to find out how to apply for Oklahoma Cares."

## 5. Online application step structure

The OHCA "Apply for Benefits" page lists the portal's own steps as how-to videos, verbatim:
`Step 1 - People and Contacts` · `Step 2 - Tax Household` · `Step 3 - Household Income` ·
`Step 4 - Expenses` · `Step 5 - Health Insurance` · `Step 6 - Review` ·
`Step 7 - Citizenship and Identity` · `Step 8 - Provider Selection`
Source: https://oklahoma.gov/ohca/individuals/mysoonercare/apply-for-soonercare-online/apply-for-soonercare.html

This is the closest thing to a field-group listing OHCA publishes. The portal itself is behind a
login, so its individual field labels were not extracted.

## 6. Not confirmed

- Individual field labels inside apply.okhca.org: not extracted. The application is behind account
  creation; only the eight-step structure above is public.
- Whether OHCA intends the expired federal Marketplace PDF as a current mail-in route, or whether
  the link is stale: the page text is current, the PDF's own expiry stamp is 09/30/2022. Both facts
  recorded; the conflict is unresolved.
- PS-1: no okdhs-side source found. See section 4 caveat.
