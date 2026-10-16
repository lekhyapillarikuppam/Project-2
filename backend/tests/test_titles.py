import pytest

from app.pipeline.titles import match_titles, normalize_title, prf, title_score


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chapter I: Down the Rabbit-Hole", "down the rabbit hole"),
        ("CHAPTER 3. The Pool of Tears", "the pool of tears"),
        ("Part Two - The Return", "the return"),
        ("Lecture 12 Eigenvalues", "eigenvalues"),
        ("4.1 Random Variables", "random variables"),
        ("2 Eigenvalues and Eigenvectors", "eigenvalues and eigenvectors"),
        ("The Duel (Part 2)", "the duel"),
        ("Who Stole the Tarts?", "who stole the tarts"),
        ("  Alice’s   Evidence ", "alices evidence"),
        ("Chapter Summary", "chapter summary"),  # "Summary" is not a number
        ("Book Did It", "book did it"),  # "Did" is not a roman numeral
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_perfect_match_and_order() -> None:
    labeled = ["Down the Rabbit-Hole", "The Pool of Tears"]
    detected = ["Chapter I: Down the Rabbit-Hole", "Chapter II: The Pool of Tears"]
    r = match_titles(detected, labeled, [])
    assert (r.tp, r.precision, r.recall, r.f1, r.order_ok) == (2, 1.0, 1.0, 1.0, True)


def test_fuzzy_threshold() -> None:
    r = match_titles(["The Pool of Tear"], ["The Pool of Tears"], [])
    assert r.tp == 1
    r = match_titles(["Something Else Entirely"], ["The Pool of Tears"], [])
    assert r.tp == 0 and r.unmatched_detected == ["Something Else Entirely"]
    assert r.unmatched_labeled == ["The Pool of Tears"]


def test_one_to_one_greedy_matching() -> None:
    r = match_titles(["Introduction", "Introduction"], ["Introduction"], [])
    assert r.tp == 1 and r.detected == 2 and r.precision == 0.5 and r.recall == 1.0


def test_optional_titles_are_not_counted() -> None:
    r = match_titles(["Introduction", "Random Variables"], ["Random Variables"], ["Introduction"])
    assert r.detected == 1 and r.precision == 1.0 and r.optional_matched == ["Introduction"]
    # An unmatched optional label is ignored.
    r = match_titles(["Random Variables"], ["Random Variables"], ["Abstract"])
    assert r.f1 == 1.0


def test_required_label_wins_over_optional() -> None:
    r = match_titles(["Introduction"], ["Introduction"], ["Introduction"])
    assert r.tp == 1 and r.optional_matched == []


def test_order_not_required_for_match_but_reported() -> None:
    r = match_titles(["B title", "A title"], ["A title", "B title"], [])
    assert r.tp == 2 and not r.order_ok


def test_prf_edge_cases() -> None:
    assert prf(0, 0, 0) == (1.0, 1.0, 1.0)
    assert prf(0, 0, 5) == (0.0, 0.0, 0.0)
    p, r, f = prf(3, 4, 6)
    assert (round(p, 3), round(r, 3), round(f, 3)) == (0.75, 0.5, 0.6)


def test_label_only_title_keeps_its_label() -> None:
    assert normalize_title("Chapter 12") == "chapter 12"
    assert normalize_title("CHAPTER XII.") == "chapter xii"
    assert normalize_title("") == ""


def test_label_only_titles_match_only_when_identical() -> None:
    assert title_score("Chapter 12", "Chapter 12") == 100
    assert title_score("Chapter 12", "Chapter 13") == 0
    assert match_titles(["Chapter 12"], ["Chapter 12"], []).tp == 1
    assert match_titles(["Chapter 12"], ["Chapter 13"], []).tp == 0


def test_empty_titles_never_match() -> None:
    assert title_score("", "") == 0
    assert title_score("...", "") == 0
    result = match_titles([""], [""], [])
    assert result.tp == 0 and result.f1 == 0.0


def test_bare_number_prefix_is_stripped() -> None:
    assert normalize_title("XVII. THE IDIOT") == "the idiot"
    assert title_score("XVII. THE IDIOT", "The Idiot") == 100
    assert title_score("3. Convexity", "Convexity") == 100
    assert title_score("I. A Discussion", "A Discussion") == 100


def test_roman_without_period_is_not_stripped() -> None:
    assert normalize_title("I Am Legend") == "i am legend"
