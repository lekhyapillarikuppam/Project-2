"""Voice casting (SPEC §15.5)."""

import json
from pathlib import Path

from app.dialogue.cast import CastMember, NameReport
from app.dialogue.voices import cast_with_voices, load_voice_pool, new_casting

NARRATOR = "en-US-AriaNeural"


def member(name: str, gender: str, lines: int = 1) -> CastMember:
    return CastMember(name, gender, lines)


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


def test_ranked_characters_get_distinct_voices_by_gender() -> None:
    casting = new_casting(NARRATOR, 8)
    alice, hatter, hare = member("Alice", "female"), member("Hatter", "male"), member("Hare", "male")
    casting.assign([alice, hatter, hare])
    voices = [casting.voice_for(m) for m in (alice, hatter, hare)]
    assert len(set(voices)) == 3
    assert voices[0].gender == "female" and voices[1].gender == voices[2].gender == "male"
    assert casting.voice_for(None) == casting.narrator


def test_unknown_gender_uses_the_pool_with_more_unused_voices(tmp_path: Path) -> None:
    casting = new_casting(NARRATOR, 8, small_pool(tmp_path, female=3, male=5))
    casting.assign([member("A", "unknown")])
    assert casting.voice_for(member("A", "unknown")).gender == "male"


def test_cap_and_pool_running_out_lead_to_minor_voices(tmp_path: Path) -> None:
    casting = new_casting(NARRATOR, 3, small_pool(tmp_path, female=3, male=3))
    women = [member(f"W{i}", "female") for i in range(4)]
    men = [member(f"M{i}", "male") for i in range(2)]
    casting.assign(women + men)
    voices = {m.name: casting.voice_for(m).id for m in women + men}
    # pool f0, f1 (f2 is the minor female voice); the cap of 3 distinct voices is reached by W0, W1, M?
    assert voices["W0"] == "f0" and voices["W1"] == "f1"
    assert voices["W2"] == voices["W3"] == "f2"  # female pool used up: minor
    assert voices["M0"] == "m0" and voices["M1"] == "m2"  # cap reached: minor male


def test_voices_never_change_and_new_characters_get_the_next_free_one() -> None:
    casting = new_casting(NARRATOR, 8)
    alice = member("Alice", "female")
    casting.assign([alice])
    first = casting.voice_for(alice)
    casting.assign([member("Queen", "female"), alice])
    assert casting.voice_for(alice) == first
    assert casting.voice_for(member("Queen", "female")) not in (first, casting.minor["female"])


def test_assignment_is_deterministic() -> None:
    reports = [NameReport("Alice", 9), NameReport("Hatter", 5), NameReport("Dormouse", 2)]
    genders = {"alice": "female", "hatter": "male", "dormouse": "male"}
    runs = []
    for _ in range(2):
        casting = new_casting(NARRATOR, 8)
        cast = cast_with_voices(casting, reports, genders, [NameReport("Queen", 1, "female")])
        runs.append([(m.name, casting.voice_for(m).id) for m in cast.members])
    assert runs[0] == runs[1]
    assert [name for name, _ in runs[0]] == ["Alice", "Hatter", "Dormouse", "Queen"]
