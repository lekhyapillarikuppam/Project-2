"""Cleaning (SPEC §6.3): headers/footers, captions, footnotes, equations, paragraph merging, characters.

Every function here is pure: it takes lines and returns new lines.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from app.models import TextLine
from app.pipeline.titles import ROMAN

# --- Running headers / footers (§6.3.1) ---
HEADER_ZONE_RATIO = 0.08  # top / bottom ~8% of the page height
REPEAT_MIN_PAGES = 3  # a header must repeat on at least max(3, 30% of pages) pages ...
REPEAT_PAGE_RATIO = 0.30  # ... (SPEC §6.3.1)
# Running-header *style* rule (extension, see report): a zone font+size that differs from the body
# style and is not larger than the body size, used in that zone on at least the same number of pages,
# marks running headers whose text changes (e.g. the current chapter title on odd pages).
HEADER_STYLE_MAX_SIZE_RATIO = 1.0
# Short documents (SPEC §6.3.6): with exactly this many pages, zone text that appears on every page
# is a running header/footer (the max(3, 30%) rule can never be met).
SHORT_DOC_PAGES = 2

# --- Footnotes (§6.3.5) ---
FOOTNOTE_REGION_RATIO = 0.25  # footnotes are looked for in the bottom quarter of the page
FOOTNOTE_SIZE_RATIO = 0.9  # "noticeably smaller": at most 90% of the body font size
FOOTNOTE_START = re.compile(r"^[0-9*†‡§¶]")

# --- Equations (§6.3.5) ---
MATH_SYMBOL_RATIO = 0.5  # math symbols + Greek letters >= 50% of the Latin letters -> equation
MATH_SYMBOLS = set("=+−×÷±∓≤≥≠≈≡∼∝∑∏∫∮√∞∂∇∈∉∋⊂⊃⊆⊇∪∩∧∨¬→←↔⇒⇐⇔↦∀∃∅⟨⟩‖⊥∥∘⊗⊕^_|<>")

# --- Captions (§6.3.5) ---
# "Figure N" / "Fig. N" / "Table N" followed by a caption separator. Without the separator the
# paragraph is ordinary prose ("Figure 4.2 shows ...") and is kept.
CAPTION = re.compile(
    r"^(?:figure|fig\.|table)\s+\d+(?:\.\d+)*\s*(?:[:—–-]|\.(?!\d))", re.IGNORECASE
)
CAPTION_SMALL_SIZE_RATIO = 0.95  # a "Figure N ..." paragraph set smaller than body is also a caption
CAPTION_START = re.compile(r"^(?:figure|fig\.|table)\s+\d+", re.IGNORECASE)

# --- Paragraph merging (§6.3.2-3) ---
SAME_SIZE_TOLERANCE = 0.5  # font sizes within 0.5 pt count as the same size
PARAGRAPH_GAP_RATIO = 0.8  # vertical gap > 80% of the line height -> paragraph break
INDENT_TOLERANCE = 3.0  # a line starting > 3 pt right of the block's left edge is indented
SHORT_LINE_RATIO = 0.15  # a line ending > 15% of the column width before its right edge is "short"
TERMINAL_PUNCTUATION = tuple(".!?:;\"'’”)")
HYPHENS = ("-", "\u00ad", "\u2010")

PAGE_NUMBER = re.compile(
    rf"^(?:page\s+)?(?:\d+|{ROMAN})(?:\s+of\s+\d+)?$",
    re.IGNORECASE,
)
_DASHES_AROUND = re.compile(r"^[\s\-–—]+|[\s\-–—]+$")

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
CHAR_MAP = {
    **LIGATURES,
    "\u00a0": " ",  # no-break space
    "\u2002": " ",
    "\u2003": " ",
    "\u2009": " ",  # thin space
    "\u202f": " ",
    "\u00ad": "",  # soft hyphen
    "\u200b": "",  # zero-width space
    "\ufeff": "",
    "\ufffd": "",  # replacement character (glyph without a Unicode mapping)
    "‚": "‘",  # low single quote
    "‛": "‘",
    "„": "“",  # low double quote
    "‟": "“",
    "`": "‘",
    # TeX OT1 ligature artifacts: "!`" and "?`" render as inverted marks. English-only (D3), so
    # inverted marks are never meant literally.
    "¡": "!",
    "¿": "?",
}
_TRANSLATE = str.maketrans(CHAR_MAP)
_SPACES = re.compile(r"[ \t\r\n\f\v]+")


def normalize_chars(text: str) -> str:
    """Ligatures, odd quote forms, soft hyphens, special spaces, repeated whitespace (§6.3.4)."""
    text = unicodedata.normalize("NFC", text).translate(_TRANSLATE)
    return _SPACES.sub(" ", text).strip()


def body_font_size(lines: list[TextLine]) -> float:
    """Most common font size weighted by character count."""
    counts: Counter[float] = Counter()
    for line in lines:
        counts[round(line.font_size, 1)] += len(line.text)
    return counts.most_common(1)[0][0] if counts else 0.0


def _body_style(lines: list[TextLine]) -> tuple[str | None, float]:
    counts: Counter[tuple[str | None, float]] = Counter()
    for line in lines:
        counts[(line.font, round(line.font_size, 1))] += len(line.text)
    return counts.most_common(1)[0][0] if counts else (None, 0.0)


def is_page_number_text(text: str) -> bool:
    """Only a number, a roman numeral, "Page N" or "N of M" (dashes around it allowed)."""
    t = _DASHES_AROUND.sub("", text.strip())
    return bool(t) and bool(PAGE_NUMBER.match(t))


_DIGITS = re.compile(r"\d+")
_ROMAN_WORD = re.compile(rf"\b{ROMAN}\b", re.IGNORECASE)


def header_key(text: str) -> str:
    """Text used to recognise a repeating header: lowercased, digits and roman numerals removed."""
    t = _DIGITS.sub(" ", text.lower())
    t = _ROMAN_WORD.sub(" ", t)
    return _SPACES.sub(" ", t).strip(" .,:;-–—|")


def _zone(line: TextLine, page_height: float) -> str | None:
    if line.bbox is None or page_height <= 0:
        return None
    if line.bbox[1] < HEADER_ZONE_RATIO * page_height:
        return "top"
    if line.bbox[3] > (1 - HEADER_ZONE_RATIO) * page_height:
        return "bottom"
    return None


def repeat_threshold(page_count: int) -> float:
    """Pages a header style must repeat on: max(3, 30% of pages)."""
    return max(REPEAT_MIN_PAGES, REPEAT_PAGE_RATIO * page_count)


def text_repeat_threshold(page_count: int) -> float:
    """Pages a header text must repeat on: every page for 2-page documents, else max(3, 30%)."""
    if page_count == SHORT_DOC_PAGES:
        return page_count
    return repeat_threshold(page_count)


def remove_running_headers_footers(
    lines: list[TextLine], page_count: int, page_height_by_page: dict[int, float]
) -> list[TextLine]:
    """Drop running headers/footers and page numbers in the top/bottom zones (§6.3.1)."""
    threshold = repeat_threshold(page_count)
    text_threshold = text_repeat_threshold(page_count)
    body_font, body_size = _body_style(lines)
    text_pages: dict[tuple[str, str], set[int]] = defaultdict(set)
    style_pages: dict[tuple[str, str | None, float], set[int]] = defaultdict(set)
    zones: list[str | None] = []
    for line in lines:
        zone = _zone(line, page_height_by_page.get(line.page, 0.0)) if line.page is not None else None
        zones.append(zone)
        if zone is None:
            continue
        key = header_key(line.text)
        if key:
            text_pages[(zone, key)].add(line.page)
        style_pages[(zone, line.font, round(line.font_size, 1))].add(line.page)

    header_styles = {
        style
        for style, pages in style_pages.items()
        if len(pages) >= threshold
        and (style[1], style[2]) != (body_font, body_size)
        and style[2] <= HEADER_STYLE_MAX_SIZE_RATIO * body_size + SAME_SIZE_TOLERANCE
    }
    kept = []
    for line, zone in zip(lines, zones):
        if zone is not None:
            if is_page_number_text(line.text):
                continue
            key = header_key(line.text)
            if key and len(text_pages[(zone, key)]) >= text_threshold:
                continue
            if (zone, line.font, round(line.font_size, 1)) in header_styles:
                continue
        kept.append(line)
    return kept


def _letters(text: str) -> tuple[int, int]:
    """(Latin letters, Greek letters) in text."""
    latin = greek = 0
    for c in text:
        if "Ͱ" <= c <= "Ͽ":
            greek += 1
        elif c.isalpha():
            latin += 1
    return latin, greek


def is_equation_line(line: TextLine) -> bool:
    """Math-font line, high math-symbol ratio, or no letters at all (numbers/symbol residue)."""
    if line.is_math_font:
        return True
    latin, greek = _letters(line.text)
    if latin + greek == 0:
        return True
    symbols = sum(1 for c in line.text if c in MATH_SYMBOLS) + greek
    return symbols >= MATH_SYMBOL_RATIO * max(latin, 1)


def is_caption_start(line: TextLine, body_size: float) -> bool:
    if CAPTION.match(line.text):
        return True
    return bool(CAPTION_START.match(line.text)) and line.font_size <= CAPTION_SMALL_SIZE_RATIO * body_size


def drop_captions_footnotes_equations(
    lines: list[TextLine],
    body_size: float,
    page_height_by_page: dict[int, float] | None = None,
) -> list[TextLine]:
    """Drop captions (whole block), footnotes (PDF, bottom region, small font) and equations (§6.3.5)."""
    heights = page_height_by_page or {}
    caption_blocks: set[tuple[int | None, int | None]] = set()
    seen_blocks: set[tuple[int | None, int | None]] = set()
    kept = []
    in_footnote_page: int | None = None
    for line in lines:
        block = (line.page, line.block_id)
        if block not in seen_blocks:
            seen_blocks.add(block)
            if is_caption_start(line, body_size):
                caption_blocks.add(block)
        if block in caption_blocks:
            continue

        height = heights.get(line.page, 0.0) if line.page is not None else 0.0
        in_region = (
            line.bbox is not None
            and height > 0
            and line.bbox[1] > (1 - FOOTNOTE_REGION_RATIO) * height
        )
        small = line.font_size <= FOOTNOTE_SIZE_RATIO * body_size
        if in_region and small and (FOOTNOTE_START.match(line.text) or in_footnote_page == line.page):
            in_footnote_page = line.page
            continue
        in_footnote_page = None

        if is_equation_line(line):
            continue
        kept.append(line)
    return kept


def _same_size(a: float, b: float) -> bool:
    return abs(a - b) <= SAME_SIZE_TOLERANCE


@dataclass(frozen=True)
class Vocabulary:
    """Hyphenated words of a document; they decide whether a line-end hyphen is kept."""

    compounds: frozenset[str] = frozenset()  # lowercase hyphenated words seen inside a line


_WORD_TOKEN = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
_TRAILING_WORD = re.compile(r"([A-Za-z]+)$")
_LEADING_WORD = re.compile(r"^([A-Za-z]+)")


def _ends_with_split(text: str) -> bool:
    return text.endswith(HYPHENS)


def build_vocabulary(lines: list[TextLine]) -> Vocabulary:
    """Collect the hyphenated words that appear inside a line (a hyphen at a line end is not one)."""
    compounds = {
        token.lower() for line in lines for token in _WORD_TOKEN.findall(line.text) if "-" in token
    }
    return Vocabulary(frozenset(compounds))


def keep_compound_hyphen(before: str, after: str, vocabulary: Vocabulary) -> bool:
    """SPEC §6.3.6 (rev 4): keep the hyphen only if `before-after` appears inside a line elsewhere."""
    return f"{before.lower()}-{after.lower()}" in vocabulary.compounds


def _join(prev: str, nxt: str, vocabulary: Vocabulary | None = None) -> str:
    """Join two line texts. A trailing hyphen before a lowercase letter is removed (§6.3.2)
    unless the two parts form a compound word (§6.3.6)."""
    if _ends_with_split(prev) and nxt[:1].islower():
        before = _TRAILING_WORD.search(prev[:-1])
        after = _LEADING_WORD.match(nxt)
        if (
            vocabulary is not None
            and before is not None
            and after is not None
            and keep_compound_hyphen(before.group(1), after.group(1), vocabulary)
        ):
            return prev[:-1] + "-" + nxt
        return prev[:-1] + nxt
    return f"{prev} {nxt}"


def _merge_unit(unit: TextLine, line: TextLine, vocabulary: Vocabulary | None = None) -> TextLine:
    bbox = unit.bbox
    if unit.bbox is not None and line.bbox is not None and unit.page == line.page:
        bbox = (
            min(unit.bbox[0], line.bbox[0]),
            min(unit.bbox[1], line.bbox[1]),
            max(unit.bbox[2], line.bbox[2]),
            max(unit.bbox[3], line.bbox[3]),
        )
    return replace(unit, text=_join(unit.text, line.text, vocabulary), bbox=bbox)


def _breaks_within_block(
    prev: TextLine, line: TextLine, block_left: float, block_right: float
) -> bool:
    assert prev.bbox is not None and line.bbox is not None
    height = max(prev.bbox[3] - prev.bbox[1], 1.0)
    if line.bbox[1] - prev.bbox[3] > PARAGRAPH_GAP_RATIO * height:
        return True
    indented = line.bbox[0] > block_left + INDENT_TOLERANCE
    if indented and abs(line.bbox[0] - prev.bbox[0]) > INDENT_TOLERANCE:
        return True
    width = max(block_right - block_left, 1.0)
    short = prev.bbox[2] < block_right - SHORT_LINE_RATIO * width
    return prev.text.endswith(TERMINAL_PUNCTUATION) and short


def _continues_across_blocks(unit: TextLine, line: TextLine, body_size: float) -> bool:
    """A body paragraph cut by a page or column break continues when it has no terminal
    punctuation (or ends in a hyphen) and the next block starts with a lowercase letter."""
    return (
        _same_size(unit.font_size, body_size)
        and _same_size(line.font_size, body_size)
        and line.text[:1].islower()
        and not unit.text.endswith(TERMINAL_PUNCTUATION)
    )


def merge_into_paragraphs(lines: list[TextLine], body_size: float) -> list[TextLine]:
    """Merge PDF lines into paragraph units (§6.3.2-3). DOCX lines are already paragraphs.

    A unit keeps the font_size, bold and style of its first line; headings stay separate units
    because their size (or block) differs from the body text around them.
    """
    block_edges: dict[tuple[int | None, int | None], tuple[float, float]] = {}
    for line in lines:
        if line.bbox is None:
            continue
        key = (line.page, line.block_id)
        left, right = block_edges.get(key, (math.inf, -math.inf))
        block_edges[key] = (min(left, line.bbox[0]), max(right, line.bbox[2]))
    vocabulary = build_vocabulary(lines)

    units: list[TextLine] = []
    prev: TextLine | None = None
    for line in lines:
        if not units or prev is None or line.bbox is None or prev.bbox is None:
            units.append(replace(line))
            prev = line
            continue
        unit = units[-1]
        same_block = (line.page, line.block_id) == (prev.page, prev.block_id)
        if same_block:
            left, right = block_edges[(line.page, line.block_id)]
            merge = _same_size(line.font_size, prev.font_size) and not _breaks_within_block(
                prev, line, left, right
            )
        else:
            merge = _continues_across_blocks(unit, line, body_size)
        if merge:
            units[-1] = _merge_unit(unit, line, vocabulary)
        else:
            units.append(replace(line))
        prev = line
    normalized = [replace(u, text=normalize_chars(u.text)) for u in units]
    return [u for u in normalized if u.text]


def _soft_hyphen_to_hyphen(text: str) -> str:
    stripped = text.rstrip()
    return stripped[:-1] + "-" if stripped.endswith("\u00ad") else text


def clean_lines(
    lines: list[TextLine],
    page_count: int | None,
    page_height_by_page: dict[int, float] | None = None,
) -> tuple[list[TextLine], float]:
    """Run all cleaning steps in SPEC order; return (paragraph units, body font size).

    Characters are normalized first as well as last, so header matching and caption/equation
    tests see the same text. A soft hyphen ending a line becomes "-" so the hyphenation rule
    can still join the word.
    """
    lines = [replace(line, text=normalize_chars(_soft_hyphen_to_hyphen(line.text))) for line in lines]
    lines = [line for line in lines if line.text]
    heights = page_height_by_page or {}
    if page_count:
        lines = remove_running_headers_footers(lines, page_count, heights)
    body_size = body_font_size(lines)
    lines = drop_captions_footnotes_equations(lines, body_size, heights)
    units = merge_into_paragraphs(lines, body_size)
    return units, body_size
