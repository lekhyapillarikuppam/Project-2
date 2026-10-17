"""Upload validation (SPEC §6.1 steps 1-7) for a file that is already on disk.

The streaming / Content-Length part of step 1 belongs to the upload endpoint (Phase 3);
here the size check uses the file size on disk.
"""

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import docx
import pymupdf

from app.config import Settings
from app.pipeline.errors import fail

logger = logging.getLogger(__name__)

PDF_SIGNATURE = b"%PDF"
DOCX_REQUIRED_PART = "word/document.xml"


@dataclass
class ValidationResult:
    source_type: Literal["pdf", "docx"]
    page_count: int | None = None
    image_only_pages: set[int] = field(default_factory=set)  # 0-based; skipped with a warning
    blank_pages: set[int] = field(default_factory=set)  # 0-based; skipped silently
    warnings: list[str] = field(default_factory=list)

    @property
    def skip_pages(self) -> set[int]:
        return self.image_only_pages | self.blank_pages


def check_extension(original_filename: str) -> Literal["pdf", "docx"]:
    suffix = Path(original_filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".doc":
        raise fail("LEGACY_DOC")
    raise fail("UNSUPPORTED_TYPE")


def check_signature(path: Path, source_type: Literal["pdf", "docx"]) -> None:
    if source_type == "pdf":
        with path.open("rb") as f:
            if f.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
                raise fail("UNSUPPORTED_TYPE")
        return
    if not zipfile.is_zipfile(path):
        raise fail("UNSUPPORTED_TYPE")
    try:
        with zipfile.ZipFile(path) as z:
            if DOCX_REQUIRED_PART not in z.namelist():
                raise fail("UNSUPPORTED_TYPE")
    except zipfile.BadZipFile as e:
        raise fail("UNSUPPORTED_TYPE") from e


def classify_pages(
    doc: pymupdf.Document, min_chars: int
) -> tuple[set[int], set[int]]:
    """Return (image_only_pages, blank_pages), both 0-based (SPEC §6.1 step 7)."""
    image_only: set[int] = set()
    blank: set[int] = set()
    for page in doc:
        chars = sum(1 for c in page.get_text("text") if not c.isspace())
        if chars >= min_chars:
            continue
        if page.get_image_info():
            image_only.add(page.number)
        else:
            blank.add(page.number)
    return image_only, blank


def _validate_pdf(path: Path, settings: Settings) -> ValidationResult:
    doc = None
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001 - any open failure means a damaged file
        logger.info("Could not open %s as PDF: %s", path.name, e)
    if doc is None:
        # Raised outside the except block and unchained: the open error's traceback keeps the
        # file handle alive, and Windows then refuses to delete the rejected upload.
        raise fail("CORRUPT_FILE")
    with doc:
        # needs_pass: a user password is required. Owner-password-only PDFs open fine and are accepted.
        if doc.needs_pass:
            raise fail("ENCRYPTED_FILE")
        if doc.page_count == 0:
            raise fail("CORRUPT_FILE")
        if doc.page_count > settings.max_pdf_pages:
            raise fail("TOO_MANY_PAGES")
        image_only, blank = classify_pages(doc, settings.scanned_page_min_chars)
        non_blank = doc.page_count - len(blank)
        if non_blank > 0 and len(image_only) > settings.scanned_page_ratio * non_blank:
            raise fail("SCANNED_PDF")
        result = ValidationResult("pdf", doc.page_count, image_only, blank)
        if image_only:
            result.warnings.append(f"{len(image_only)} image-only pages were skipped")
        return result


def _validate_docx(path: Path) -> ValidationResult:
    opened = False
    try:
        docx.Document(str(path))
        opened = True
    except Exception as e:  # noqa: BLE001 - any open failure means a damaged file
        logger.info("Could not open %s as DOCX: %s", path.name, e)
    if not opened:
        raise fail("CORRUPT_FILE")  # outside the except block, as in _validate_pdf
    return ValidationResult("docx")


def validate_file(path: Path, original_filename: str, settings: Settings) -> ValidationResult:
    """Run SPEC §6.1 steps 1-7 in order; raise PipelineError at the first failure."""
    if path.stat().st_size > settings.max_file_bytes:
        raise fail("FILE_TOO_LARGE")
    source_type = check_extension(original_filename)
    check_signature(path, source_type)
    if source_type == "pdf":
        result = _validate_pdf(path, settings)
    else:
        result = _validate_docx(path)
    logger.info(
        "Validated %s as %s (pages=%s, image-only=%d, blank=%d)",
        original_filename,
        result.source_type,
        result.page_count,
        len(result.image_only_pages),
        len(result.blank_pages),
    )
    return result
