"""Dialogue segmentation (SPEC §15.2): split paragraphs into narration and quote segments.

Pure functions only. The rules, in the order SPEC §15.2 lists them:
1. the document's quote family (double or single, curly and straight alike) by marks per 1,000 words;
   opener vs closer is decided by position, never by glyph;
2. apostrophes (single family only) are never boundaries;
3. a quote left open at paragraph end continues into a next paragraph that starts with an opener,
   unless that paragraph has an explicit speech tag (then the first quote is closed: damaged PDF);
4. broken quotes around a speech tag share one utterance id;
5. spans include their marks; marks of the other family are ordinary text;
6. an unclosed quote without continuation is closed at paragraph end (counted).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from app.models import Chapter, DocumentModel, Paragraph, Segment

QuoteFamily = Literal["double", "single"]

DOUBLE_MARKS = frozenset("“”\"")
SINGLE_MARKS = frozenset("‘’'")
FAMILY_MARKS: dict[QuoteFamily, frozenset[str]] = {"double": DOUBLE_MARKS, "single": SINGLE_MARKS}

# Rule 1: below this many dialogue marks per 1,000 words the document has no dialogue.
MIN_MARKS_PER_1000_WORDS = 2.0

# Rule 1: besides whitespace, an opener may follow these.
OPENER_PRECEDERS = frozenset("([—–")

# Rule 2 (b): words that start with an apostrophe ('tis, 'em). Compared casefolded.
LEADING_ELISIONS = frozenset({"tis", "twas", "em", "cause", "til", "twere", "ere", "ow", "e"})
# Rule 2 (d): words that end with an apostrophe (talkin', o'). Compared casefolded.
TRAILING_ELISIONS = frozenset({"o", "an", "talkin", "nothin", "somethin", "goin", "ol"})
# Rule 2 (d): "any word ending in'" — only applied when the mark is followed by a space and a
# lowercase word that is not a speech verb, so that `‘Try again’ said Alice` still closes.
TRAILING_ELISION_SUFFIX = "in"
TRAILING_ELISION_MIN_LETTERS = 4

# Speech-tag verbs (shared with the rule-based attribution in §15.3).
SPEECH_VERBS = (
    "said", "says", "say", "asked", "asks", "cried", "replied", "shouted", "whispered", "answered",
    "exclaimed", "added", "went on", "continued", "remarked", "muttered", "began", "called",
    "thought", "repeated", "interrupted", "observed", "explained", "growled", "murmured",
    "returned", "retorted", "roared", "screamed", "sighed", "put in", "broke in", "inquired",
    "demanded", "insisted", "admitted", "agreed", "protested", "persisted", "pleaded", "urged",
    "suggested", "declared", "groaned", "panted", "snapped", "sobbed", "stammered", "yelled",
    "grumbled", "ventured",  # Phase 6.2 gate: alice_ch07 ¶22 `the Hatter grumbled:`, ¶55 `Alice ventured to ask`
)  # fmt: skip
_VERB = "(?:" + "|".join(re.escape(v) for v in sorted(SPEECH_VERBS, key=len, reverse=True)) + r")\b"
_PRONOUN = r"(?:he|she|they|it|i|we|you)\b"
_NAME = r"(?:(?:the\s+)?[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*){0,3}|the\s+[a-z][\w-]*(?:\s+[a-z][\w-]*){0,2})"

# Rule 3 exception: a tag that names the speaker (`said X`, `X said`; pronouns don't count).
_EXPLICIT_TAG = re.compile(
    rf"\b{_VERB}\s+(?!{_PRONOUN}){_NAME}|(?<![\w’'])(?!(?i:{_PRONOUN})){_NAME}\s+{_VERB}"
)
# Rule 4: narration right after a quote that starts with a tag (`said Alice`, `she said`, `the
# March Hare said`): that quote has a speech tag of its own.
_TAG_AFTER = re.compile(rf"^[\s,;:—–-]*(?:\w[\w’'-]*\s+){{0,4}}?{_VERB}", re.IGNORECASE)
# Rule 4: a sentence that introduces the next quote (`The Hatter said, ‘…’`).
_INTRO_TAG = re.compile(rf"{_VERB}[^.!?]*[,:—]\s*$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?][’”'\")\]]*\s+")
# Damaged closing mark inside a paragraph (extends the rule 3 exception): inside an open quote,
# punctuation followed by a speech tag (`again! said Alice`, `don’t! the Hatter said`,
# `curious! she thought`) ends the quote there. Case-sensitive: a lowercase pronoun or `the`
# after `!` can only be a tag, a new sentence inside the quote would be capitalised.
DAMAGED_CLOSE_PUNCTUATION = frozenset("!?,.;")
# An `-ly` adverb may stand between subject and verb, and `and` may lead a lowercase tag
# (`sh! and the Dormouse sulkily remarked`).
_CAP = r"[A-Z][\w’'-]*"
_ADV = r"(?:\s+[a-z]+ly)?"
_DAMAGED_CLOSE = re.compile(
    rf"\s+(?:{_VERB}\s+(?:the\s+)?{_CAP}|(?!The\b){_CAP}(?:\s+{_CAP})*{_ADV}\s+{_VERB}"
    rf"|(?:and\s+)?(?:the\s+{_CAP}(?:\s+{_CAP})*|he|she|they|I|we|you){_ADV}\s+{_VERB}|{_VERB}\s+(?:he|she|they)\b)"
)


@dataclass
class SegmentStats:
    marks_seen: int = 0  # marks of the dialogue family
    apostrophes_skipped: int = 0
    stray_closers: int = 0  # closer-only marks outside a quote (ignored)
    reopened: int = 0  # opener-only mark inside an open quote: the open one is closed before it
    unclosed_closed: int = 0  # rule 6
    continuations: int = 0  # rule 3
    speech_tag_closures: int = 0  # rule 3 exception
    damaged_tag_closures: int = 0  # quote closed before a speech tag inside a paragraph

    def add(self, other: "SegmentStats") -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass(frozen=True)
class QuoteSpan:
    start: int  # at the opening mark
    end: int  # after the closing mark (exclusive)
    utterance_id: str


@dataclass
class ParagraphScan:
    spans: list[tuple[int, int]] = field(default_factory=list)
    ends_open: bool = False
    stats: SegmentStats = field(default_factory=SegmentStats)


# --- rule 2: apostrophes -------------------------------------------------------------------------


def _word_before(text: str, i: int) -> str:
    j = i
    while j > 0 and text[j - 1].isalpha():
        j -= 1
    return text[j:i]


def _word_after(text: str, i: int) -> str:
    j = i + 1
    while j < len(text) and text[j].isalpha():
        j += 1
    return text[i + 1 : j]


def is_between_letters(text: str, i: int) -> bool:
    """Rule 2 (a): don't, Alice's, doesn‘t."""
    return 0 < i < len(text) - 1 and text[i - 1].isalpha() and text[i + 1].isalpha()


def is_leading_elision(text: str, i: int) -> bool:
    """Rule 2 (b): 'tis, 'em — the mark starts a word from LEADING_ELISIONS."""
    if i > 0 and text[i - 1].isalpha():
        return False
    word = _word_after(text, i)
    return bool(word) and word.casefold() in LEADING_ELISIONS


def is_plural_possessive(text: str, i: int, quote_open: bool) -> bool:
    """Rule 2 (c): `the Queens' ` — after `s` at a word end, only while no quote is open."""
    return (
        not quote_open
        and i > 0
        and text[i - 1] in "sS"
        and (i + 1 == len(text) or not text[i + 1].isalpha())
    )


def is_trailing_elision(text: str, i: int) -> bool:
    """Rule 2 (d): talkin', o' — the mark ends a word from TRAILING_ELISIONS, or a word ending
    in `in` followed by a lowercase word that is not a speech verb (`goin' home`)."""
    if i + 1 < len(text) and text[i + 1].isalpha():
        return False
    word = _word_before(text, i).casefold()
    if not word:
        return False
    if word in TRAILING_ELISIONS:
        return True
    if len(word) < TRAILING_ELISION_MIN_LETTERS or not word.endswith(TRAILING_ELISION_SUFFIX):
        return False
    rest = text[i + 1 :]
    if not rest[:1].isspace():
        return False
    nxt = rest.lstrip()
    return nxt[:1].islower() and not re.match(_VERB, nxt)


def is_apostrophe(text: str, i: int, quote_open: bool) -> bool:
    """Rule 2: a single-family mark that is never a quote boundary."""
    return (
        is_between_letters(text, i)
        or is_leading_elision(text, i)
        or is_plural_possessive(text, i, quote_open)
        or is_trailing_elision(text, i)
    )


# --- rule 1: family and opener/closer by position ------------------------------------------------


def _is_punctuation(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def is_opener_position(text: str, i: int) -> bool:
    """At paragraph start or after whitespace / ( [ — –, and followed by a non-space."""
    before = i == 0 or text[i - 1].isspace() or text[i - 1] in OPENER_PRECEDERS
    return before and i + 1 < len(text) and not text[i + 1].isspace()


def is_closer_position(text: str, i: int) -> bool:
    """After a non-space, and followed by whitespace, punctuation or paragraph end."""
    if i == 0 or text[i - 1].isspace():
        return False
    return i + 1 == len(text) or text[i + 1].isspace() or _is_punctuation(text[i + 1])


def mark_role(text: str, i: int, inside: bool) -> Literal["open", "close"]:
    """Opener or closer by position; a mark that is both or neither follows the state."""
    opener, closer = is_opener_position(text, i), is_closer_position(text, i)
    if opener != closer:
        return "open" if opener else "close"
    return "close" if inside else "open"


def dialogue_mark_count(text: str, family: QuoteFamily) -> int:
    """Marks of the family that are not apostrophes (single family; no quote assumed open)."""
    marks = FAMILY_MARKS[family]
    return sum(
        1
        for i, ch in enumerate(text)
        if ch in marks and not (family == "single" and is_apostrophe(text, i, quote_open=False))
    )


def quote_family_of_texts(texts: list[str]) -> QuoteFamily | None:
    """Rule 1: the family with more dialogue marks per 1,000 words, if at least the minimum."""
    words = sum(len(t.split()) for t in texts)
    if words == 0:
        return None
    rates = {f: 1000 * sum(dialogue_mark_count(t, f) for t in texts) / words for f in FAMILY_MARKS}
    family: QuoteFamily = "double" if rates["double"] >= rates["single"] else "single"
    return family if rates[family] >= MIN_MARKS_PER_1000_WORDS else None


def paragraph_text(paragraph: Paragraph) -> str:
    return " ".join(s.text for s in paragraph.segments)


def detect_quote_family(document: DocumentModel) -> QuoteFamily | None:
    return quote_family_of_texts([paragraph_text(p) for c in document.chapters for p in c.paragraphs])


# --- scanning one paragraph ----------------------------------------------------------------------


def _end_before(text: str, i: int) -> int:
    """End offset of the text before position i, without trailing whitespace."""
    return len(text[:i].rstrip())


def scan_paragraph(text: str, family: QuoteFamily) -> ParagraphScan:
    """Quote spans of one paragraph (rules 1, 2, 5, 6). Every paragraph starts outside a quote."""
    marks = FAMILY_MARKS[family]
    scan = ParagraphScan()
    stats = scan.stats
    start: int | None = None
    for i, ch in enumerate(text):
        if start is not None and ch in DAMAGED_CLOSE_PUNCTUATION and _DAMAGED_CLOSE.match(text, i + 1):
            scan.spans.append((start, i + 1))
            stats.damaged_tag_closures += 1
            start = None
            continue
        if ch not in marks:
            continue
        stats.marks_seen += 1
        if family == "single" and is_apostrophe(text, i, quote_open=start is not None):
            stats.apostrophes_skipped += 1
            continue
        role = mark_role(text, i, inside=start is not None)
        if start is None:
            if role == "open":
                start = i
            else:
                stats.stray_closers += 1
        elif role == "close":
            scan.spans.append((start, i + 1))
            start = None
        else:  # an opener-only mark while a quote is open: the open quote lost its closing mark
            end = _end_before(text, i)
            if end > start + 1:
                scan.spans.append((start, end))
            stats.reopened += 1
            start = i
    if start is not None:
        end = _end_before(text, len(text))
        if end > start + 1:
            scan.spans.append((start, end))
            scan.ends_open = True
    return scan


def narration_parts(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """The text between spans: before the first, between each pair, after the last."""
    edges = [0] + [x for s, e in spans for x in (s, e)] + [len(text)]
    return [text[edges[k] : edges[k + 1]] for k in range(0, len(edges), 2)]


def has_explicit_speech_tag(narration: str) -> bool:
    """Rule 3 exception: narration names a speaker with a speech verb (`said X`, `X said`)."""
    return bool(_EXPLICIT_TAG.search(narration))


def starts_with_tag(narration: str) -> bool:
    """Narration right after a quote begins with a speech tag (`said Alice`, `she remarked`)."""
    return bool(_TAG_AFTER.match(narration))


def joins_previous(between: str, after: str) -> bool:
    """Rule 4: does a quote continue the previous quote of the same paragraph (one utterance)?

    Not when the quote has its own tag right after it (`‘A,’ said Alice. ‘B,’ said the Hare.`), or
    when the narration in between ends with a new sentence that introduces it
    (`‘A.’ Alice nodded. The Hatter said, ‘B.’`). Otherwise yes: `‘Well,’ said Alice, ‘I suppose so.’`.
    """
    if starts_with_tag(after):
        return False
    sentences = _SENTENCE_END.split(between.strip())
    return not (len(sentences) > 1 and _INTRO_TAG.search(sentences[-1]))


def starts_with_opener(text: str, scan: ParagraphScan) -> bool:
    first = len(text) - len(text.lstrip())
    return bool(scan.spans) and scan.spans[0][0] == first


# --- the chapter ---------------------------------------------------------------------------------


def find_quote_spans(
    paragraphs: list[str], family: QuoteFamily | None
) -> tuple[list[list[QuoteSpan]], SegmentStats]:
    """Quote spans per paragraph with utterance ids `U1…` (unique within the paragraphs given)."""
    stats = SegmentStats()
    if family is None:
        return [[] for _ in paragraphs], stats
    scans = [scan_paragraph(t, family) for t in paragraphs]
    for scan in scans:
        stats.add(scan.stats)

    # rule 3 / rule 6: which paragraphs continue the open quote of the previous one
    continues = [False] * len(paragraphs)
    for k, scan in enumerate(scans):
        if not scan.ends_open:
            continue
        nxt = k + 1
        if nxt < len(paragraphs) and starts_with_opener(paragraphs[nxt], scans[nxt]):
            narration = " ".join(narration_parts(paragraphs[nxt], scans[nxt].spans))
            if has_explicit_speech_tag(narration):
                stats.speech_tag_closures += 1
            else:
                continues[nxt] = True
                stats.continuations += 1
        else:
            stats.unclosed_closed += 1

    result: list[list[QuoteSpan]] = []
    count = 0
    current = ""
    for k, (text, scan) in enumerate(zip(paragraphs, scans, strict=True)):
        parts = narration_parts(text, scan.spans)
        spans: list[QuoteSpan] = []
        for j, (s, e) in enumerate(scan.spans):
            if j == 0:
                same = continues[k] and count > 0
            else:
                same = joins_previous(parts[j], parts[j + 1])
            if not same:
                count += 1
                current = f"U{count}"
            spans.append(QuoteSpan(s, e, current))
        result.append(spans)
    return result, stats


def split_paragraph(paragraph: Paragraph, text: str, spans: list[QuoteSpan]) -> Paragraph:
    """Alternating narration / quote segments; whitespace-only narration is dropped."""
    if not spans:
        return paragraph
    segments: list[Segment] = []
    pos = 0
    for span in spans:
        before = text[pos : span.start].strip()
        if before:
            segments.append(Segment(text=before))
        segments.append(Segment(text=text[span.start : span.end], kind="quote", utterance_id=span.utterance_id))
        pos = span.end
    after = text[pos:].strip()
    if after:
        segments.append(Segment(text=after))
    return paragraph.model_copy(update={"segments": segments})


def segment_chapter(chapter: Chapter, family: QuoteFamily | None) -> tuple[Chapter, SegmentStats]:
    """The chapter with its paragraphs split into narration and quote segments (SPEC §15.2).

    `family` comes from detect_quote_family on the whole document; None leaves the chapter as is.
    Quote speakers stay "narrator" until attribution (D33)."""
    texts = [paragraph_text(p) for p in chapter.paragraphs]
    spans, stats = find_quote_spans(texts, family)
    paragraphs = [split_paragraph(p, t, s) for p, t, s in zip(chapter.paragraphs, texts, spans, strict=True)]
    return chapter.model_copy(update={"paragraphs": paragraphs}), stats
