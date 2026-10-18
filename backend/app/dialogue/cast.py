"""Cast building (SPEC §15.4): merge the character names that attribution produced into one cast.

Pure functions only. Names are compared by a normalised form (leading `the`, honorifics and
punctuation stripped, casefolded). Two names are merged when the LLM listed one as an alias of the
other, or when rapidfuzz `token_set_ratio` >= 90 or one name's tokens are a subset of the other's
(`Hatter` ⊂ `the Mad Hatter`), and only when their genders don't conflict.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

GENDERS = ("female", "male", "unknown")
HONORIFICS = frozenset({"mr", "mrs", "miss", "dr", "professor", "sir", "lady", "father"})
FUZZY_MERGE_MIN_RATIO = 90
# Gender from pronouns: how far after a character's speech tag to count he/she (characters).
PRONOUN_WINDOW_CHARS = 200

_PUNCTUATION = re.compile(r"[^\w\s-]")
_MALE = re.compile(r"\b(?:he|him|his|himself)\b", re.IGNORECASE)
_FEMALE = re.compile(r"\b(?:she|her|hers|herself)\b", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """`the Mad Hatter` -> `mad hatter`, `Mr. Glass` -> `glass`, `Father Brown` -> `brown`.
    A name that is only an honorific (`Father`) keeps it."""
    words = _PUNCTUATION.sub(" ", name.replace("’", "'")).casefold().split()
    if words[:1] == ["the"]:
        words = words[1:]
    stripped = [w for w in words if w not in HONORIFICS]
    return " ".join(stripped or words)


def gender_from_pronouns(texts: list[str]) -> str:
    """`male` / `female` when one set of pronouns is strictly more frequent in `texts`, else `unknown`."""
    male = sum(len(_MALE.findall(t)) for t in texts)
    female = sum(len(_FEMALE.findall(t)) for t in texts)
    if male == female:
        return "unknown"
    return "male" if male > female else "female"


@dataclass
class NameReport:
    """One source's view of a character: a surface name, its line count, and (LLM only) a gender
    and aliases. `gender` None means the source didn't say (the baseline)."""

    name: str
    lines: int = 0
    gender: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class CastMember:
    name: str  # display name: the most frequent surface form
    gender: str  # female | male | unknown
    lines: int
    aliases: list[str] = field(default_factory=list)

    def keys(self) -> set[str]:
        """Normalised forms of the name and every alias."""
        return {normalize_name(n) for n in [self.name, *self.aliases] if n.strip()}


@dataclass
class Cast:
    members: list[CastMember]  # lines descending, then name

    def resolve(self, name: str) -> CastMember | None:
        """The member with exactly this name or alias, else the one whose name or alias normalises
        to the same form as `name`."""
        exact = next((m for m in self.members if name in (m.name, *m.aliases)), None)
        if exact is not None:
            return exact
        key = normalize_name(name)
        return next((m for m in self.members if key in m.keys()), None)


def _conflict(a: str, b: str) -> bool:
    return a != "unknown" and b != "unknown" and a != b


def _similar(a: str, b: str) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return True
    return fuzz.token_set_ratio(a, b) >= FUZZY_MERGE_MIN_RATIO


def _majority(genders: list[str]) -> str:
    counts = Counter(g for g in genders if g in ("female", "male"))
    if not counts:
        return "unknown"
    (top, n), *rest = counts.most_common()
    return top if not rest or rest[0][1] < n else "unknown"


def build_cast(
    reports: list[NameReport],
    pronoun_genders: dict[str, str] | None = None,
    distinct: set[frozenset[str]] | None = None,
) -> Cast:
    """Merge name reports into a cast (SPEC §15.4).

    `pronoun_genders` (normalised name -> gender) is used when no LLM report gives a gender.
    `distinct` holds pairs of normalised names that must stay separate: the LLM declared one as a
    new character while the other was already in the list it was given."""
    pronoun_genders = pronoun_genders or {}
    distinct = distinct or set()
    reports = [r for r in reports if r.name.strip()]
    # Union-find over report indices, merging in report order, so the result is deterministic.
    parent = list(range(len(reports)))
    names = [{normalize_name(n) for n in (r.name, *r.aliases) if n.strip()} for r in reports]
    surfaces = [{" ".join(n.casefold().split()) for n in (r.name, *r.aliases) if n.strip()} for r in reports]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def members_of(root: int) -> list[int]:
        return [i for i in range(len(reports)) if find(i) == root]

    def keys(root: int) -> set[str]:
        return set().union(*(names[i] for i in members_of(root)))

    def surface_keys(root: int) -> set[str]:
        return set().union(*(surfaces[i] for i in members_of(root)))

    def group_gender(root: int) -> str:
        found = _majority([reports[i].gender or "unknown" for i in members_of(root)])
        if found != "unknown":
            return found
        return _majority([pronoun_genders.get(k, "unknown") for k in keys(root)])

    def separate(a: int, b: int) -> bool:
        return any(frozenset((x, y)) in distinct for x in keys(a) for y in keys(b))

    def merge_pass(match: Callable[[int, int], bool], checked: bool) -> None:
        changed = True
        while changed:
            changed = False
            roots = list(dict.fromkeys(find(i) for i in range(len(reports))))
            for k, a in enumerate(roots):
                for b in roots[k + 1 :]:
                    if match(a, b) and not (
                        checked and (_conflict(group_gender(a), group_gender(b)) or separate(a, b))
                    ):
                        parent[b] = a
                        changed = True
                        break
                if changed:
                    break

    # The same surface name or an LLM alias always merges; equal normalised names (Mr Smith, Mrs
    # Smith) and similar ones (subset or fuzzy) only when genders don't conflict and the LLM didn't
    # declare them distinct.
    merge_pass(lambda a, b: bool(surface_keys(a) & surface_keys(b)), checked=False)
    merge_pass(lambda a, b: any(_similar(p, q) for p in keys(a) for q in keys(b)), checked=True)

    members: list[CastMember] = []
    for root in dict.fromkeys(find(i) for i in range(len(reports))):
        group = [reports[i] for i in members_of(root)]
        surface: Counter[str] = Counter()
        for r in group:
            surface[r.name] += max(r.lines, 1)
        display = max(surface, key=lambda n: (surface[n], -list(surface).index(n)))
        aliases = list(dict.fromkeys(n for r in group for n in (r.name, *r.aliases) if n.strip() and n != display))
        lines = sum(r.lines for r in group)
        members.append(CastMember(display, group_gender(root), lines, aliases))
    members.sort(key=lambda m: (-m.lines, m.name.casefold()))
    return Cast(members)
