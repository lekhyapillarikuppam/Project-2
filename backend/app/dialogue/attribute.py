"""Speaker attribution (SPEC §15.3): who speaks each utterance.

Two methods behind one interface, both returning one `Attribution` per utterance:
- `baseline_attribute`: rules only (explicit tags, pronoun tags, turn-taking, else UNKNOWN);
- `llm_attribute`: one Groq request per window (`windows.py`), validated, retried once, and
  replaced by the baseline for that window on any failure (D34).
`combine` makes the three candidate methods of D39: `baseline`, `llm`, and
`llm_with_baseline_override` (an explicit tag found by the baseline wins over the LLM).
Every attribution records why it was made: `explicit`, `pronoun`, `turn`, `unknown`, or `llm`.
"""

import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.dialogue.cast import PRONOUN_WINDOW_CHARS, Cast, NameReport, gender_from_pronouns, normalize_name
from app.dialogue.llm import GroqClient, LLMError
from app.dialogue.segment import (
    SPEECH_VERBS,
    QuoteFamily,
    QuoteSpan,
    detect_quote_family,
    find_quote_spans,
    paragraph_text,
)
from app.dialogue.voices import VoiceCasting, cast_for_chapter
from app.dialogue.windows import Utterance, build_windows, collect_utterances, render_window
from app.models import Chapter, DocumentModel

logger = logging.getLogger(__name__)

UNKNOWN = "UNKNOWN"
Reason = Literal["explicit", "pronoun", "turn", "unknown", "llm"]
Method = Literal["baseline", "llm", "llm_with_baseline_override"]
METHODS: tuple[Method, ...] = ("baseline", "llm", "llm_with_baseline_override")

# Pronoun tags look for named characters in this many paragraphs (the current one included).
PRONOUN_LOOKBACK_PARAGRAPHS = 3
# Turn-taking: a paragraph belongs to a run of dialogue when it has a quote and at most this many
# words of narration outside its quotes (speech tags like `said the Hatter, tossing his head`).
TURN_MAX_NARRATION_WORDS = 20
# The running character list sent with each window is capped (SPEC §15.3).
ROSTER_PROMPT_LIMIT = 30

# --- baseline: tag patterns -----------------------------------------------------------------------

_VERB = "(?:" + "|".join(re.escape(v) for v in sorted(SPEECH_VERBS, key=len, reverse=True)) + r")\b"
_ADV = r"(?:\s+[a-z]+ly)?"
_CAP_WORD = r"[A-Z][\w’'-]*"
# Capitalised words that start sentences but are never names (`She said`, `But I said`).
_NOT_NAMES = r"(?:I|He|She|It|They|We|You|But|And|Now|Then|So|Yet|Or|When|There|This|That)\b"
_CAP_NAME = rf"(?:(?:Mr|Mrs|Dr|St)\.\s+)?(?!{_NOT_NAMES}){_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}}"
# After a verb: `the Hatter`, `the March Hare`, or a lowercase phrase (`the young woman`) that ends
# at punctuation, an `-ly` adverb or a preposition (`the girl shortly`, `the priest in some perplexity`).
_PHRASE_END = r"(?=\s*(?:[,.;:!?—–]|$)|\s+[a-z]+ly\b|\s+(?:with|in|as|at|to|from|for|and|but|who|without)\b)"
_THE_NAME_AFTER = rf"the\s+(?:{_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}}|[a-z][\w-]*(?:\s+[a-z][\w-]*){{0,3}}?{_PHRASE_END})"
# Before a verb: `the Hatter`, `the March Hare`, `the old man` (up to 4 words).
_THE_NAME_BEFORE = r"[Tt]he\s+[A-Za-z][\w’'-]*(?:\s+[A-Za-z][\w’'-]*){0,3}?"
_LEAD = r"^[\s,;:—–-]*(?:and\s+)?"
_TAG_AFTER_VERB_NAME = re.compile(rf"{_LEAD}{_VERB}\s+(?P<name>{_THE_NAME_AFTER}|{_CAP_NAME})")
_TAG_AFTER_NAME_VERB = re.compile(rf"{_LEAD}(?P<name>{_THE_NAME_BEFORE}|{_CAP_NAME}){_ADV}\s+{_VERB}")
_TAG_AFTER_PRONOUN = re.compile(
    rf"{_LEAD}(?:(?P<p1>he|she|they){_ADV}(?:\s+both|\s+all)?\s+{_VERB}|{_VERB}\s+(?P<p2>he|she|they)\b)"
)
_TAG_BEFORE_NAME_VERB = re.compile(
    rf"(?:^|[.!?]\s+|\s)(?P<name>{_THE_NAME_BEFORE}|{_CAP_NAME}){_ADV}\s+{_VERB}(?:\s+[\w’'-]+){{0,4}}?\s*[,:—]\s*$"
)
_TAG_BEFORE_VERB_NAME = re.compile(rf"{_VERB}\s+(?P<name>{_THE_NAME_AFTER}|{_CAP_NAME})\s*[,:—]\s*$")
_TAG_BEFORE_PRONOUN = re.compile(
    rf"(?:^|\W)(?P<p1>[Hh]e|[Ss]he|[Tt]hey){_ADV}\s+{_VERB}(?:\s+[\w’'-]+){{0,4}}?\s*[,:—]\s*$"
)


