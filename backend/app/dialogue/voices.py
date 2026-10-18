"""Voice casting (SPEC §15.5, D41): which edge-tts voice reads each character.

Voices are assigned lazily, just before a chapter is narrated, from that chapter's attribution
(names and genders from the LLM), and never change once assigned:
- the whole-book baseline pass only *reserves* distinct voices: up to `max_cast - 2` capitalised
  names (never lowercase epithets like `the girl`) with at least 3 explicitly tagged lines;
  a reserved character gets its distinct voice on first narration, with the gender known then;
- an unreserved character gets a free distinct voice if it has at least 2 lines in the chapter
  and slots remain (reserved-but-unused slots are held back); otherwise the minor voice of its
  gender (the last voice of each pool, never handed out as a distinct voice).
Distinct voices come from the pool of the character's gender (`unknown`: the pool with more
unused voices). `UNKNOWN` speakers are read by the narrator (D33).
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.dialogue.cast import Cast, CastMember, NameReport, build_cast, normalize_name

VOICE_POOL_PATH = Path(__file__).resolve().parent / "voice_pool.json"
RESERVE_MIN_EXPLICIT_LINES = 3  # whole book, explicitly tagged (baseline pass)
UNRESERVED_MIN_CHAPTER_LINES = 2  # in the chapter about to be narrated
RESERVE_SLOTS_KEPT_FREE = 2  # reservations take at most max_cast - 2 slots


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    gender: str  # female | male (unknown for the narrator)


@dataclass(frozen=True)
class VoicePool:
    female: tuple[Voice, ...]
    male: tuple[Voice, ...]

    def pool(self, gender: str) -> tuple[Voice, ...]:
        return self.female if gender == "female" else self.male


def load_voice_pool(path: Path = VOICE_POOL_PATH, exclude: str | None = None) -> VoicePool:
    """The pools from voice_pool.json, without `exclude` (the narrator voice)."""
    data = json.loads(path.read_text(encoding="utf-8"))

    def voices(gender: str) -> tuple[Voice, ...]:
        return tuple(Voice(v["id"], v["label"], gender) for v in data[gender] if v["id"] != exclude)

    return VoicePool(voices("female"), voices("male"))


@dataclass
class VoiceCasting:
    pool: VoicePool
    narrator: Voice
    max_cast: int
    reserved: list[set[str]] = field(default_factory=list)  # normalised name sets, ranking order
    assigned: dict[str, Voice] = field(default_factory=dict)  # normalised name -> voice, in order

    @property
    def minor(self) -> dict[str, Voice]:
        return {"female": self.pool.female[-1], "male": self.pool.male[-1]}

    def minor_voice(self, gender: str) -> Voice:
        """The shared minor voice for a gender (`unknown`: the larger pool's)."""
        if gender == "unknown":
            gender = "female" if len(self.pool.female) >= len(self.pool.male) else "male"
        return self.minor[gender]

    # -- reservations (the whole-book baseline pass) ----------------------------------------
    def reserve(self, ranking: list[NameReport]) -> None:
        """Reserve distinct voices for up to `max_cast - 2` capitalised names with at least 3
        explicitly tagged lines; `ranking` holds explicit line counts per surface name."""
        names = [r for r in ranking if r.name[:1].isupper()]
        cast = build_cast(names)
        limit = max(0, self.max_cast - RESERVE_SLOTS_KEPT_FREE)
        self.reserved = [m.keys() for m in cast.members if m.lines >= RESERVE_MIN_EXPLICIT_LINES][:limit]

    def is_reserved(self, member: CastMember) -> bool:
        keys = member.keys()
        return any(keys & r for r in self.reserved)

    def _pending_reservations(self) -> int:
        return sum(1 for r in self.reserved if not any(k in r for k in self.assigned))

    # -- assignment ---------------------------------------------------------------------------
    def _distinct_free(self, gender: str) -> list[Voice]:
        used = set(self.assigned.values())
        return [v for v in self.pool.pool(gender)[:-1] if v not in used]

    def _distinct_count(self) -> int:
        minors = set(self.minor.values())
        return len({v for v in self.assigned.values() if v not in minors})

    def _distinct(self, gender: str) -> Voice | None:
        if gender == "unknown":
            female, male = self._distinct_free("female"), self._distinct_free("male")
            gender = "female" if len(female) >= len(male) else "male"
        free = self._distinct_free(gender)
        return free[0] if free else None

    def assigned_voice(self, member: CastMember) -> Voice | None:
        keys = member.keys()
        return next((v for k, v in self.assigned.items() if k in keys), None)

    def cast_chapter(self, members: list[tuple[CastMember, int]]) -> None:
        """Assign voices to the chapter's speakers (member, lines in this chapter) that have none
        yet, most lines first. Assigned voices never change."""
        for member, lines in sorted(members, key=lambda ml: (-ml[1], ml[0].name.casefold())):
            if self.assigned_voice(member) is not None:
                continue
            voice: Voice | None = None
            if self.is_reserved(member):
                voice = self._distinct(member.gender)
            elif (
                lines >= UNRESERVED_MIN_CHAPTER_LINES
                and self._distinct_count() + self._pending_reservations() < self.max_cast
            ):
                voice = self._distinct(member.gender)
            self.assigned[normalize_name(member.name)] = voice or self.minor_voice(member.gender)

    def voice_for(self, member: CastMember | None) -> Voice:
        """The member's voice; the narrator for None (UNKNOWN) or a member never assigned."""
        if member is None:
            return self.narrator
        return self.assigned_voice(member) or self.narrator


def new_casting(narrator_id: str, max_cast: int, pool_path: Path = VOICE_POOL_PATH) -> VoiceCasting:
    pool = load_voice_pool(pool_path, exclude=narrator_id)
    return VoiceCasting(pool, Voice(narrator_id, "Narrator", "unknown"), max_cast)


def cast_for_chapter(
    casting: VoiceCasting,
    found: list[NameReport],
    pronoun_genders: dict[str, str],
    distinct: set[frozenset[str]],
    chapter_speakers: list[str],
) -> Cast:
    """The book's cast so far (`found`: every name attribution produced, with LLM genders and
    aliases), and voices for this chapter's speakers (one entry per utterance, UNKNOWN excluded)."""
    cast = build_cast(found, pronoun_genders, distinct)
    counts: Counter[int] = Counter()
    for speaker in chapter_speakers:
        member = cast.resolve(speaker)
        if member is not None:
            counts[cast.members.index(member)] += 1
    casting.cast_chapter([(cast.members[i], n) for i, n in counts.items()])
    return cast
