"""Download filenames and Content-Disposition (SPEC §9).

`{book}_{nn}_{title}.mp3` for a chapter and `{book}.mp3` for the full book. Each part: characters
invalid on Windows and control characters removed, whitespace runs -> `_`, cut to 60 characters.
"""

import re
from urllib.parse import quote

PART_MAX_CHARS = 60
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_part(text: str, fallback: str) -> str:
    # Whitespace first, so tabs and newlines become "_" instead of being dropped as control chars.
    cleaned = _INVALID.sub("", _WHITESPACE.sub("_", text.strip()))[:PART_MAX_CHARS]
    return cleaned or fallback


def chapter_filename(book: str, index: int, title: str) -> str:
    return f"{sanitize_part(book, 'audiobook')}_{index:02d}_{sanitize_part(title, 'chapter')}.mp3"


def full_book_filename(book: str) -> str:
    return f"{sanitize_part(book, 'audiobook')}.mp3"


def content_disposition(filename: str) -> str:
    """`attachment` with an ASCII-only `filename` (non-ASCII -> `_`) plus RFC 5987 `filename*`."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"