@dataclass(frozen=True)
class Attribution:
    utterance_id: str
    speaker: str  # a character name or UNKNOWN
    reason: Reason
    method: Literal["baseline", "llm"]


@dataclass(frozen=True)
class Tag:
    kind: Literal["explicit", "pronoun"]
    value: str  # the name (explicit) or `he` / `she` / `they` (group speech: stays UNKNOWN)
    paragraph: int
    end: int  # offset in the paragraph just after the tag (for gender counting)


def clean_name(name: str) -> str:
    """`the Hatter` -> `Hatter`, `the March Hare` -> `March Hare`; lowercase phrases keep `the`."""
    name = " ".join(name.split()).strip(" ,.;:-")
    words = name.split()
    if words[:1] and words[0].casefold() == "the" and len(words) > 1 and words[1][:1].isupper():
        return " ".join(words[1:])
    return name


def _narration_around(text: str, row: list[QuoteSpan], k: int) -> tuple[str, str]:
    """Narration before span k (since the previous span) and after it (to the next span)."""
    before = text[row[k - 1].end if k else 0 : row[k].start]
    after = text[row[k].end : row[k + 1].start if k + 1 < len(row) else len(text)]
    return before, after


def find_tag(paragraphs: list[str], spans: list[list[QuoteSpan]], utterance: Utterance) -> Tag | None:
    """The utterance's speech tag: right after any of its spans (`‘…’ said Alice`, `‘…’ Alice
    said`, `‘…’ she said`), else right before its first span (`The Hatter said, ‘…’`)."""
    for p, start, _ in utterance.parts:
        row = spans[p]
        k = next(i for i, q in enumerate(row) if q.start == start)
        _, after = _narration_around(paragraphs[p], row, k)
        offset = row[k].end
        for pattern in (_TAG_AFTER_VERB_NAME, _TAG_AFTER_NAME_VERB):
            m = pattern.match(after)
            if m:
                return Tag("explicit", clean_name(m.group("name")), p, offset + m.end())
        m = _TAG_AFTER_PRONOUN.match(after)
        if m:
            return Tag("pronoun", (m.group("p1") or m.group("p2")).casefold(), p, offset + m.end())
    p, start, _ = utterance.parts[0]
    row = spans[p]
    k = next(i for i, q in enumerate(row) if q.start == start)
    before, _ = _narration_around(paragraphs[p], row, k)
    for pattern in (_TAG_BEFORE_NAME_VERB, _TAG_BEFORE_VERB_NAME):
        m = pattern.search(before)
        if m:
            return Tag("explicit", clean_name(m.group("name")), p, start)
    m = _TAG_BEFORE_PRONOUN.search(before)
    if m:
        return Tag("pronoun", m.group("p1").casefold(), p, start)
    return None


