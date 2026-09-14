"""Tier routing and the tier-0 reader. Zero model calls happen in this file.

Tier 0: the pending question expects one field and a regex can read it.
Tier 1: six words or fewer that tier 0 could not read (Haiku).
Tier 2: everything else (Sonnet).

`tier0_parse` returns one `Fact` whose `value` is a small encoded token; `intake.py`
is the only place that decides where that token lands in `HouseholdFacts`.
"""
from __future__ import annotations

import re
from typing import Optional

from .schemas import Fact

# Arabic block U+0600-U+06FF plus Arabic Supplement / Presentation Forms.
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

OK_CITIES = [
    "tulsa", "oklahoma city", "okc", "norman", "broken arrow", "edmond", "lawton",
    "moore", "midwest city", "stillwater", "muskogee", "enid", "bartlesville",
    "owasso", "shawnee", "ponca city", "duncan", "ardmore", "yukon", "del city",
    "تولسا", "أوكلاهوما", "اوكلاهوما", "نورمان", "لوتن",
]

_YES = {"yes", "yeah", "yep", "yup", "y", "sure", "correct", "right", "true", "ok",
        "okay", "affirmative", "we do", "i do", "si"}
_NO = {"no", "nope", "nah", "n", "none", "nothing", "never", "false", "not really",
       "no one", "nobody"}
_AR_YES = ["نعم", "ايوه", "أيوه", "إيوه", "اي", "أي", "إي", "ايه", "أيه", "إيه",
           "أجل", "اجل", "صح", "تمام", "اكيد", "أكيد", "بلى"]
_AR_NO = ["لا", "كلا", "لأ", "ما في", "مافي", "ولا واحد", "ما حد", "مو"]

_SPOUSE_WORDS = ["wife", "husband", "spouse", "partner", "زوجتي", "زوجي", "زوجة", "الزوجة", "الزوج"]
_SS_WORDS = ["social security", "ssa", "ss check", "retirement", "pension", "disability check",
             "ضمان اجتماعي", "الضمان", "التقاعد", "تقاعد", "معاش"]
_NO_PAPERS = ["no papers", "without papers", "dont have papers", "don't have papers",
              "do not have papers", "no documents", "undocumented", "illegal", "not legal",
              "no green card", "بدون أوراق", "بدون اوراق", "ما عندنا أوراق", "ما عندنا اوراق",
              "ما عندي أوراق", "لا نملك أوراق", "مافي أوراق", "بلا أوراق", "غير شرعي"]
_BORN_HERE = ["born here", "born in the us", "born in america", "born in the states",
              "born and raised", "us citizens", "american citizens", "مولودين هنا",
              "ولدوا هنا", "مواليد هنا", "مولودين في أمريكا", "أمريكيين"]
# Said of the whole home with no "but ... no papers" clause, this answers the citizenship
# question outright, so tier 0 reads it instead of paying a model to re-read it.
_ALL_CITIZEN = _BORN_HERE + ["all citizens", "all us citizens", "everyone is a citizen",
                             "we are citizens", "i am a citizen", "im a citizen",
                             "كلنا مواطنين", "كلنا مواطنون", "أنا مواطن", "انا مواطن"]
_PREGNANT = ["pregnant", "expecting", "with child", "حامل", "حبلى"]
_BREASTFEEDING = ["breastfeed", "breast feed", "breast-feed", "nursing", "nurses",
                  "رضاعة", "ترضع", "أرضع", "ارضع", "مرضع", "برضع"]

_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY",
}

_MONTHS_OLD = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:months?|month|mos?\.?|شهر|أشهر|اشهر|شهور)\b", re.I)
_MONTHS_NO_NUM = re.compile(
    r"(شهرين|بضعة أشهر|بضعة اشهر|كام شهر|months old|a few months)", re.I)
_NEWBORN = ["newborn", "new born", "baby", "infant", "just born", "رضيع", "مولود", "حديث الولادة"]

_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand|ألف|الف)?", re.I)

_PER_HOUR = ["an hour", "a hour", "per hour", "/hr", "/hour", "hourly", "hr",
             "بالساعة", "في الساعة", "للساعة", "الساعة"]
