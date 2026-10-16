"""DOCX extraction with python-docx (SPEC §6.2). Tables, footnotes and endnotes are not read."""

import logging
from collections import Counter
from pathlib import Path

import docx
from docx.styles.style import BaseStyle
from docx.text.paragraph import Paragraph as DocxParagraph

from app.models import TextLine

logger = logging.getLogger(__name__)

DEFAULT_FONT_SIZE = 11.0  # used when neither the runs nor the style chain set a size


def _style_chain(style: BaseStyle | None) -> list[BaseStyle]:
    chain = []
    while style is not None and len(chain) < 20:
        chain.append(style)
        style = style.base_style
    return chain


def _style_font_size(style: BaseStyle | None) -> float | None:
    for s in _style_chain(style):
        font = getattr(s, "font", None)
        if font is not None and font.size is not None:
            return font.size.pt
    return None


def _style_bold(style: BaseStyle | None) -> bool:
    for s in _style_chain(style):
        font = getattr(s, "font", None)
        if font is not None and font.bold is not None:
            return bool(font.bold)
    return False


def paragraph_to_line(paragraph: DocxParagraph, index: int) -> TextLine | None:
    text = paragraph.text.strip()
    if not text:
        return None
    style = paragraph.style
    style_size = _style_font_size(style)
    style_bold = _style_bold(style)
    runs = [r for r in paragraph.runs if r.text.strip()]
    sizes: Counter[float] = Counter()
    bold_all = bool(runs)
    for run in runs:
        n = len(run.text.strip())
        size = run.font.size.pt if run.font.size is not None else style_size
        sizes[size if size is not None else DEFAULT_FONT_SIZE] += n
        run_bold = run.bold if run.bold is not None else style_bold
        bold_all = bold_all and bool(run_bold)
    font_size = sizes.most_common(1)[0][0] if sizes else (style_size or DEFAULT_FONT_SIZE)
    return TextLine(
        text=text,
        font_size=float(font_size),
        bold=bold_all,
        style=style.name if style is not None else None,
        block_id=index,
    )


def extract_docx(path: Path) -> list[TextLine]:
    """Body paragraphs in document order (tables are separate elements and are skipped)."""
    document = docx.Document(str(path))
    lines = [
        line
        for i, p in enumerate(document.paragraphs)
        if (line := paragraph_to_line(p, i)) is not None
    ]
    logger.info("Extracted %d paragraphs from DOCX", len(lines))
    return lines
