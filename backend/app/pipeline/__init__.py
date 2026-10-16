"""Document pipeline: validate -> extract -> clean -> structure -> DocumentModel (SPEC §6.1-6.4)."""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import Settings
from app.models import DocumentModel
from app.pipeline.clean import clean_lines
from app.pipeline.errors import PipelineError, fail
from app.pipeline.extract_docx import extract_docx
from app.pipeline.extract_pdf import extract_pdf, get_page_heights
from app.pipeline.structure import detect_structure
from app.pipeline.validate import validate_file

__all__ = ["PipelineError", "run_pipeline"]

logger = logging.getLogger(__name__)


@contextmanager
def _stage(name: str, filename: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    logger.info("%s: %s took %.2f s", filename, name, time.perf_counter() - start)


def run_pipeline(path: Path, original_filename: str, settings: Settings) -> DocumentModel:
    """Turn a PDF/DOCX on disk into a DocumentModel, or raise PipelineError."""
    with _stage("validate", original_filename):
        validation = validate_file(path, original_filename, settings)

    with _stage("extract", original_filename):
        if validation.source_type == "pdf":
            lines, outline, page_count = extract_pdf(path, validation.skip_pages)
            page_heights = get_page_heights(path)
        else:
            lines, outline, page_count, page_heights = extract_docx(path), [], None, {}

    with _stage("clean", original_filename):
        units, _ = clean_lines(lines, page_count, page_heights)

    with _stage("structure", original_filename):
        chapters, source = detect_structure(units, outline, validation.source_type)

    word_count = sum(c.word_count for c in chapters)
    if word_count > settings.max_words:
        raise fail("TOO_MANY_WORDS")
    if word_count < settings.min_words:
        raise fail("EMPTY_DOCUMENT")

    return DocumentModel(
        source_filename=original_filename,
        source_type=validation.source_type,
        page_count=page_count,
        word_count=word_count,
        structure_source=source,
        warnings=validation.warnings,
        chapters=chapters,
    )
