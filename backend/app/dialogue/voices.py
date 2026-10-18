"""Voice casting (SPEC §15.5): which edge-tts voice reads each character.

The `max_cast` characters ranked first (by the baseline's line counts over the whole book) get
distinct voices: females from the female pool, males from the male pool, `unknown` from whichever
pool has more unused voices. Everyone else shares the minor voice of their gender's pool (the last
voice of each pool, never handed out as a distinct voice). Characters first found later (by the
LLM) get the next free distinct voice while fewer than `max_cast` are assigned, else the minor one.
A voice never changes once assigned. `UNKNOWN` speakers are read by the narrator (D33).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.dialogue.cast import Cast, CastMember, NameReport, build_cast, normalize_name

VOICE_POOL_PATH = Path(__file__).resolve().parent / "voice_pool.json"


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
    assigned: dict[str, Voice] = field(default_factory=dict)  # normalised name -> voice, in order

    @property
    def minor(self) -> dict[str, Voice]:
        return {"female": self.pool.female[-1], "male": self.pool.male[-1]}

    def _distinct_free(self, gender: str) -> list[Voice]:
        used = set(self.assigned.values())
        return [v for v in self.pool.pool(gender)[:-1] if v not in used]

    def _distinct_count(self) -> int:
        minors = set(self.minor.values())
        return len({v for v in self.assigned.values() if v not in minors})

    def _pick(self, gender: str) -> Voice:
        if self._distinct_count() < self.max_cast:
            pool_gender = gender
            if gender == "unknown":
                female, male = self._distinct_free("female"), self._distinct_free("male")
                pool_gender = "female" if len(female) >= len(male) else "male"
            free = self._distinct_free(pool_gender)
            if free:
                return free[0]
        return self.minor_voice(gender)

    def _assigned_voice(self, member: CastMember) -> Voice | None:
        keys = member.keys()
        return next((v for k, v in self.assigned.items() if k in keys), None)

    def assign(self, members: list[CastMember]) -> None:
        """Give every member without a voice one, in the order given (ranking first)."""
        for member in members:
            if self._assigned_voice(member) is None:
                self.assigned[normalize_name(member.name)] = self._pick(member.gender)

    def voice_for(self, member: CastMember | None) -> Voice:
        """The member's voice; the narrator for None (UNKNOWN) or a member never assigned."""
        if member is None:
            return self.narrator
        return self._assigned_voice(member) or self.narrator

    def minor_voice(self, gender: str) -> Voice:
        """The shared minor voice for a gender (`unknown`: the larger pool's)."""
        if gender == "unknown":
            gender = "female" if len(self.pool.female) >= len(self.pool.male) else "male"
        return self.minor[gender]


def new_casting(narrator_id: str, max_cast: int, pool_path: Path = VOICE_POOL_PATH) -> VoiceCasting:
    pool = load_voice_pool(pool_path, exclude=narrator_id)
    return VoiceCasting(pool, Voice(narrator_id, "Narrator", "unknown"), max_cast)


def cast_with_voices(
    casting: VoiceCasting,
    ranking: list[NameReport],
    pronoun_genders: dict[str, str],
    found: list[NameReport],
    distinct: set[frozenset[str]] | None = None,
) -> Cast:
    """Voices for the ranked cast first (the whole-book baseline pass), then the cast including the
    characters attribution `found` (LLM roster), whose new members get the next free voices."""
    casting.assign(build_cast(ranking, pronoun_genders).members)
    cast = build_cast(ranking + found, pronoun_genders, distinct)
    casting.assign(cast.members)
    return cast
