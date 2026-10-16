import pytest

from app.tts.normalize import (
    expand_abbreviations,
    expand_currency,
    expand_dates,
    expand_numbers,
    expand_ordinals,
    expand_percentages,
    expand_ranges,
    expand_years,
    lowercase_shouting,
    normalize_for_speech,
    normalize_heading,
    replace_symbols,
    replace_urls_and_emails,
    roman_to_int,
    tidy_punctuation,
)


def test_integers_with_commas_and_plain() -> None:
    assert expand_numbers("1,250 men") == "one thousand two hundred and fifty men"
    assert expand_numbers("room 42") == "room forty-two"
    assert expand_numbers("agent 007") == "agent zero zero seven"


def test_decimals() -> None:
    assert expand_numbers("pi is 3.14") == "pi is three point one four"
    assert expand_numbers("version v2 stays") == "version v2 stays"


def test_percentages() -> None:
    assert expand_percentages("up 3.5% and 20 %") == "up three point five percent and twenty percent"


def test_ordinals() -> None:
    assert expand_ordinals("the 21st and 2nd") == "the twenty-first and second"


def test_years() -> None:
    assert expand_years("born in 1865.") == "born in eighteen sixty-five."
    assert expand_years("in 2024") == "in twenty twenty-four"
    assert expand_years("the 1860s") == "the eighteen sixties"
    assert expand_years("1,865 copies") == "1,865 copies"  # comma: a count, not a year
    assert expand_years("year 1099 and 2100") == "year 1099 and 2100"  # outside 1100-2099


def test_currency() -> None:
    assert expand_currency("$5.50") == "five dollars and fifty cents"
    assert expand_currency("$1") == "one dollar"
    assert expand_currency("£12") == "twelve pounds"
    assert expand_currency("€0.01") == "zero euros and one cent"
    assert expand_currency("₹1,250") == "one thousand two hundred and fifty rupees"
    assert expand_currency("$2 million") == "two million dollars"
    assert normalize_for_speech("It cost $1865.") == "It cost one thousand eight hundred and sixty-five dollars."


def test_ranges() -> None:
    assert expand_ranges("pages 10–20") == "pages 10 to 20"
    assert normalize_for_speech("pages 10–20") == "pages ten to twenty"
    assert normalize_for_speech("1914-1918") == "nineteen fourteen to nineteen eighteen"


def test_dates() -> None:
    assert expand_dates("on 2024-10-16") == "on the sixteenth of October twenty twenty-four"
    assert expand_dates("October 3rd") == "October third"
    assert expand_dates("3 October") == "the third of October"


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Dr. Watson", "Doctor Watson"),
        ("Mr. Brown", "Mister Brown"),
        ("Mrs. Hudson", "Missus Hudson"),
        ("e.g. this", "for example this"),
        ("i.e. that", "that is that"),
        ("apples, pears, etc. and more", "apples, pears, et cetera and more"),
        ("apples, pears, etc. Then", "apples, pears, et cetera. Then"),
        ("cats vs. dogs", "cats versus dogs"),
        ("approx. ten", "approximately ten"),
        ("see Fig. 2", "see Figure 2"),
        ("St. Paul", "Saint Paul"),
        ("Baker St. near", "Baker Street near"),
        ("No. 7", "number 7"),
        ("No. I won't", "No. I won't"),
    ],
)
def test_abbreviations(raw: str, spoken: str) -> None:
    assert expand_abbreviations(raw) == spoken


def test_urls_and_emails() -> None:
    assert replace_urls_and_emails("see https://example.com/a?b=1 now") == "see link now"
    assert replace_urls_and_emails("www.gutenberg.org") == "link"
    assert replace_urls_and_emails("mail help@example.com today").split() == ["mail", "today"]


def test_symbols_and_ampersand() -> None:
    assert tidy_punctuation(replace_symbols("salt & pepper * # | • _ ~ done")) == "salt and pepper done"


def test_repeated_punctuation() -> None:
    assert tidy_punctuation("What?!? No!!! Wait..... ok ,, fine") == "What? No! Wait... ok, fine"


def test_all_caps_lowered_but_acronyms_kept() -> None:
    assert lowercase_shouting("nothing so VERY remarkable") == "nothing so very remarkable"
    assert lowercase_shouting("the AI from the USA") == "the AI from the USA"
    assert lowercase_shouting("Henry VIII") == "Henry VIII"  # a roman numeral, not shouting


def test_pronoun_i_and_body_roman_numerals_survive() -> None:
    assert normalize_for_speech("I think I shall go, said I.") == "I think I shall go, said I."
    assert normalize_for_speech("World War II ended.") == "World War II ended."


def test_heading_roman_numerals() -> None:
    assert normalize_heading("Chapter XII: Alice's Evidence") == "Chapter twelve. Alice's Evidence"
    assert normalize_heading("CHAPTER IV") == "Chapter four"
    assert normalize_heading("XVII. THE IDIOT") == "Seventeen. The Idiot"
    assert normalize_heading("Chapter 3 The Pool") == "Chapter three. The Pool"


def test_heading_section_numbers() -> None:
    assert normalize_heading("4.1 Random Variables") == "four point one. Random Variables"
    assert normalize_heading("2 Eigenvalues and Eigenvectors") == "two. Eigenvalues and Eigenvectors"
    assert normalize_heading("I Am Legend") == "I Am Legend"


def test_roman_to_int() -> None:
    assert [roman_to_int(r) for r in ("i", "IV", "ix", "XII", "xlii", "MCMXC")] == [1, 4, 9, 12, 42, 1990]


def test_full_sentence() -> None:
    text = "Dr. Smith paid $5.50 on 21st May 1865 — about 3% of it & more!!"
    assert normalize_for_speech(text) == (
        "Doctor Smith paid five dollars and fifty cents on the twenty-first of May eighteen sixty-five"
        " — about three percent of it and more!"
    )