_PER_WEEK = ["a week", "per week", "weekly", "/wk", "/week", "بالأسبوع", "في الأسبوع", "أسبوعيا", "اسبوعيا"]
_PER_TWO_WEEKS = ["every two weeks", "biweekly", "every 2 weeks", "كل أسبوعين", "كل اسبوعين"]
_PER_YEAR = ["a year", "per year", "the year", "a yr", "per yr", "each year", "every year",
             "yearly", "annually", "annual", "/yr", "/year",
             "بالسنة", "في السنة", "سنويا", "سنوياً", "كل سنة"]
_HOURS_WEEK = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|ساعة|ساعات)\s*(?:a|per|each|/|في|بال)?\s*"
    r"(?:week|wk|أسبوع|الأسبوع|اسبوع)", re.I)
# An hour count with no period word ("$14 an hour and I get about 30 hours"). The digit
# must touch the unit, so "14 an hour" is a rate and never reads back as 14 hours.
_HOURS_BARE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b|"
                         r"(\d+(?:\.\d+)?)\s*(?:ساعة|ساعات)", re.I)

# Tier 0 reads a short reply to one question. A longer message carries context a regex
# cannot weigh, so DESIGN's "tier 2: everything else" takes it. 13 is the longest reply
# in gallery/scripts.json ("I'm 31, my wife is 28, and our baby is 2 months old").
TIER0_MAX_WORDS = 13

# An age or pregnancy answer about someone who may not live in the home is misread by a
# regex, which has no way to attach the fact to the right person. Spouse words are absent
# on purpose: `_who` already routes those to the spouse.
_THIRD_PARTY = ["sister", "brother", "mother", "father", "cousin", "friend", "roommate",
                "aunt", "uncle", "niece", "nephew", "neighbor", "neighbour", "in-law",
                "أختي", "اختي", "أخي", "اخي", "أمي", "امي", "صديقتي", "صديقي",
                "جارتي", "جاري", "قريبتي", "قريبي"]

# Words a digit-and-word mix uses for ages. "one" is left out: "no one" is a common no.
_NUMBER_WORDS = ["two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
                 "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
                 "اثنين", "اثنان", "ثلاثة", "أربعة", "اربعة", "خمسة", "ستة", "سبعة",
                 "ثمانية", "تسعة", "عشرة"]

# A legal-process or abuse question is never answered by this agent. Status wording ("no
# papers", "green card", "permanent resident") is deliberately absent: disclosing a status
# is an answer to the citizenship question, not a legal question.
_SAFETY_PHRASES = ["deport", "immigration court", "immigration judge", "lawyer", "attorney",
                   "asylum", "removal proceedings", "restraining order", "hits me",
                   "beats me", "hurts me", "domestic violence",
                   "ترحيل", "يرحلوني", "يرحلونا", "محكمة الهجرة", "محامي", "محامية",
                   "لجوء", "يضربني", "العنف الأسري", "عنف أسري"]
_SAFETY_TOKENS = {"ice", "abuse", "abused", "abusive", "uscis"}


_SSN = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
_ANUM = re.compile(r"\bA[-\s]?\d{8,9}\b", re.I)
REDACTED = "[id removed]"


def redact(text: str) -> str:
    """Mask an SSN or A-number before anything reads it. `hooks.py` guards the way out of
    the agent; this guards the way in, so the digits never become a number and never
    reach a `Fact.quote` that is stored on the case and sent to the browser."""
    if not text:
        return text
    return _ANUM.sub(REDACTED, _SSN.sub(REDACTED, text))


def _ascii_digits(text: str) -> str:
    return text.translate(_ARABIC_INDIC)


def _norm(text: str) -> str:
    return _ascii_digits(text or "").strip().lower()


def detect_language(text: str) -> str:
    """Script detection only, no model: any Arabic letter means the family writes Arabic.

    Spanish and every other Latin-script language stay "en" here; the cheap model
    labels them later from the message itself.
    """
    return "ar" if _ARABIC.search(text or "") else "en"


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _tokens(text: str) -> list[str]:
    """Words only. Arabic yes/no words match as whole tokens: "لا" is a substring of
    ordinary words such as "الأولاد", so a substring test would read a "no" that is not there."""
    # Arabic punctuation (،  ؛  ؟  ـ) sits inside the Arabic code block, so `\w` keeps it;
    # it is stripped first or the first token comes back as "نعم،".
    stripped = re.sub(r"[،؛؟ـ٫٬…]", " ", _norm(text))
    return re.sub(r"[^\w\s؀-ۿ]", " ", stripped).split()


