import pytest

from app.pipeline.titles import match_titles, normalize_title, prf


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
