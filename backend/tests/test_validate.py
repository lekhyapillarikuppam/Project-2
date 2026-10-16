import zipfile
from pathlib import Path

import pymupdf
import pytest

from app.config import Settings
from app.pipeline.errors import ERROR_MESSAGES, PipelineError
from app.pipeline.validate import validate_file


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


def make_pdf(path: Path, pages: list[str]) -> Path:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def code_of(path: Path, name: str, settings: Settings) -> str:
    with pytest.raises(PipelineError) as info:
        validate_file(path, name, settings)
    assert info.value.message == ERROR_MESSAGES[info.value.code]
    return info.value.code


def test_text_file_renamed_to_pdf(tmp_path: Path, settings: Settings) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_text("This is plain text, not a PDF.")
    assert code_of(fake, "fake.pdf", settings) == "UNSUPPORTED_TYPE"


def test_empty_file(tmp_path: Path, settings: Settings) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert code_of(empty, "empty.pdf", settings) == "UNSUPPORTED_TYPE"


def test_legacy_doc_and_other_extensions(tmp_path: Path, settings: Settings) -> None:
    doc = tmp_path / "old.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    assert code_of(doc, "old.doc", settings) == "LEGACY_DOC"
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    assert code_of(txt, "notes.txt", settings) == "UNSUPPORTED_TYPE"


def test_zip_without_document_xml(tmp_path: Path, settings: Settings) -> None:
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "not word")
    assert code_of(bad, "bad.docx", settings) == "UNSUPPORTED_TYPE"


def test_corrupt_docx(tmp_path: Path, settings: Settings) -> None:
    bad = tmp_path / "broken.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("word/document.xml", "<not xml")
    assert code_of(bad, "broken.docx", settings) == "CORRUPT_FILE"


def test_corrupt_pdf(tmp_path: Path, settings: Settings) -> None:
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.7\n garbage garbage garbage")
    assert code_of(bad, "broken.pdf", settings) == "CORRUPT_FILE"


def test_real_one_page_pdf_is_accepted(tmp_path: Path, settings: Settings) -> None:
    pdf = make_pdf(tmp_path / "ok.pdf", ["Hello world, this page has plenty of extractable text."])
    result = validate_file(pdf, "ok.pdf", settings)
    assert result.source_type == "pdf"
    assert result.page_count == 1
    assert result.skip_pages == set() and result.warnings == []


def test_extension_check_is_case_insensitive(tmp_path: Path, settings: Settings) -> None:
    pdf = make_pdf(tmp_path / "upper.PDF", ["Hello world, this page has plenty of extractable text."])
    assert validate_file(pdf, "UPPER.PDF", settings).source_type == "pdf"


def test_blank_pages_are_skipped_silently(tmp_path: Path, settings: Settings) -> None:
    pdf = make_pdf(tmp_path / "blank.pdf", ["Real text on the first page, long enough to count.", ""])
    result = validate_file(pdf, "blank.pdf", settings)
    assert result.blank_pages == {1} and result.warnings == []


def test_too_many_pages(tmp_path: Path) -> None:
    small = Settings(_env_file=None, max_pdf_pages=2)
    pdf = make_pdf(tmp_path / "long.pdf", ["Page with enough text to be a real page."] * 3)
    assert code_of(pdf, "long.pdf", small) == "TOO_MANY_PAGES"


def test_file_too_large(tmp_path: Path) -> None:
    tiny = Settings(_env_file=None, max_file_mb=0)
    pdf = make_pdf(tmp_path / "big.pdf", ["Some text that makes the file non-empty."])
    assert code_of(pdf, "big.pdf", tiny) == "FILE_TOO_LARGE"


def test_encrypted_pdf(tmp_path: Path, settings: Settings) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Secret text that is long enough to count.")
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    doc.close()
    assert code_of(path, "locked.pdf", settings) == "ENCRYPTED_FILE"


def _image_page(doc: pymupdf.Document) -> None:
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 300, 300), pixmap=pix)


def test_scanned_pdf_rejected_and_partial_scans_warned(tmp_path: Path, settings: Settings) -> None:
    doc = pymupdf.open()
    for _ in range(3):
        _image_page(doc)
    doc.new_page().insert_text((72, 72), "One page of real text that is long enough.")
    scanned = tmp_path / "scanned.pdf"
    doc.save(scanned)
    doc.close()
    assert code_of(scanned, "scanned.pdf", settings) == "SCANNED_PDF"

    doc = pymupdf.open()
    _image_page(doc)
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "A page of real text that is long enough.")
    partial = tmp_path / "partial.pdf"
    doc.save(partial)
    doc.close()
    result = validate_file(partial, "partial.pdf", settings)
    assert result.image_only_pages == {0}
    assert result.warnings == ["1 image-only pages were skipped"]