def _mentions(text: str, name: str) -> list[int]:
    """Positions where `name` (a cleaned character name) occurs as whole words."""
    return [m.start() for m in re.finditer(rf"(?<![\w’'])({re.escape(name)})(?![\w’'])", text)]


def _narration_words(text: str, row: list[QuoteSpan]) -> int:
    edges = [0] + [x for q in row for x in (q.start, q.end)] + [len(text)]
    return sum(len(text[edges[i] : edges[i + 1]].split()) for i in range(0, len(edges), 2))


@dataclass
class BaselineResult:
    attributions: dict[str, Attribution]
    tags: dict[str, Tag]  # utterance id -> its tag
    genders: dict[str, str]  # normalised character name -> gender from pronouns near its tags

    def reports(self) -> list[NameReport]:
        """Line counts per surface name (UNKNOWN excluded), for the cast and the ranking."""
        counts = Counter(a.speaker for a in self.attributions.values() if a.speaker != UNKNOWN)
        return [NameReport(name, lines) for name, lines in counts.items()]


def baseline_attribute(paragraphs: list[str], spans: list[list[QuoteSpan]]) -> BaselineResult:
    """The rule-based method (SPEC §15.3): explicit tags, then pronoun tags, then turn-taking."""
    utterances = collect_utterances(spans)
    tags = {u.id: t for u in utterances if (t := find_tag(paragraphs, spans, u)) is not None}

    # Genders of explicitly named characters from the pronouns after their tags.
    near: dict[str, list[str]] = {}
    for t in tags.values():
        if t.kind == "explicit":
            near.setdefault(normalize_name(t.value), []).append(paragraphs[t.paragraph][t.end : t.end + PRONOUN_WINDOW_CHARS])
    genders = {name: gender_from_pronouns(texts) for name, texts in near.items()}
    named = list(dict.fromkeys(t.value for t in tags.values() if t.kind == "explicit"))

    result: dict[str, Attribution] = {}
    for u in utterances:
        tag = tags.get(u.id)
        if tag is None:
            continue
        if tag.kind == "explicit":
            result[u.id] = Attribution(u.id, tag.value, "explicit", "baseline")
            continue
        # Pronoun: the named characters of that gender mentioned in the last 3 paragraphs.
        p, start, _ = u.parts[0]
        texts = [paragraphs[q] for q in range(max(0, p - PRONOUN_LOOKBACK_PARAGRAPHS + 1), p)]
        texts.append(paragraphs[p][: max(start, tag.end)])
        window = "\n".join(texts)
        if tag.value not in ("he", "she"):
            continue
        want = "male" if tag.value == "he" else "female"
        candidates = {
            normalize_name(n): n for n in named if genders.get(normalize_name(n)) == want and _mentions(window, n)
        }
        if len(candidates) == 1:
            result[u.id] = Attribution(u.id, next(iter(candidates.values())), "pronoun", "baseline")

    # Turn-taking inside runs of dialogue paragraphs.
    by_paragraph: dict[int, list[Utterance]] = {}
    for u in utterances:
        by_paragraph.setdefault(u.first_paragraph, []).append(u)
    pair: tuple[str, str] | None = None  # the last two explicitly tagged speakers, A then B
    last: str | None = None  # the speaker of the previous paragraph in the run
    for p in range(len(paragraphs)):
        row = by_paragraph.get(p, [])
        if not row or _narration_words(paragraphs[p], spans[p]) > TURN_MAX_NARRATION_WORDS:
            pair, last = None, None
            continue
        known = [result[u.id] for u in row if u.id in result]
        if not known and any(u.id in tags for u in row):  # tagged, but unresolved: the turn order is lost
            last = None
            continue
        if known:
            for a in known:
                if a.reason == "explicit" and (pair is None or a.speaker != pair[1]):
                    pair = (pair[1], a.speaker) if pair else (a.speaker, a.speaker)
            last = known[-1].speaker
            continue
        if pair and pair[0] != pair[1] and last in pair:
            speaker = pair[0] if last == pair[1] else pair[1]
            for u in row:
                result[u.id] = Attribution(u.id, speaker, "turn", "baseline")
            last = speaker
        else:
            last = None

    for u in utterances:
        result.setdefault(u.id, Attribution(u.id, UNKNOWN, "unknown", "baseline"))
    ordered = {u.id: result[u.id] for u in utterances}
    return BaselineResult(ordered, tags, genders)