def _yes_no(text: str) -> Optional[bool]:
    t = _norm(text)
    tokens = _tokens(text)
    if not tokens:
        return None
    head, pair = tokens[0], " ".join(tokens[:2])
    if head in _AR_YES or pair in _AR_YES:
        return True
    if head in _AR_NO or pair in _AR_NO:
        return False
    if any(tok in _AR_YES for tok in tokens) and not any(tok in _AR_NO for tok in tokens):
        return True
    if any(tok in _AR_NO for tok in tokens):
        return False
    if head in _YES or " ".join(tokens) in _YES:
        return True
    if head in _NO or " ".join(tokens) in _NO:
        return False
    if any(p in t for p in ["not really", "no one", "nobody", "we don't", "we do not", "i don't"]):
        return False
    return None


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for raw, mult in _NUM.findall(_ascii_digits(text)):
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        if mult:
            n *= 1000
        out.append(n)
    return out


def _to_monthly(amount: float, text: str) -> float:
    t = _norm(text)
    if _has(t, _PER_YEAR):
        return amount / 12.0
    if _has(t, _PER_TWO_WEEKS):
        return amount * 26.0 / 12.0
    if _has(t, _PER_WEEK):
        return amount * 52.0 / 12.0
    return amount


def _weekly_hours(t: str, skip: tuple[int, int] | None = None) -> Optional[float]:
    """Hours a week the family actually stated, or None. `skip` blanks the hourly-rate
    span so the rate in "$14 an hour" cannot be read back as a count of hours."""
    hrs = _HOURS_WEEK.search(t)
    if hrs:
        return float(hrs.group(1))
    if skip is not None:
        t = t[:skip[0]] + " " * (skip[1] - skip[0]) + t[skip[1]:]
    bare = _HOURS_BARE.search(t)
    if bare:
        return float(bare.group(1) or bare.group(2))
    return None


def _money_monthly(text: str) -> Optional[float]:
    """One money amount from a message, converted to USD per month."""
    t = _norm(text)
    if _yes_no(text) is False and not _numbers(text):
        return 0.0
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:dollars?\s*)?(?:an hour|a hour|per hour|/\s*hr|"
                           r"/\s*hour|hourly|بالساعة|في الساعة|للساعة)", _ascii_digits(t))
    if hour_match:
        rate = float(hour_match.group(1))
        hours = _weekly_hours(_ascii_digits(t), skip=hour_match.span())
        if hours is None:
            return None  # never assume a 40-hour week; intake asks for the hours instead
        return rate * hours * 52.0 / 12.0
    nums = _numbers(text)
    if not nums:
        return None
    # Ignore bare hour counts ("30 hours a week") when no rate was found.
    return _to_monthly(nums[0], text)


def _who(text: str) -> str:
    return "spouse" if _has(_norm(text), _SPOUSE_WORDS) else "self"


def _parse_ages(text: str) -> list[int]:
    t = _ascii_digits(text or "")
    ages: list[tuple[int, int]] = []           # (position, age)
    for m in _MONTHS_OLD.finditer(t):
        ages.append((m.start(), 0))            # "2 months old" is age 0 to the engine
    blanked = _MONTHS_OLD.sub(lambda m: " " * len(m.group(0)), t)
    for m in _MONTHS_NO_NUM.finditer(blanked):
        ages.append((m.start(), 0))            # "شهرين" / "months old" with no digit is also age 0
    blanked = _MONTHS_NO_NUM.sub(lambda m: " " * len(m.group(0)), blanked)
    for m in re.finditer(r"(?<![\w.])(\d{1,3})(?!\w)(?!\.\d)", blanked):
        n = int(m.group(1))
        if 0 <= n <= 120:
            ages.append((m.start(), n))
    if not ages and _has(_norm(t), _NEWBORN):
        ages.append((0, 0))
    return [a for _, a in sorted(ages)]


def _parse_state(text: str) -> Optional[str]:
    t = _norm(text)
    if re.search(r"\boklahoma\b", t) or re.search(r"\bok\b", t) or "أوكلاهوما" in t or "اوكلاهوما" in t:
        return "OK"
    if _has(t, OK_CITIES):
        return "OK"
    m = re.search(r"\b([A-Z]{2})\b", _ascii_digits(text or ""))
    return m.group(1) if m and m.group(1) in _STATE_CODES else None


def _has_unparsed_number_word(text: str) -> bool:
    """True when a number is written as a word next to the digits tier 0 did read.
    "kids are seven and four, and I'm 29" yields one age out of three, and a partial
    ages list is worse than none: it silently resizes the household."""
    blanked = _NUM.sub(lambda m: " " * len(m.group(0)), _ascii_digits(text or ""))
    return any(w in _tokens(blanked) for w in _NUMBER_WORDS)


