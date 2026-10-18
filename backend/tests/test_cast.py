"""Cast building (SPEC §15.4)."""

import pytest

from app.dialogue.cast import NameReport, build_cast, gender_from_pronouns, normalize_name


@pytest.mark.parametrize(
    "name, normal",
    [("the Mad Hatter", "mad hatter"), ("Mr. Glass", "glass"), ("Father Brown", "brown"), ("Dr Hood", "hood"),
     ("Father", "father"), ("ALICE!", "alice")],
)  # fmt: skip
def test_normalize_name(name: str, normal: str) -> None:
    assert normalize_name(name) == normal


def test_llm_aliases_merge() -> None:
    cast = build_cast([NameReport("Zaladin", 3, "male", ("Mr Glass",)), NameReport("Mr Glass", 1)])
    assert [(m.name, m.lines) for m in cast.members] == [("Zaladin", 4)]
    assert cast.resolve("Glass") is cast.members[0]


def test_subset_and_fuzzy_names_merge() -> None:
    cast = build_cast([NameReport("Hatter", 5), NameReport("the Mad Hatter", 2), NameReport("Dormouse", 1),
                       NameReport("Dormous", 1)])  # fmt: skip
    assert sorted((m.name, m.lines) for m in cast.members) == [("Dormouse", 2), ("Hatter", 7)]


def test_conflicting_genders_do_not_merge() -> None:
    cast = build_cast([NameReport("Mr Smith", 2, "male"), NameReport("Mrs Smith", 2, "female")])
    assert len(cast.members) == 2


def test_names_the_llm_declared_distinct_do_not_merge() -> None:
    reports = [NameReport("Mrs MacNab", 2, "female"), NameReport("Maggie MacNab", 3, "female")]
    assert len(build_cast(reports).members) == 1
    assert len(build_cast(reports, distinct={frozenset(("macnab", "maggie macnab"))}).members) == 2


def test_gender_from_llm_majority_then_pronouns() -> None:
    cast = build_cast(
        [NameReport("Alice", 1, "female"), NameReport("Alice", 1, "female"), NameReport("Alice", 1, "male"),
         NameReport("Hatter", 1)],
        pronoun_genders={"hatter": "male"},
    )  # fmt: skip
    genders = {m.name: m.gender for m in cast.members}
    assert genders == {"Alice": "female", "Hatter": "male"}
    assert gender_from_pronouns(["she said, and her eyes", "he"]) == "female"
    assert gender_from_pronouns(["he", "she"]) == "unknown"


def test_display_name_is_the_most_frequent_surface_form() -> None:
    cast = build_cast([NameReport("the Hatter", 1), NameReport("Hatter", 4)])
    assert cast.members[0].name == "Hatter" and "the Hatter" in cast.members[0].aliases