# --- LLM -------------------------------------------------------------------------------------------

SYSTEM_PROMPT = """You label who speaks each quotation in a passage of fiction.

The user message has CHARACTERS (known so far, with gender), then optional CONTEXT (earlier text,
read only), then TEXT. In TEXT every quotation starts with an id like [Q3]. One id can appear
before several pieces of one broken quotation; they have one speaker.

Rules:
- Give a speaker for every id, exactly once.
- Use a name from CHARACTERS when it is that character, spelled exactly as listed.
- A character not in CHARACTERS: use their name as the text gives it, and add them to "new".
- Quoted thoughts belong to the thinker.
- Speech by several characters at once ("they cried") is "UNKNOWN".
- If the text does not tell who speaks, use "UNKNOWN". Do not guess.

Reply with JSON only, no explanation:
{"q":{"Q1":"Alice","Q2":"UNKNOWN"},"new":[{"name":"Dormouse","gender":"male","aliases":[]}]}
"new" lists only characters that are not in CHARACTERS (gender: female, male or unknown)."""


@dataclass
class RosterEntry:
    name: str
    gender: str
    aliases: list[str] = field(default_factory=list)
    lines: int = 0


@dataclass
class Roster:
    """The running character list across a book's windows (names the LLM has used)."""

    entries: dict[str, RosterEntry] = field(default_factory=dict)  # normalised name -> entry
    # Pairs the LLM declared distinct: it added one as new while the other was in its list.
    distinct: set[frozenset[str]] = field(default_factory=set)

    def find(self, name: str) -> RosterEntry | None:
        key = normalize_name(name)
        if key in self.entries:
            return self.entries[key]
        return next((e for e in self.entries.values() if key in {normalize_name(a) for a in e.aliases}), None)

    def add(self, name: str, gender: str = "unknown", aliases: list[str] | None = None) -> RosterEntry:
        entry = self.find(name)
        if entry is None:
            entry = RosterEntry(name, gender if gender in ("female", "male") else "unknown", list(aliases or []))
            self.entries[normalize_name(name)] = entry
        else:
            if entry.gender == "unknown" and gender in ("female", "male"):
                entry.gender = gender
            entry.aliases += [a for a in aliases or [] if a not in entry.aliases and a != entry.name]
        return entry

    def top(self, limit: int = ROSTER_PROMPT_LIMIT) -> list[RosterEntry]:
        return sorted(self.entries.values(), key=lambda e: -e.lines)[:limit]

    def reports(self) -> list[NameReport]:
        return [NameReport(e.name, e.lines, e.gender, tuple(e.aliases)) for e in self.entries.values()]


def user_prompt(window_text: str, roster: Roster) -> str:
    listed = roster.top()
    characters = "\n".join(f"- {e.name} ({e.gender})" for e in listed) or "(none yet)"
    return f"CHARACTERS:\n{characters}\n\n{window_text}"


class ResponseError(ValueError):
    pass


