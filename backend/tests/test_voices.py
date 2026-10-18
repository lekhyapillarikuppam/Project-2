"""Voice casting (SPEC §15.5, D41): reservations from the book pass, lazy casting per chapter."""

import json
from pathlib import Path

from app.dialogue.cast import CastMember, NameReport
from app.dialogue.voices import cast_for_chapter, load_voice_pool, new_casting

NARRATOR = "en-US-AriaNeural"


def member(name: str, gender: str) -> CastMember:
    return CastMember(name, gender, 0)


def small_pool(tmp_path: Path, female: int, male: int) -> Path:
    data = {
        "female": [{"id": f"f{i}", "label": f"F{i}"} for i in range(female)],
        "male": [{"id": f"m{i}", "label": f"M{i}"} for i in range(male)],
    }
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_pool_excludes_the_narrator_and_is_split_by_gender() -> None:
    pool = load_voice_pool(exclude=NARRATOR)
    assert NARRATOR not in {v.id for v in pool.female + pool.male}
    assert {v.gender for v in pool.female} == {"female"} and {v.gender for v in pool.male} == {"male"}


def test_reservations_need_a_capitalised_name_and_three_explicit_lines() -> None:
    casting = new_casting(NARRATOR, 8)
    casting.reserve([NameReport("Alice", 9), NameReport("the girl", 12), NameReport("Hatter", 3),
                     NameReport("Dodo", 2)])  # fmt: skip
    assert casting.is_reserved(member("Alice", "female")) and casting.is_reserved(member("the Hatter", "male"))
    assert not casting.is_reserved(member("the girl", "female"))  # an epithet never takes a slot
    assert not casting.is_reserved(member("Dodo", "male"))


def test_reservations_are_capped_at_max_cast_minus_two() -> None:
    casting = new_casting(NARRATOR, 4)
    casting.reserve([NameReport(n, 10 - i) for i, n in enumerate(["Ann", "Bob", "Cy", "Di", "Ed"])])
    assert len(casting.reserved) == 2


def test_a_reserved_character_gets_the_llm_gender() -> None:
    casting = new_casting(NARRATOR, 8)
    casting.reserve([NameReport("March Hare", 5)])  # the baseline couldn't tell its gender
    casting.cast_chapter([(member("March Hare", "male"), 1)])  # the LLM says male
    voice = casting.voice_for(member("March Hare", "male"))
    assert voice.gender == "male" and voice != casting.minor["male"]


def test_an_epithet_with_many_lines_cannot_take_a_reserved_slot(tmp_path: Path) -> None:
    casting = new_casting(NARRATOR, 4, small_pool(tmp_path, female=4, male=4))
    casting.reserve([NameReport("Ann", 5), NameReport("Bob", 5), NameReport("the girl", 30)])
    casting.cast_chapter([(member("the girl", "female"), 30), (member("Cy", "male"), 3)])
    # 4 slots, 2 held for Ann and Bob: the girl and Cy take the other two
    assert casting.voice_for(member("the girl", "female")).id == "f0"
    assert casting.voice_for(member("Cy", "male")).id == "m0"
    casting.cast_chapter([(member("Dan", "male"), 5)])  # no slots left (Ann, Bob still held)
    assert casting.voice_for(member("Dan", "male")) == casting.minor["male"]
    casting.cast_chapter([(member("Ann", "female"), 1), (member("Bob", "male"), 1)])
    assert casting.voice_for(member("Ann", "female")).id == "f1"
    assert casting.voice_for(member("Bob", "male")).id == "m1"


def test_a_late_major_character_gets_a_distinct_voice_when_slots_remain() -> None:
    casting = new_casting(NARRATOR, 8)
    casting.reserve([NameReport("Alice", 20)])
    casting.cast_chapter([(member("Alice", "female"), 10)])
    casting.cast_chapter([(member("Queen", "female"), 6)])  # first appears in a later chapter
    queen = casting.voice_for(member("Queen", "female"))
    assert queen not in (casting.voice_for(member("Alice", "female")), casting.minor["female"])


def test_one_line_characters_get_the_minor_voice() -> None:
    casting = new_casting(NARRATOR, 8)
    casting.cast_chapter([(member("Eaglet", "male"), 1), (member("Lory", "female"), 1), (member("Duck", "unknown"), 1)])
    assert casting.voice_for(member("Eaglet", "male")) == casting.minor["male"]
    assert casting.voice_for(member("Lory", "female")) == casting.minor["female"]
    assert casting.voice_for(member("Duck", "unknown")) == casting.minor_voice("unknown")
    assert casting.voice_for(None) == casting.narrator


def test_voices_never_change_once_assigned() -> None:
    casting = new_casting(NARRATOR, 8)
    casting.cast_chapter([(member("Dodo", "male"), 1)])  # minor voice in chapter 1
    first = casting.voice_for(member("Dodo", "male"))
    casting.cast_chapter([(member("Dodo", "male"), 9)])  # a big part later: still the same voice
    assert casting.voice_for(member("Dodo", "male")) == first


def test_assignment_is_deterministic() -> None:
    def run() -> list[tuple[str, str]]:
        casting = new_casting(NARRATOR, 8)
        casting.reserve([NameReport("Alice", 9), NameReport("Hatter", 5)])
        found = [NameReport("Alice", 4, "female"), NameReport("Hatter", 3, "male"), NameReport("Dormouse", 2, "male")]
        cast = cast_for_chapter(casting, found, {}, set(), ["Dormouse", "Alice", "Hatter", "Dormouse", "Alice"])
        return [(m.name, casting.voice_for(m).id) for m in cast.members]

    assert run() == run()
