"""Text normalization for speech (SPEC §6.5). Every rule is a small pure function.

`normalize_for_speech` is applied to body text, `normalize_heading` to chapter titles.
"""

import re
from decimal import Decimal

from num2words import num2words

from app.pipeline.titles import LABEL_WORD, ROMAN

# --------------------------------------------------------------------------- numbers

YEAR_MIN, YEAR_MAX = 1100, 2099  # 4-digit numbers in this range are read as years
MAX_NUMBER_DIGITS = 15  # longer digit runs (IDs, phone numbers) are read digit by digit
ALL_CAPS_MIN_LETTERS = 4  # ALL-CAPS words this long are emphasis, not acronyms -> lowercase

CURRENCIES = {
    "$": ("dollar", "dollars", "cent", "cents"),
    "€": ("euro", "euros", "cent", "cents"),
    "£": ("pound", "pounds", "penny", "pence"),
    "₹": ("rupee", "rupees", "paisa", "paise"),
}
SCALES = ("thousand", "million", "billion", "trillion")
MONTHS = (
    "January February March April May June July August September October November December".split()
)

_DIGITS = "0123456789"


def number_words(n: int) -> str:
    """Cardinal words without the commas num2words puts into long numbers."""
    if len(str(abs(n))) > MAX_NUMBER_DIGITS:
        return " ".join(num2words(int(d)) for d in str(abs(n)))
    return num2words(n).replace(",", "")


def decimal_words(text: str) -> str:
    """'3.14' -> 'three point one four' (Decimal keeps the digits exactly)."""
    return num2words(Decimal(text)).replace(",", "")


def year_words(n: int) -> str:
    return num2words(n, to="year").replace(",", "")


def _plural_decade(words: str) -> str:
    """'eighteen sixty' -> 'eighteen sixties'."""
    return words[:-1] + "ies" if words.endswith("y") else words + "s"


# --------------------------------------------------------------------------- rules (body text)

_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


def replace_urls_and_emails(text: str) -> str:
    """URLs -> 'link'; email addresses are dropped (§6.5)."""
    text = _EMAIL.sub("", text)
    return _URL.sub("link", text)