def parse_response(content: str, ids: list[str]) -> tuple[dict[str, str], list[dict[str, object]]]:
    """Validate a window's reply: every id answered exactly once with a non-empty string."""
    duplicates: list[str] = []

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for k, v in items:
            if k in seen:
                duplicates.append(k)
            seen[k] = v
        return seen

    try:
        data = json.loads(content, object_pairs_hook=pairs)
    except ValueError as e:
        raise ResponseError(f"the reply is not valid JSON ({e})") from e
    if not isinstance(data, dict) or not isinstance(data.get("q"), dict):
        raise ResponseError('the reply must be an object with a "q" object')
    q: dict[str, object] = data["q"]
    problems: list[str] = []
    if dup := sorted(set(duplicates) & set(ids)):
        problems.append(f"ids answered more than once: {', '.join(dup)}")
    if missing := [i for i in ids if i not in q]:
        problems.append(f"missing ids: {', '.join(missing)}")
    if extra := [i for i in q if i not in ids]:
        problems.append(f"unknown ids: {', '.join(extra)}")
    if empty := [i for i in ids if i in q and not (isinstance(q[i], str) and q[i].strip())]:
        problems.append(f"speaker must be a non-empty string for: {', '.join(empty)}")
    if problems:
        raise ResponseError("; ".join(problems))
    new = data.get("new") or []
    new_list = [n for n in new if isinstance(n, dict) and isinstance(n.get("name"), str) and n["name"].strip()] if isinstance(new, list) else []
    return {i: str(q[i]).strip() for i in ids}, new_list


@dataclass
class ChapterAttribution:
    attributions: dict[str, Attribution]  # utterance id -> attribution, in text order
    windows_total: int = 0
    windows_llm: int = 0  # windows answered by the LLM (the rest used the baseline)

    @property
    def method_used(self) -> Literal["llm", "baseline", "mixed", "none"]:
        if not self.windows_total:
            return "none"
        if self.windows_llm == self.windows_total:
            return "llm"
        return "baseline" if self.windows_llm == 0 else "mixed"


async def _ask(client: GroqClient, window_text: str, roster: Roster, ids: list[str]) -> tuple[dict[str, str], list[dict[str, object]]]:
    """One request, validated; on a bad reply one retry with the error appended."""
    prompt = user_prompt(window_text, roster)
    content = await client.complete(SYSTEM_PROMPT, prompt, quotes=len(ids))
    try:
        return parse_response(content, ids)
    except ResponseError as e:
        logger.warning("Invalid attribution reply (%s); retrying once", e)
        retry = f"{prompt}\n\nYour previous reply was invalid: {e}. Reply again with JSON only."
        content = await client.complete(SYSTEM_PROMPT, retry, quotes=len(ids))
        return parse_response(content, ids)


