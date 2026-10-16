"""PDF extraction with PyMuPDF (SPEC §6.2), including the multi-column reading-order rule."""

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.models import TextLine

logger = logging.getLogger(__name__)

# Substrings of font names that mark math fonts (TeX Computer Modern math, generic math/symbol fonts).
MATH_FONT_MARKERS = ("CMMI", "CMSY", "CMEX", "MATH", "SYMBOL")
# A visual line is "math font" when at least this share of its letters (or of all its non-space
# characters, if it has no letters) is set in math fonts. Counting letters ignores the digits and
# brackets of equation numbers such as "(4.1)".
MATH_FONT_LINE_RATIO = 0.5
# PyMuPDF span flag bits.
FLAG_SUPERSCRIPT = 1
FLAG_BOLD = 16
# Superscript spans made only of these characters are footnote markers and are removed (SPEC §6.3.5).
FOOTNOTE_MARKER_CHARS = set("0123456789*†‡§¶")
# Lines whose writing direction deviates from horizontal by more than this (|dir.y|) are skipped
# (rotated axis labels, margin notes).
MAX_LINE_DIR_Y = 0.1
# Two PyMuPDF lines of one block belong to the same visual line when their vertical centres differ by
# less than this fraction of the smaller line height.
SAME_VISUAL_LINE_RATIO = 0.5
# A block in which every visual line consists of at least this many separately placed pieces is a
# table without ruling lines (find_tables() misses those) and is skipped.
TABLE_MIN_CELLS = 3

# SPEC §6.2 reading order.
FULL_WIDTH_RATIO = 0.6  # block width > 60% of the text-area width -> full-width
# A block that reaches this far (fraction of text-area width) past the centre line on both sides
# straddles the columns (e.g. a centred author line) and is treated as full-width.
STRADDLE_TOLERANCE = 0.02
MIN_COLUMN_BLOCKS = 3  # at least 3 left and 3 right blocks
MIN_COLUMN_CHAR_SHARE = 0.5  # left+right blocks hold >= 50% of the page's characters


@dataclass
class _Block:
    bbox: tuple[float, float, float, float]
    lines: list[TextLine]

    @property
    def chars(self) -> int:
        return sum(len(line.text) for line in self.lines)


def _is_math_font(font: str) -> bool:
    upper = font.upper()
    return any(marker in upper for marker in MATH_FONT_MARKERS)


def _is_footnote_marker(span: dict) -> bool:
    text = span["text"].strip()
    return bool(text) and span["flags"] & FLAG_SUPERSCRIPT and set(text) <= FOOTNOTE_MARKER_CHARS


def _inside(bbox: tuple[float, float, float, float], rects: list[pymupdf.Rect]) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects)


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _piece_from_line(line: dict) -> dict | None:
    """Reduce one PyMuPDF line to text + style statistics, or None if it has no usable text."""
    if abs(line["dir"][1]) > MAX_LINE_DIR_Y or line["dir"][0] <= 0:
        return None
    raw = line["spans"]
    # A superscript digit right after a math-font span is an exponent ("P^-1"), not a footnote marker.
    spans = [
        s
        for i, s in enumerate(raw)
        if s["text"]
        and not (_is_footnote_marker(s) and (i == 0 or not _is_math_font(raw[i - 1]["font"])))
    ]
    text = "".join(s["text"] for s in spans)
    if not text.strip():
        return None
    size_chars: Counter[float] = Counter()
    font_chars: Counter[str] = Counter()
    bold_chars = math_chars = math_letters = letters = total = 0
    for s in spans:
        n = sum(1 for c in s["text"] if not c.isspace())
        n_letters = sum(1 for c in s["text"] if c.isalpha())
        total += n
        letters += n_letters
        size_chars[round(s["size"], 1)] += n
        font_chars[s["font"]] += n
        if s["flags"] & FLAG_BOLD:
            bold_chars += n
        if _is_math_font(s["font"]):
            math_chars += n
            math_letters += n_letters
    return {
        "text": text.strip(),
        "bbox": tuple(line["bbox"]),
        "size": size_chars.most_common(1)[0][0],
        "font": font_chars.most_common(1)[0][0],
        "bold_chars": bold_chars,
        "math_chars": math_chars,
        "math_letters": math_letters,
        "letters": letters,
        "chars": total,
    }


def _visual_lines(pieces: list[dict]) -> list[list[dict]]:
    """Group the pieces of one block that sit on the same baseline, left to right."""
    rows: list[list[dict]] = []
    for piece in sorted(pieces, key=lambda p: (p["bbox"][1] + p["bbox"][3]) / 2):
        cy = (piece["bbox"][1] + piece["bbox"][3]) / 2
        height = piece["bbox"][3] - piece["bbox"][1]
        if rows:
            last = rows[-1][0]
            last_cy = (last["bbox"][1] + last["bbox"][3]) / 2
            last_h = last["bbox"][3] - last["bbox"][1]
            if abs(cy - last_cy) < SAME_VISUAL_LINE_RATIO * min(height, last_h):
                rows[-1].append(piece)
                continue
        rows.append([piece])
    return [sorted(row, key=lambda p: p["bbox"][0]) for row in rows]