_ABBREVIATIONS = [
    (re.compile(r"\bDr\.(?=\s|$)"), "Doctor"),
    (re.compile(r"\bMrs\.(?=\s|$)"), "Missus"),
    (re.compile(r"\bMr\.(?=\s|$)"), "Mister"),
    (re.compile(r"\be\.g\.(?=\W|$)", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.(?=\W|$)", re.IGNORECASE), "that is"),
    (re.compile(r"\bvs\.(?=\s|$)", re.IGNORECASE), "versus"),
    (re.compile(r"\bapprox\.(?=\s|$)", re.IGNORECASE), "approximately"),
    (re.compile(r"\bFig\.(?=\s|$)"), "Figure"),
    (re.compile(r"\bNo\.(?=\s*\d)"), "number"),
    (re.compile(r"\bSt\.(?=\s+[A-Z])"), "Saint"),
    (re.compile(r"\bSt\.(?=\s|$)"), "Street"),
]
# "etc." keeps its period when it ends the sentence.
_ETC_END = re.compile(r"\betc\.(?=\s*$|\s+[A-Z\"“‘'])")
_ETC = re.compile(r"\betc\.")


def expand_abbreviations(text: str) -> str:
    """Dr., Mr., Mrs., St., e.g., i.e., etc., vs., approx., Fig., No. (§6.5)."""
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    text = _ETC_END.sub("et cetera.", text)
    return _ETC.sub("et cetera", text)


_STRAY_SYMBOLS = re.compile(r"[*#|•_~]")


def replace_symbols(text: str) -> str:
    """'&' -> 'and'; stray * # | • _ ~ are removed (§6.5)."""
    text = re.sub(r"\s*&\s*", " and ", text)
    return _STRAY_SYMBOLS.sub(" ", text)


_CURRENCY = re.compile(
    r"([$€£₹])\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?:\s+(" + "|".join(SCALES) + r")\b)?"
)


def _currency_words(match: re.Match[str]) -> str:
    one, many, sub_one, sub_many = CURRENCIES[match.group(1)]
    whole = int(match.group(2).replace(",", ""))
    fraction, scale = match.group(3), match.group(4)
    if scale:
        amount = match.group(2).replace(",", "") + (f".{fraction}" if fraction else "")
        return f"{decimal_words(amount) if fraction else number_words(whole)} {scale} {many}"
    words = f"{number_words(whole)} {one if whole == 1 else many}"
    if fraction:
        cents = int(fraction.ljust(2, "0"))
        if cents:
            words += f" and {number_words(cents)} {sub_one if cents == 1 else sub_many}"
    return words


def expand_currency(text: str) -> str:
    """'$5.50' -> 'five dollars and fifty cents', '₹2 million' -> 'two million rupees'."""
    return _CURRENCY.sub(_currency_words, text)


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def expand_percentages(text: str) -> str:
    return _PERCENT.sub(lambda m: f"{_number_text(m.group(1))} percent", text)


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(rf"\b({'|'.join(MONTHS)})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({'|'.join(MONTHS)})\b")


def _iso_date(match: re.Match[str]) -> str:
    year, month, day = (int(g) for g in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return match.group(0)
    return f"the {num2words(day, to='ordinal')} of {MONTHS[month - 1]} {year_words(year)}"


def expand_dates(text: str) -> str:
    """'2024-10-16' -> 'the sixteenth of October twenty twenty-four'; 'October 16' and '16 October'
    -> ordinal days."""
    text = _ISO_DATE.sub(_iso_date, text)
    text = _MONTH_DAY.sub(lambda m: f"{m.group(1)} {num2words(int(m.group(2)), to='ordinal')}", text)
    return _DAY_MONTH.sub(lambda m: f"the {num2words(int(m.group(1)), to='ordinal')} of {m.group(2)}", text)


_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.IGNORECASE)


def expand_ordinals(text: str) -> str:
    """'21st' -> 'twenty-first'."""
    return _ORDINAL.sub(lambda m: num2words(int(m.group(1)), to="ordinal"), text)


_RANGE = re.compile(r"(?<![\d.,-])(\d+)\s?[–—-]\s?(\d+)(?![\d.,]*\d)(?!-)")


def expand_ranges(text: str) -> str:
    """'10–20' -> '10 to 20' (the numbers are spoken by the later rules)."""
    return _RANGE.sub(lambda m: f"{m.group(1)} to {m.group(2)}", text)


_DECADE = re.compile(r"\b(1[1-9]\d0|20\d0)s\b")
_YEAR = re.compile(r"(?<![\d,.])\b(1[1-9]\d\d|20\d\d)\b(?![,.]\d)")


def expand_years(text: str) -> str:
    """'1865' -> 'eighteen sixty-five', '1860s' -> 'eighteen sixties' (1100-2099, no comma)."""
    text = _DECADE.sub(lambda m: _plural_decade(year_words(int(m.group(1)))), text)

    def year(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return year_words(n) if YEAR_MIN <= n <= YEAR_MAX else m.group(0)

    return _YEAR.sub(year, text)


_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\w])")


def _number_text(text: str) -> str:
    text = text.replace(",", "")
    if "." in text:
        return decimal_words(text)
    if len(text) > 1 and text.startswith("0"):
        return " ".join(num2words(int(d)) for d in text)  # "007" -> "zero zero seven"
    return number_words(int(text))


def expand_numbers(text: str) -> str:
    """Integers (with thousands commas), decimals."""
    return _NUMBER.sub(lambda m: _number_text(m.group(1) + (m.group(2) or "")), text)


_ROMAN_WORD = re.compile(rf"^{ROMAN}$", re.IGNORECASE)
_CAPS_WORD = re.compile(rf"\b[A-Z]{{{ALL_CAPS_MIN_LETTERS},}}\b")


def lowercase_shouting(text: str) -> str:
    """ALL-CAPS words of 4+ letters ('VERY') -> lowercase so they are not spelled out. Shorter caps
    (likely acronyms: 'AI', 'USA') and roman numerals ('VIII') are left alone."""
    return _CAPS_WORD.sub(lambda m: m.group(0) if _ROMAN_WORD.match(m.group(0)) else m.group(0).lower(), text)


_REPEATED_PUNCT = [
    (re.compile(r"([!?])[!?]+"), r"\1"),
    (re.compile(r"\.{4,}"), "..."),
    (re.compile(r"([,;:])\1+"), r"\1"),
    (re.compile(r"\s+([,.;:!?])"), r"\1"),
]
_SPACES = re.compile(r"\s+")


def tidy_punctuation(text: str) -> str:
    """Collapse repeated punctuation and whitespace."""
    for pattern, replacement in _REPEATED_PUNCT:
        text = pattern.sub(replacement, text)
    return _SPACES.sub(" ", text).strip()


BODY_RULES = (
    replace_urls_and_emails,
    expand_abbreviations,
    replace_symbols,
    expand_currency,
    expand_percentages,
    expand_dates,
    expand_ordinals,
    expand_ranges,
    expand_years,
    expand_numbers,
    lowercase_shouting,
    tidy_punctuation,
)


def normalize_for_speech(text: str) -> str:
    """Apply every body rule in order. Roman numerals are never touched in body text."""
    for rule in BODY_RULES:
        text = rule(text)
    return text


# --------------------------------------------------------------------------- headings

_HEADING_LABEL = re.compile(rf"^({LABEL_WORD})\s+({ROMAN}|\d+)\b\s*[:.\-–—]?\s*", re.IGNORECASE)
_HEADING_BARE_ROMAN = re.compile(rf"^({ROMAN})\.\s+", re.IGNORECASE)
_HEADING_SECTION = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+")


def roman_to_int(numeral: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    digits = [values[c] for c in numeral.lower()]
    for i, v in enumerate(digits):
        total += -v if i + 1 < len(digits) and v < digits[i + 1] else v
    return total


def _number_or_roman(token: str) -> str:
    return number_words(int(token)) if token.isdigit() else number_words(roman_to_int(token))


def speak_heading_number(title: str) -> str:
    """'Chapter XII: Alice's Evidence' -> 'Chapter twelve. Alice's Evidence';
    'XVII. The Idiot' -> 'Seventeen. The Idiot'; '4.1 Random Variables' -> 'four point one. Random
    Variables'. Roman numerals are converted only here, never in body text."""
    m = _HEADING_LABEL.match(title)
    if m:
        rest = title[m.end():]
        spoken = f"{m.group(1)} {_number_or_roman(m.group(2))}"
        return f"{spoken}. {rest}" if rest else spoken
    m = _HEADING_BARE_ROMAN.match(title)
    if m:
        return f"{_number_or_roman(m.group(1)).capitalize()}. {title[m.end():]}"
    m = _HEADING_SECTION.match(title)
    if m:
        number = m.group(1)
        spoken = decimal_words(number) if "." in number else number_words(int(number))
        return f"{spoken}. {title[m.end():]}"
    return title


def title_case_if_all_caps(title: str) -> str:
    """'THE IDIOT' -> 'The Idiot', so short capitalised words are not spelled out."""
    letters = [c for c in title if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return " ".join(w.capitalize() for w in title.split())
    return title


def normalize_heading(title: str) -> str:
    """Chapter title as it is spoken at the start of the chapter."""
    title = speak_heading_number(title_case_if_all_caps(title.strip()))
    return normalize_for_speech(title)