async def llm_attribute(
    paragraphs: list[str],
    spans: list[list[QuoteSpan]],
    client: GroqClient,
    roster: Roster,
    baseline: BaselineResult,
    window_words: int,
    on_window: Callable[[], None] | None = None,
) -> ChapterAttribution:
    """One request per window; a window whose request or reply fails twice (or fails in any other
    way) uses the baseline (D34). New characters are added to `roster`, and their lines counted.
    `on_window` is called after each window (progress)."""
    utterances = collect_utterances(spans)
    windows = build_windows(paragraphs, utterances, window_words)
    result: dict[str, Attribution] = {}
    answered = 0
    for window in windows:
        text, labels = render_window(window, paragraphs, utterances)
        ids = list(labels)
        listed = {normalize_name(e.name) for e in roster.top()}
        try:
            speakers, new = await _ask(client, text, roster, ids)
        except (LLMError, ResponseError) as e:
            logger.warning("Window %d: using the baseline (%s)", window.index, e)
            speakers = None
        except Exception:  # noqa: BLE001 - the LLM must never fail the job (D34)
            logger.exception("Window %d: unexpected error; using the baseline", window.index)
            speakers = None
        if speakers is None:
            for uid in window.utterance_ids:
                result[uid] = baseline.attributions[uid]
            if on_window is not None:
                on_window()
            continue
        answered += 1
        for n in new:
            gender = n.get("gender") if isinstance(n.get("gender"), str) else "unknown"
            aliases = [a for a in n.get("aliases") or [] if isinstance(a, str) and a.strip()]  # type: ignore[union-attr]
            name = str(n["name"]).strip()
            key = normalize_name(name)
            if roster.find(name) is None:
                roster.distinct |= {frozenset((key, other)) for other in listed if other != key}
            roster.add(name, str(gender), aliases)
        for label, uid in labels.items():
            speaker = speakers[label]
            if speaker.casefold() == UNKNOWN.casefold():
                result[uid] = Attribution(uid, UNKNOWN, "llm", "llm")
                continue
            entry = roster.find(speaker) or roster.add(speaker)
            entry.lines += 1
            result[uid] = Attribution(uid, entry.name, "llm", "llm")
        if on_window is not None:
            on_window()
    ordered = {u.id: result.get(u.id, baseline.attributions[u.id]) for u in utterances}
    return ChapterAttribution(ordered, len(windows), answered)


def combine(method: Method, baseline: BaselineResult, llm: ChapterAttribution | None) -> ChapterAttribution:
    """The D39 candidate methods. `llm_with_baseline_override`: an explicit tag wins."""
    if method == "baseline" or llm is None:
        return ChapterAttribution(dict(baseline.attributions))
    if method == "llm":
        return llm
    merged = {
        uid: baseline.attributions[uid] if baseline.attributions[uid].reason == "explicit" else a
        for uid, a in llm.attributions.items()
    }
    return ChapterAttribution(merged, llm.windows_total, llm.windows_llm)


async def attribute_chapter(
    paragraphs: list[str],
    spans: list[list[QuoteSpan]],
    method: Method,
    client: GroqClient | None,
    roster: Roster,
    window_words: int,
    on_window: Callable[[], None] | None = None,
) -> tuple[ChapterAttribution, BaselineResult]:
    """Attribute one chapter with `method` (the baseline is always computed: fallback and override)."""
    baseline = baseline_attribute(paragraphs, spans)
    if method == "baseline" or client is None:
        return combine("baseline", baseline, None), baseline
    llm = await llm_attribute(paragraphs, spans, client, roster, baseline, window_words, on_window)
    return combine(method, baseline, llm), baseline


# --- whole book ------------------------------------------------------------------------------------


def chapter_texts(chapter: Chapter) -> list[str]:
    return [paragraph_text(p) for p in chapter.paragraphs]


def rank_characters(document: DocumentModel) -> tuple[list[NameReport], dict[str, str]]:
    """The fast whole-book pass (SPEC §15.5, D41): explicitly tagged lines per character from the
    baseline, no LLM; used to reserve voices. Returns the name reports (most lines first) and the
    pronoun genders by normalised name."""
    return rank_texts([chapter_texts(c) for c in document.chapters], detect_quote_family(document))


def rank_texts(chapters: list[list[str]], family: QuoteFamily | None) -> tuple[list[NameReport], dict[str, str]]:
    """`rank_characters` over chapters given as paragraph texts."""
    counts: Counter[str] = Counter()
    genders: dict[str, list[str]] = {}
    for texts in chapters:
        spans, _ = find_quote_spans(texts, family)
        result = baseline_attribute(texts, spans)
        for a in result.attributions.values():
            if a.reason == "explicit":
                counts[a.speaker] += 1
        for name, g in result.genders.items():
            genders.setdefault(name, []).append(g)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))

    def vote(gs: list[str]) -> str:
        known = Counter(g for g in gs if g != "unknown")
        top = known.most_common(2)
        return top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else "unknown"

    return [NameReport(name, lines) for name, lines in ranked], {n: vote(gs) for n, gs in genders.items()}