def _row_to_line(row: list[dict], page: int, block_id: int) -> TextLine:
    chars = sum(p["chars"] for p in row) or 1
    letters = sum(p["letters"] for p in row)
    if letters:
        math_share = sum(p["math_letters"] for p in row) / letters
    else:
        math_share = sum(p["math_chars"] for p in row) / chars
    sizes: Counter[float] = Counter()
    fonts: Counter[str] = Counter()
    for p in row:
        sizes[p["size"]] += p["chars"]
        fonts[p["font"]] += p["chars"]
    return TextLine(
        text=" ".join(p["text"] for p in row),
        font_size=sizes.most_common(1)[0][0],
        bold=sum(p["bold_chars"] for p in row) == chars,
        page=page,
        bbox=_union([p["bbox"] for p in row]),
        style=None,
        block_id=block_id,
        is_math_font=math_share >= MATH_FONT_LINE_RATIO,
        font=fonts.most_common(1)[0][0],
    )


def order_blocks(blocks: list[_Block]) -> list[_Block]:
    """Reading order for one page (SPEC §6.2): two-column bands, else top-to-bottom."""
    if not blocks:
        return []
    area_x0 = min(b.bbox[0] for b in blocks)
    area_x1 = max(b.bbox[2] for b in blocks)
    area_w = max(area_x1 - area_x0, 1.0)
    centre = (area_x0 + area_x1) / 2
    tol = STRADDLE_TOLERANCE * area_w

    full, left, right = [], [], []
    for b in blocks:
        width = b.bbox[2] - b.bbox[0]
        straddles = b.bbox[0] < centre - tol and b.bbox[2] > centre + tol
        if width > FULL_WIDTH_RATIO * area_w or straddles:
            full.append(b)
        elif (b.bbox[0] + b.bbox[2]) / 2 < centre:
            left.append(b)
        else:
            right.append(b)

    total_chars = sum(b.chars for b in blocks) or 1
    column_chars = sum(b.chars for b in left + right)
    two_column = (
        len(left) >= MIN_COLUMN_BLOCKS
        and len(right) >= MIN_COLUMN_BLOCKS
        and column_chars / total_chars >= MIN_COLUMN_CHAR_SHARE
    )
    if not two_column:
        return sorted(blocks, key=lambda b: (round(b.bbox[1]), b.bbox[0]))

    full.sort(key=lambda b: b.bbox[1])
    ordered: list[_Block] = []
    band_top = float("-inf")
    for separator in [*full, None]:
        band_bottom = separator.bbox[1] if separator else float("inf")
        for column in (left, right):
            in_band = [b for b in column if band_top <= b.bbox[1] < band_bottom]
            ordered += sorted(in_band, key=lambda b: b.bbox[1])
        if separator:
            ordered.append(separator)
            band_top = separator.bbox[1]
    return ordered


def _has_cell_edges(page: pymupdf.Page) -> bool:
    """True if the page's vector drawings contain both a horizontal and a vertical edge."""
    horizontal = vertical = False
    for path in page.get_cdrawings():
        for item in path["items"]:
            kind = item[0]
            if kind in ("re", "qu"):
                return True
            if kind == "l":
                (x0, y0), (x1, y1) = item[1], item[2]
                horizontal = horizontal or abs(y1 - y0) < 1
                vertical = vertical or abs(x1 - x0) < 1
            if horizontal and vertical:
                return True
    return False


def _table_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Regions found by find_tables(). Its default strategy builds cells from vector line art, so a
    page without both horizontal and vertical edges (e.g. only a header rule) cannot contain a
    table and the slow call is skipped."""
    if not _has_cell_edges(page):
        return []
    return [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]


def _page_blocks(page: pymupdf.Page, first_block_id: int) -> list[_Block]:
    table_rects = _table_rects(page)
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    data = page.get_text("dict", sort=False, flags=flags)
    blocks: list[_Block] = []
    for raw in data["blocks"]:
        if raw.get("type") != 0:
            continue
        pieces = [p for line in raw["lines"] if (p := _piece_from_line(line))]
        pieces = [p for p in pieces if not _inside(p["bbox"], table_rects)]
        if not pieces:
            continue
        rows = _visual_lines(pieces)
        if all(len(row) >= TABLE_MIN_CELLS for row in rows):
            continue
        block_id = first_block_id + len(blocks)
        lines = [_row_to_line(row, page.number, block_id) for row in rows]
        blocks.append(_Block(_union([line.bbox for line in lines]), lines))
    return blocks


def extract_pdf(
    path: Path, skip_pages: set[int]
) -> tuple[list[TextLine], list[tuple[int, str, int]], int]:
    """Return (lines in reading order, outline from get_toc(), page_count). Pages are 0-based."""
    lines: list[TextLine] = []
    with pymupdf.open(path) as doc:
        outline = [(int(level), str(title), int(page)) for level, title, page, *_ in doc.get_toc()]
        next_block_id = 0
        for page in doc:
            if page.number in skip_pages:
                continue
            blocks = order_blocks(_page_blocks(page, next_block_id))
            next_block_id += len(blocks) + 1
            for block in blocks:
                lines.extend(block.lines)
        page_count = doc.page_count
    logger.info("Extracted %d lines from %d pages", len(lines), page_count)
    return lines, outline, page_count


def get_page_heights(path: Path) -> dict[int, float]:
    """Page heights by 0-based page number (needed by the header/footer and footnote zones)."""
    with pymupdf.open(path) as doc:
        return {page.number: page.rect.height for page in doc}
