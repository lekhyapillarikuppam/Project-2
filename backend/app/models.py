"""Data models. Pydantic models mirror SPEC §7.1 (document.json); TextLine is internal to the pipeline."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel


class Segment(BaseModel):
    speaker: str = "narrator"
    text: str


class Paragraph(BaseModel):
    index: int
    segments: list[Segment]


class Chapter(BaseModel):
    index: int
    title: str
    word_count: int
    paragraphs: list[Paragraph]


class DocumentModel(BaseModel):
    source_filename: str
    source_type: Literal["pdf", "docx"]
    page_count: int | None
    word_count: int
    structure_source: Literal["outline", "styles", "typography", "patterns", "fallback"]
    warnings: list[str]
    chapters: list[Chapter]


@dataclass
class TextLine:
    """One visual line (after extraction) or one paragraph unit (after cleaning).

    Not serialized. `page` and `bbox` are PDF-only; `style` is DOCX-only.
    `font` is the dominant font name (PDF), used to recognise running-header styles.
    """

    text: str
    font_size: float
    bold: bool
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    style: str | None = None
    block_id: int | None = None
    is_math_font: bool = False
    font: str | None = None
