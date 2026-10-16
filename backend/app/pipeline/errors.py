"""Pipeline error type and the user-facing messages from SPEC §12."""

ERROR_MESSAGES: dict[str, str] = {
    "UNSUPPORTED_TYPE": "Only PDF and DOCX files are supported.",
    "LEGACY_DOC": (
        "Old Word (.doc) files aren't supported. Open it in Word and \"Save As\" .docx, then try again."
    ),
    "FILE_TOO_LARGE": "This file is larger than 50 MB.",
    "TOO_MANY_PAGES": "This PDF has more than 200 pages. Please upload a shorter document.",
    "TOO_MANY_WORDS": (
        "This document is too long (over 100,000 words). Please upload a shorter document."
    ),
    "CORRUPT_FILE": "This file couldn't be opened. It may be damaged.",
    "ENCRYPTED_FILE": "This PDF is password-protected. Remove the password and try again.",
    "SCANNED_PDF": (
        "This PDF looks like scanned images with no selectable text. Scanned documents aren't supported."
    ),
    "EMPTY_DOCUMENT": "No readable text was found in this document.",
    "SERVER_BUSY": "The server is busy converting other documents. Please try again in a few minutes.",
    "JOB_NOT_FOUND": (
        "This audiobook is no longer available (audio is temporary). Please upload the document again."
    ),
    "JOB_EXPIRED": (
        "This audiobook is no longer available (audio is temporary). Please upload the document again."
    ),
    "CHAPTER_NOT_READY": "This chapter is still being generated.",
    "TTS_FAILED": "Narration failed for this chapter.",
}


class PipelineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def fail(code: str) -> PipelineError:
    """Build a PipelineError with the catalogue message for `code`."""
    return PipelineError(code, ERROR_MESSAGES[code])