class CharacterBook:
    """Characters mode for one book (SPEC §15.3–15.5, D37, D41): the baseline book pass reserves
    voices; each chapter is then attributed (`attribute`) and cast (`cast_chapter`) lazily, just
    before it is narrated. One instance per job; not thread-safe (one event loop)."""

    def __init__(
        self,
        chapters: list[list[str]],
        family: QuoteFamily | None,
        method: Method,
        client: GroqClient | None,
        window_words: int,
        casting: VoiceCasting,
    ) -> None:
        self.family = family
        self.method = method
        self.client = client
        self.window_words = window_words
        self.casting = casting
        ranking, self.genders = rank_texts(chapters, family)
        casting.reserve(ranking)
        self.roster = Roster()
        self.baseline_lines: Counter[str] = Counter()  # names the baseline gave (fallback windows)
        self.cast: Cast = Cast([])

    @classmethod
    def for_document(
        cls, document: DocumentModel, method: Method, client: GroqClient | None, window_words: int,
        casting: VoiceCasting,
    ) -> "CharacterBook":  # fmt: skip
        return cls([chapter_texts(c) for c in document.chapters], detect_quote_family(document), method, client,
                   window_words, casting)  # fmt: skip

    def spans(self, texts: list[str]) -> list[list[QuoteSpan]]:
        return find_quote_spans(texts, self.family)[0]

    def windows(self, texts: list[str]) -> int:
        """LLM windows (requests) this chapter needs; 0 for the baseline method."""
        if self.method == "baseline" or self.client is None:
            return 0
        return len(build_windows(texts, collect_utterances(self.spans(texts)), self.window_words))

    async def attribute(self, texts: list[str], on_window: Callable[[], None] | None = None) -> ChapterAttribution:
        attribution, _ = await attribute_chapter(
            texts, self.spans(texts), self.method, self.client, self.roster, self.window_words, on_window
        )
        return attribution

    def cast_chapter(self, attribution: ChapterAttribution) -> tuple[dict[str, str], dict[str, str]]:
        """Voices for this chapter's speakers (D41). Returns utterance id -> cast name (UNKNOWN
        left out) and cast name -> voice id; `self.cast` becomes the book's cast so far."""
        for a in attribution.attributions.values():
            if a.method == "baseline" and a.speaker != UNKNOWN:
                self.baseline_lines[a.speaker] += 1
        found = self.roster.reports() + [NameReport(n, lines) for n, lines in self.baseline_lines.items()]
        speakers = [a.speaker for a in attribution.attributions.values() if a.speaker != UNKNOWN]
        self.cast = cast_for_chapter(self.casting, found, self.genders, self.roster.distinct, speakers)
        by_utterance: dict[str, str] = {}
        voices: dict[str, str] = {}
        for uid, a in attribution.attributions.items():
            member = self.cast.resolve(a.speaker) if a.speaker != UNKNOWN else None
            if member is None:
                continue
            voice = self.casting.voice_for(member)
            if voice != self.casting.narrator:
                by_utterance[uid] = member.name
                voices[member.name] = voice.id
        return by_utterance, voices


def apply_speakers(chapter: Chapter, speakers: dict[str, str]) -> Chapter:
    """Set each quote segment's speaker from `speakers` (utterance id -> cast name); UNKNOWN and
    unlisted utterances are read by the narrator (D33). `chapter` must be segmented already."""
    paragraphs = []
    for p in chapter.paragraphs:
        segments = [
            s.model_copy(update={"speaker": speakers.get(s.utterance_id or "", UNKNOWN)})
            if s.kind == "quote" and speakers.get(s.utterance_id or "", UNKNOWN) != UNKNOWN
            else s
            for s in p.segments
        ]
        paragraphs.append(p.model_copy(update={"segments": segments}))
    return chapter.model_copy(update={"paragraphs": paragraphs})
