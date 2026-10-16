"""Structure detection (SPEC §6.4): strategies 1-5, heading-text rule, post-processing 1-6."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace

from app.models import Chapter, Paragraph, Segment, TextLine
from app.pipeline.clean import SAME_SIZE_TOLERANCE, body_font_size
from app.pipeline.titles import LABEL_ONLY, LABEL_START, MATCH_THRESHOLD, normalize_title, title_score

# --- Heading candidates (strategy 3) ---
HEADING_MAX_WORDS = 12
HEADING_SIZE_RATIO = 1.2  # >= 1.2x body font size
# Bold body-size line counts as "on its own line" when its height is under this many font sizes.
SINGLE_LINE_HEIGHT_RATIO = 1.8
# "Whitespace above": gap to the previous unit on the page of at least half the line's font size.
WHITESPACE_ABOVE_RATIO = 0.5
# A heading size level is "frequent" when it occurs on at least this many different pages
# (PDF) or this many times (DOCX). Counting pages stops a two-line title page from becoming a level.
FREQUENT_LEVEL_MIN = 2
MIN_PLAUSIBLE_CHAPTERS = 2  # every strategy needs >= 2 chapter boundaries to count as plausible

# --- Strategy 4 text patterns ---
NUMBERED_TOP = re.compile(r"^\d+\s+[A-Z]")
NUMBERED_SUB = re.compile(r"^\d+\.\d+\s+[A-Z]")

# --- Strategy 5 fallback ---
FALLBACK_PART_WORDS = 3000

# --- Post-processing ---
TOC_HEADINGS = {"contents", "table of contents"}
TOC_ENTRY = re.compile(r"(?:\.{2,}|…)\s*\S*$|\s\d+$")  # ends with a page number or dot leaders
TOC_ENTRY_SHARE = 0.5  # >= 50% of a page's units look like TOC entries
TOC_MIN_UNITS = 3  # ... on a page with at least this many units
DOCX_TOC_STYLE = re.compile(r"^toc\s*\d", re.IGNORECASE)
BACK_MATTER = {
    "references",
    "bibliography",
    "works cited",
    "index",
    "acknowledgements",
    "acknowledgments",
}
FRONT_MATTER_MIN_WORDS = 150
TINY_CHAPTER_WORDS = 40
HUGE_CHAPTER_WORDS = 12_000

HEADING_STYLE = re.compile(r"^(title|heading\s*\d)$", re.IGNORECASE)


@dataclass
class Boundary:
    index: int  # unit index where the chapter starts
    title: str
    consumes_unit: bool = True  # the unit at `index` is the heading itself (not body text)


@dataclass
class Draft:
    title: str
    units: list[TextLine] = field(default_factory=list)
    headings: list[bool] = field(default_factory=list)  # parallel to units: is a sub-heading

    @property
    def words(self) -> int:
        return sum(word_count(u.text) for u in self.units)


def word_count(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------------------- heading tests


def _is_heading_style(unit: TextLine) -> bool:
    return unit.style is not None and bool(HEADING_STYLE.match(unit.style.strip()))


def _single_line(unit: TextLine) -> bool:
    if unit.bbox is None:
        return True
    return unit.bbox[3] - unit.bbox[1] <= SINGLE_LINE_HEIGHT_RATIO * unit.font_size


def _whitespace_above(unit: TextLine, prev: TextLine | None) -> bool:
    if prev is None or unit.bbox is None or prev.bbox is None or prev.page != unit.page:
        return True
    return unit.bbox[1] - prev.bbox[3] >= WHITESPACE_ABOVE_RATIO * unit.font_size


def _short_title_text(text: str) -> bool:
    words = word_count(text)
    return 0 < words <= HEADING_MAX_WORDS and not text.rstrip().endswith(".")


def is_heading_candidate(unit: TextLine, prev: TextLine | None, body_size: float) -> bool:
    """Strategy 3 test: short, no final period, and large or bold-on-its-own-line with space above."""
    if not _short_title_text(unit.text):
        return False
    if unit.font_size >= HEADING_SIZE_RATIO * body_size:
        return True
    return unit.bold and _single_line(unit) and _whitespace_above(unit, prev)


def _heading_like(unit: TextLine, body_size: float) -> bool:
    """Looser test used by the heading-text rule: short and larger, bold, or a heading style."""
    return _short_title_text(unit.text) and (
        unit.font_size >= HEADING_SIZE_RATIO * body_size or unit.bold or _is_heading_style(unit)
    )


def heading_flags(units: list[TextLine], body_size: float) -> list[bool]:
    return [
        is_heading_candidate(u, units[i - 1] if i else None, body_size) or _is_heading_style(u)
        for i, u in enumerate(units)
    ]


# --------------------------------------------------------------------------- heading-text rule


def _union_bbox(a: TextLine, b: TextLine) -> tuple[float, float, float, float] | None:
    if a.bbox is None or b.bbox is None or a.page != b.page:
        return b.bbox
    return (
        min(a.bbox[0], b.bbox[0]),
        min(a.bbox[1], b.bbox[1]),
        max(a.bbox[2], b.bbox[2]),
        max(a.bbox[3], b.bbox[3]),
    )


def combine_heading_labels(units: list[TextLine], body_size: float) -> list[TextLine]:
    """`Chapter I` + next heading line -> one unit `Chapter I: Down the Rabbit-Hole` (SPEC §6.4)."""
    out: list[TextLine] = []
    i = 0
    while i < len(units):
        unit = units[i]
        nxt = units[i + 1] if i + 1 < len(units) else None
        if (
            nxt is not None
            and LABEL_ONLY.match(unit.text)
            and _heading_like(unit, body_size)
            and nxt.page == unit.page
            and not LABEL_ONLY.match(nxt.text)
            and _heading_like(nxt, body_size)
        ):
            label = unit.text.rstrip(" :.")
            out.append(
                replace(
                    nxt,
                    text=f"{label}: {nxt.text}",
                    font_size=max(unit.font_size, nxt.font_size),
                    bold=unit.bold or nxt.bold,
                    bbox=_union_bbox(unit, nxt),
                    style=unit.style if _is_heading_style(unit) else nxt.style,
                )
            )
            i += 2
            continue
        out.append(unit)
        i += 1
    return out


# --------------------------------------------------------------------------- post-processing 1


def remove_toc_pages(units: list[TextLine]) -> list[TextLine]:
    """Drop table-of-contents pages (PDF) or Word TOC paragraphs (DOCX) (post-processing step 1)."""
    by_page: dict[int, list[TextLine]] = defaultdict(list)
    for u in units:
        if u.page is not None:
            by_page[u.page].append(u)
    toc_pages = set()
    for page, page_units in by_page.items():
        if normalize_title(page_units[0].text) in TOC_HEADINGS:
            toc_pages.add(page)
            continue
        entries = sum(1 for u in page_units if TOC_ENTRY.search(u.text))
        if len(page_units) >= TOC_MIN_UNITS and entries >= TOC_ENTRY_SHARE * len(page_units):
            toc_pages.add(page)

    kept: list[TextLine] = []
    for u in units:
        if u.page is not None and u.page in toc_pages:
            continue
        if u.style is not None and DOCX_TOC_STYLE.match(u.style):
            if kept and kept[-1].page is None and normalize_title(kept[-1].text) in TOC_HEADINGS:
                kept.pop()
            continue
        kept.append(u)
    return kept


# --------------------------------------------------------------------------- strategies


def _best_match_on_page(
    units: list[TextLine], page: int, after: int, title: str
) -> int | None:
    best: tuple[float, float, int] | None = None
    for i in range(after + 1, len(units)):
        u = units[i]
        if u.page != page:
            continue
        score = title_score(u.text, title)
        if score < MATCH_THRESHOLD:
            continue
        key = (score, u.font_size, -i)
        if best is None or key > best:
            best = key
    return -best[2] if best else None


def strategy_outline(units: list[TextLine], outline: list[tuple[int, str, int]]) -> list[Boundary] | None:
    """Strategy 1: PDF outline level-1 entries (level 2 if there is only one level-1 entry)."""
    level1 = [e for e in outline if e[0] == 1]
    entries = level1 if len(level1) != 1 else [e for e in outline if e[0] == 2]
    if len(entries) < MIN_PLAUSIBLE_CHAPTERS:
        return None
    boundaries: list[Boundary] = []
    last = -1
    for _, title, page_1based in entries:
        title = title.strip()
        page = page_1based - 1
        found = None
        for p in (page, page + 1, page - 1):
            found = _best_match_on_page(units, p, last, title)
            if found is not None:
                break
        if found is not None:
            boundaries.append(Boundary(found, units[found].text))
            last = found
            continue
        # No heading line matched: start the chapter at the first unit on (or after) its page.
        start = next((i for i in range(last + 1, len(units)) if (units[i].page or 0) >= page), None)
        if start is None:
            continue
        boundaries.append(Boundary(start, title, consumes_unit=False))
        last = start
    return boundaries if len(boundaries) >= MIN_PLAUSIBLE_CHAPTERS else None


def strategy_styles(units: list[TextLine]) -> list[Boundary] | None:
    """Strategy 2: DOCX Title / Heading 1 (Heading 2 when there are fewer than two Heading 1s)."""
    def style_of(u: TextLine) -> str:
        return re.sub(r"\s+", " ", (u.style or "").strip().lower())

    h1 = sum(1 for u in units if style_of(u) == "heading 1")
    levels = {"title", "heading 1"} if h1 >= 2 else {"title", "heading 1", "heading 2"}
    chapter_level = "heading 1" if h1 >= 2 else "heading 2"
    boundaries = [Boundary(i, u.text) for i, u in enumerate(units) if style_of(u) in levels]
    real = sum(1 for b in boundaries if style_of(units[b.index]) in {chapter_level, "heading 1"})
    return boundaries if real >= MIN_PLAUSIBLE_CHAPTERS else None


def _cluster_sizes(sizes: list[float]) -> list[list[float]]:
    clusters: list[list[float]] = []
    for size in sorted(set(sizes), reverse=True):
        if clusters and clusters[-1][0] - size <= SAME_SIZE_TOLERANCE:
            clusters[-1].append(size)
        else:
            clusters.append([size])
    return clusters


def strategy_typography(units: list[TextLine], body_size: float) -> list[Boundary] | None:
    """Strategy 3: heading candidates clustered by size; the largest frequent level is chapters."""
    candidates = [
        i for i, u in enumerate(units) if is_heading_candidate(u, units[i - 1] if i else None, body_size)
    ]
    if not candidates:
        return None
    for cluster in _cluster_sizes([units[i].font_size for i in candidates]):
        members = [i for i in candidates if units[i].font_size in cluster]
        pages = {units[i].page for i in members}
        occurrences = len(members) if None in pages else len(pages)
        if occurrences >= FREQUENT_LEVEL_MIN:
            if len(members) < MIN_PLAUSIBLE_CHAPTERS:
                return None
            return [Boundary(i, units[i].text) for i in members]
    return None


def strategy_patterns(units: list[TextLine]) -> list[Boundary] | None:
    """Strategy 4: chapter-label lines and top-level numbered headings (sub-numbers if needed)."""
    def short(u: TextLine) -> bool:
        return _short_title_text(u.text)

    chosen = [
        i
        for i, u in enumerate(units)
        if short(u) and (LABEL_START.match(u.text) or NUMBERED_TOP.match(u.text))
    ]
    if len(chosen) < MIN_PLAUSIBLE_CHAPTERS:
        chosen = sorted(
            set(chosen) | {i for i, u in enumerate(units) if short(u) and NUMBERED_SUB.match(u.text)}
        )
    if len(chosen) < MIN_PLAUSIBLE_CHAPTERS:
        return None
    return [Boundary(i, units[i].text) for i in chosen]


def strategy_fallback(units: list[TextLine]) -> list[Boundary]:
    """Strategy 5: `Part 1`, `Part 2`, ... of roughly FALLBACK_PART_WORDS words."""
    total = sum(word_count(u.text) for u in units)
    parts = max(1, round(total / FALLBACK_PART_WORDS))
    target = total / parts
    boundaries = [Boundary(0, "Part 1", consumes_unit=False)] if units else []
    running = 0
    for i, u in enumerate(units):
        if running >= target * len(boundaries) and len(boundaries) < parts and i > 0:
            boundaries.append(Boundary(i, f"Part {len(boundaries) + 1}", consumes_unit=False))
        running += word_count(u.text)
    return boundaries


# --------------------------------------------------------------------------- drafts


def build_drafts(
    units: list[TextLine], flags: list[bool], boundaries: list[Boundary]
) -> tuple[Draft, list[Draft]]:
    """Split units at the boundaries. Returns (front matter, chapters)."""
    front = Draft(title="")
    chapters: list[Draft] = []
    starts = {b.index: b for b in boundaries}
    current = front
    for i, (u, is_heading) in enumerate(zip(units, flags)):
        if i in starts:
            b = starts[i]
            current = Draft(title=b.title)
            chapters.append(current)
            if b.consumes_unit:
                continue
        current.units.append(u)
        current.headings.append(is_heading)
    return front, chapters


def drop_back_matter(chapters: list[Draft]) -> list[Draft]:
    """Post-processing step 2."""
    return [c for c in chapters if normalize_title(c.title) not in BACK_MATTER]


def apply_front_matter(front: Draft, chapters: list[Draft]) -> list[Draft]:
    """Post-processing step 3: keep text before the first chapter if it has >= 150 words."""
    if front.words < FRONT_MATTER_MIN_WORDS:
        return chapters
    first_is_intro = bool(chapters) and normalize_title(chapters[0].title) == "introduction"
    front.title = "Front Matter" if first_is_intro else "Introduction"
    return [front, *chapters]


def merge_tiny_chapters(chapters: list[Draft]) -> list[Draft]:
    """Post-processing step 4: chapters under 40 words merge into the next (last: into previous).

    The tiny chapter's own title is kept as its first paragraph, so no text is lost. Working from
    the end keeps the text in reading order when several tiny chapters follow each other.
    """
    result = [Draft(c.title, list(c.units), list(c.headings)) for c in chapters]
    for i in range(len(result) - 1, -1, -1):
        c = result[i]
        if len(result) == 1 or c.words >= TINY_CHAPTER_WORDS:
            continue
        title_unit = [TextLine(text=c.title, font_size=0.0, bold=True)] if c.title else []
        title_flag = [True] if c.title else []
        if i + 1 < len(result):
            nxt = result[i + 1]
            nxt.units = title_unit + c.units + nxt.units
            nxt.headings = title_flag + c.headings + nxt.headings
        else:
            prev = result[i - 1]
            prev.units += title_unit + c.units
            prev.headings += title_flag + c.headings
        del result[i]
    return result


def _split_points(words: list[int], allowed: list[int], parts: int) -> list[int]:
    """Choose `parts - 1` unit indices from `allowed` closest to equal word shares."""
    total = sum(words)
    cumulative = [0]
    for w in words:
        cumulative.append(cumulative[-1] + w)
    points: list[int] = []
    for k in range(1, parts):
        target = total * k / parts
        options = [i for i in allowed if i > (points[-1] if points else 0)]
        if not options:
            break
        points.append(min(options, key=lambda i: abs(cumulative[i] - target)))
    return points


def split_huge_chapters(chapters: list[Draft]) -> list[Draft]:
    """Post-processing step 5: > 12,000 words -> `Title (Part n)` at sub-headings or paragraphs."""
    result: list[Draft] = []
    for c in chapters:
        if c.words <= HUGE_CHAPTER_WORDS:
            result.append(c)
            continue
        parts = math.ceil(c.words / HUGE_CHAPTER_WORDS)
        words = [word_count(u.text) for u in c.units]
        sub_headings = [i for i, h in enumerate(c.headings) if h and i > 0]
        allowed = sub_headings if len(sub_headings) >= parts - 1 else list(range(1, len(c.units)))
        points = _split_points(words, allowed, parts)
        edges = [0, *points, len(c.units)]
        for n, (a, b) in enumerate(zip(edges, edges[1:]), start=1):
            result.append(Draft(f"{c.title} (Part {n})", c.units[a:b], c.headings[a:b]))
    return result


def to_models(chapters: list[Draft]) -> list[Chapter]:
    """Post-processing step 6: number chapters 1..N and build the document model."""
    out = []
    for n, c in enumerate(chapters, start=1):
        paragraphs = [
            Paragraph(index=k, segments=[Segment(speaker="narrator", text=u.text)])
            for k, u in enumerate(c.units, start=1)
        ]
        out.append(Chapter(index=n, title=c.title, word_count=c.words, paragraphs=paragraphs))
    return out


# --------------------------------------------------------------------------- entry point


def detect_structure(
    units: list[TextLine], outline: list[tuple[int, str, int]], source_type: str
) -> tuple[list[Chapter], str]:
    """Return (chapters, structure_source) for cleaned paragraph units."""
    body_size = body_font_size(units)
    units = remove_toc_pages(units)
    units = combine_heading_labels(units, body_size)
    flags = heading_flags(units, body_size)

    attempts: list[tuple[str, list[Boundary] | None]] = []
    if source_type == "pdf" and outline:
        attempts.append(("outline", strategy_outline(units, outline)))
    if source_type == "docx":
        attempts.append(("styles", strategy_styles(units)))
    source, boundaries = "fallback", None
    for name, result in attempts:
        if result:
            source, boundaries = name, result
            break
    if boundaries is None:
        for name, strategy in (
            ("typography", lambda: strategy_typography(units, body_size)),
            ("patterns", lambda: strategy_patterns(units)),
        ):
            result = strategy()
            if result:
                source, boundaries = name, result
                break
    if boundaries is None:
        boundaries = strategy_fallback(units)

    front, chapters = build_drafts(units, flags, boundaries)
    chapters = drop_back_matter(chapters)
    chapters = apply_front_matter(front, chapters)
    chapters = merge_tiny_chapters(chapters)
    chapters = split_huge_chapters(chapters)
    if not chapters and units:
        # Everything was front/back matter: narrate it all rather than produce nothing.
        source = "fallback"
        front, chapters = build_drafts(units, flags, strategy_fallback(units))
        chapters = split_huge_chapters(merge_tiny_chapters(chapters))
    return to_models(chapters), source