def safety_flag(message: str) -> str:
    """A short reason when the family raised a legal-process or abuse question, else "".
    Runs at every tier, including tier 0, so a legal question riding on a parseable
    answer is escalated instead of swallowed."""
    t = _norm(message)
    for phrase in _SAFETY_PHRASES:
        if phrase in t:
            return f"family raised a legal or safety question ({phrase})"
    for tok in _tokens(message):
        if tok in _SAFETY_TOKENS:
            return f"family raised a legal or safety question ({tok})"
    return ""


# Only wording that says outright "the value I gave you before was wrong". A bare "sorry"
# or "actually" is left out: it reads as hedging far more often than as a correction, and a
# false positive moves a number into a field the family never spoke about.
_CORRECTION = ["i meant", "i ment", "meant to say", "correction", "my mistake",
               "scratch that", "make that", "i said the wrong", "that was wrong",
               "قصدي", "أقصد", "اقصد", "قصدت", "غلطت", "بالغلط", "تصحيح"]

# Which field a correction names, when it names one at all.
_FIELD_WORDS = {
    "rent": ["rent", "الإيجار", "الايجار", "إيجار", "ايجار"],
    "utilities": ["utilit", "electric", "lights", "gas", "power bill", "الكهرباء", "الغاز",
                  "الفواتير"],
    "income": ["income", "wage", "salary", "pay", "earn", "الراتب", "الدخل", "أكسب"],
    "medical_expenses": ["medic", "pills", "prescription", "الأدوية", "الادوية", "الدواء"],
    "health_premiums": ["premium", "insurance", "التأمين", "قسط"],
}


def is_correction(message: str) -> bool:
    """The family is fixing a value it already gave, not answering the pending question."""
    return _has(_norm(message), _CORRECTION)


def named_field(message: str) -> str:
    """The fact key a message names outright, or "" when it names none."""
    t = _norm(message)
    hits = [key for key, words in _FIELD_WORDS.items() if _has(t, words)]
    return hits[0] if len(hits) == 1 else ""


def tier0_parse(pending: str, message: str, msg_id: str) -> Optional[Fact]:
    """Read one field from a reply, or return None so a model reads it instead.

    `pending` is the fact key the last agent question asked for. The returned
    `Fact.value` is a token `intake.py` decodes; the quote is the family's own words.
    """
    if not pending or not message or not message.strip():
        return None
    text = redact(message.strip())
    q = text if len(text) <= 200 else text[:197] + "..."

    def fact(value):
        return Fact(value=value, source_msg_id=msg_id, quote=q)

    if pending == "state":
        s = _parse_state(text)
        return fact(s) if s else None

    if pending == "household_size":
        nums = _numbers(text)
        if nums and 1 <= nums[0] <= 20 and float(nums[0]).is_integer():
            return fact(int(nums[0]))
        return None

    if pending == "ages":
        if _has_unparsed_number_word(text) or _has(_norm(text), _THIRD_PARTY):
            return None
        ages = _parse_ages(text)
        return fact(",".join(str(a) for a in ages)) if ages else None

    if pending == "weekly_hours":
        hrs = _HOURS_WEEK.search(_ascii_digits(text))
        if hrs:
            return fact(float(hrs.group(1)))
        nums = _numbers(text)
        if nums and 0 <= nums[0] <= 168:
            return fact(float(nums[0]))
        return fact(0.0) if _yes_no(text) is False else None

    if pending == "income":
        amount = _money_monthly(text)
        if amount is None:
            return None
        kind = "social_security" if _has(_norm(text), _SS_WORDS) else "employment"
        return fact(f"{kind}:{round(amount, 4)}:{_who(text)}")

    if pending in ("rent", "utilities", "medical_expenses", "health_premiums"):
        amount = _money_monthly(text)
        return fact(round(amount, 2)) if amount is not None else None

    if pending == "pregnancy":
        t = _norm(text)
        if _has(t, _THIRD_PARTY):
            return None  # a regex cannot tell whose pregnancy this is
        # "No, nobody is pregnant or breastfeeding" repeats the question's words; the No wins
        # unless the reply turns ("nobody's pregnant, but I am still breastfeeding").
        if _yes_no(text) is False and not re.search(r"\b(but|except|although|yes)\b", t):
            return fact("none")
        if _has(t, _BREASTFEEDING):
            return fact(f"breastfeeding:{_who(text)}")
        if _has(t, _PREGNANT):
            return fact(f"pregnant:{_who(text)}")
        if _yes_no(text) is False:
            return fact("none")
        return None  # a bare "yes" does not say who or which; let a model ask

    if pending == "immigration":
        t = _norm(text)
        if _has(t, _NO_PAPERS):
            return fact("not_all_kids_citizen" if _has(t, _BORN_HERE) else "not_all")
        if _has(t, _ALL_CITIZEN):
            return fact("all_citizen")
        yn = _yes_no(text)
        if yn is True:
            return fact("all_citizen")
        if yn is False:
            return fact("not_all_kids_citizen" if _has(t, _BORN_HERE) else "not_all")
        return None

    return None


def pending_key(case) -> str:
    """The fact key the agent's last message asked for, or "" when nothing is pending."""
    for entry in reversed(case.transcript):
        if entry.get("role") == "agent":
            return str(entry.get("asks") or "")
        if entry.get("role") in ("family", "user"):
            return ""
    return ""


def route(case, message: str) -> int:
    """0 when a short reply fully answers the pending question, 1 for six words or fewer
    that tier 0 could not read, 2 otherwise (DESIGN: "tier 2: everything else")."""
    pending = pending_key(case)
    words = len((message or "").split())
    if (pending and words <= TIER0_MAX_WORDS
            and tier0_parse(pending, message, "probe") is not None):
        return 0
    return 1 if words <= 6 else 2


def scan_message(message: str, msg_id: str) -> dict:
    """Deterministic extras every family message is swept for, at any tier.

    Only things a regex reads with certainty: the state behind an Oklahoma city name,
    programs the family says it already has, a recertification deadline in days, and
    a "we don't have papers" disclosure that must reach the risk agent.
    """
    message = redact(message or "")
    t = _norm(message)
    q = message.strip()[:200]
    out: dict = {}

    if _has(t, OK_CITIES) or re.search(r"\boklahoma\b", t):
        out["state"] = Fact(value="OK", source_msg_id=msg_id, quote=q)

    programs = []
    for key, words in [
        ("snap", ["snap", "food stamps", "ebt", "كوبونات", "سناب", "المساعدة الغذائية"]),
        ("medicaid", ["medicaid", "soonercare", "ميديكيد", "سونركير"]),
        ("wic", ["wic", "الوايك", "برنامج wic"]),
        ("tanf", ["tanf", "cash assistance", "مساعدة نقدية"]),
    ]:
        if _has(t, words) and _has(t, ["already", "we get", "i get", "we receive", "on ",
                                       "نستلم", "ناخذ", "عندنا", "أستلم", "استلم", "معنا"]):
            programs.append(Fact(value=key, source_msg_id=msg_id, quote=q))
    if programs:
        out["already_receives"] = programs

    days = re.search(r"(?:due|renew\w*|recert\w*|expires?|تجديد|ينتهي|موعد)[^.\n]{0,40}?"
                     r"(\d{1,3})\s*(?:days?|يوم|أيام|ايام)", _ascii_digits(t))
    if not days:
        days = re.search(r"(?:in|خلال|بعد)\s*(\d{1,3})\s*(?:days?|يوم|أيام|ايام)", _ascii_digits(t))
    if days:
        out["recert_due_days"] = int(days.group(1))

    if _has(t, _NO_PAPERS):
        out["immigration_disclosed"] = Fact(
            value="not_all_kids_citizen" if _has(t, _BORN_HERE) else "not_all",
            source_msg_id=msg_id, quote=q)

    if _has(t, ["live alone", "living alone", "by myself", "on my own", "just me", "only me",
                "أعيش لوحدي", "اعيش لوحدي", "لوحدي", "بمفردي"]):
        out["household_size"] = Fact(value=1, source_msg_id=msg_id, quote=q)

    m = re.search(r"\b(?:i'?m|i am|im)\s+(\d{1,3})(?!\s*(?:hours?|dollars?|\$))",
                  _ascii_digits(t)) or re.search(r"(?:عمري|أنا عمري|انا عمري)\s*(\d{1,3})",
                                                 _ascii_digits(t))
    if m and 0 <= int(m.group(1)) <= 120:
        out["self_age"] = Fact(value=int(m.group(1)), source_msg_id=msg_id, quote=q)

    return out
