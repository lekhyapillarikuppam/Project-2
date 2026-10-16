"""Chunking (SPEC §6.6): sentences packed into chunks of at most `max_chars` characters.

Chunk 0 of every chapter is the spoken title. A chunk never spans two paragraphs, so the
assembler can put the paragraph pause after the right chunk.
"""

import re
from dataclasses import dataclass

from app.models import Chapter
from app.tts.normalize import normalize_for_speech, normalize_heading

# Words that end with a period but almost never end a sentence.
NON_TERMINAL_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "st", "mt", "sr", "jr", "capt", "col", "gen", "lt", "sgt",
    "rev", "hon", "fig", "figs", "no", "nos", "vs", "approx", "e.g", "i.e", "cf", "al", "vol",
    "pp", "ch", "sec", "eq", "dept", "inc", "ltd", "co",
}  # fmt: skip
# Sentence end: terminal punctuation, optional closing quotes/brackets, whitespace, then something
# that can start a sentence (capital letter, digit, or an opening quote/bracket).
_BOUNDARY = re.compile(r"([.!?…]+[\"'’”)\]]*)(\s+)(?=[\"'‘“(\[]?[A-Z0-9])")
_WORD_BEFORE = re.compile(r"([\w.]+)$")
_LONG_SPLIT_SEPARATORS = (";", ",", " ")


@dataclass(frozen=True)
class Chunk:
    chapter_index: int
    seq: int  # 0 = title, then 1.. in reading order
    text: str  # normalized for speech
    paragraph_index: int  # 0 for the title
    ends_paragraph: bool
    is_title: bool


def _is_abbreviation_or_initial(text_before: str, punct: str) -> bool:
    if not punct.startswith(".") or len(punct.rstrip("\"'’”)]")) != 1:
        return False
    m = _WORD_BEFORE.search(text_before)
    if not m:
        return False
    word = m.group(1)
    if len(word) == 1 and word.isupper():  # an initial: "G. K. Chesterton"
        return True
    return word.lower().rstrip(".") in NON_TERMINAL_ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split text into sentences without breaking on abbreviations, initials or decimals."""
    sentences: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if _is_abbreviation_or_initial(text[start : m.start()], m.group(1)):
            continue
        sentence = text[start : m.end(1)].strip()
        if sentence:
            sentences.append(sentence)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """Split a sentence longer than max_chars at ';', then ',', then spaces (§6.6)."""
    if len(sentence) <= max_chars:
        return [sentence]
    for sep in _LONG_SPLIT_SEPARATORS:
        pieces = sentence.split(sep)
        keep = "" if sep == " " else sep
        pieces = [p.strip() for p in [p + keep for p in pieces[:-1]] + [pieces[-1]] if p.strip()]
        if len(pieces) <= 1:  # no separator, or only a trailing one ("…, …;"): try the next one
            continue
        parts = _pack(pieces, max_chars)
        if all(len(p) <= max_chars for p in parts):
            return parts
        # Some piece is still too long: split those pieces with the next separator.
        out: list[str] = []
        for part in parts:
            out.extend(split_long_sentence(part, max_chars) if len(part) > max_chars else [part])
        return out
    # A single "word" longer than max_chars: hard cut.
    return [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]


def _pack(pieces: list[str], max_chars: int) -> list[str]:
    """Greedily join pieces with spaces while the result stays within max_chars."""
    out: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}" if current else piece
        if current and len(candidate) > max_chars:
            out.append(current)
            current = piece
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def split_text(text: str, max_chars: int) -> list[str]:
    """Split already-normalized text into pieces of at most max_chars at sentence, then
    clause / word boundaries, packing short sentences together."""
    pieces: list[str] = []
    for sentence in split_sentences(text):
        pieces.extend(split_long_sentence(sentence, max_chars))
    return _pack(pieces, max_chars)


def chunk_paragraph(text: str, max_chars: int) -> list[str]:
    """Sentences of one paragraph, normalized for speech and packed into chunks."""
    pieces: list[str] = []
    for sentence in split_sentences(text):
        spoken = normalize_for_speech(sentence)
        if spoken:
            pieces.extend(split_long_sentence(spoken, max_chars))
    return _pack(pieces, max_chars)


def chunk_chapter(chapter: Chapter, max_chars: int) -> list[Chunk]:
    """Title chunk first, then the paragraphs' chunks in order. Empty text is never emitted."""
    chunks: list[Chunk] = []
    title = normalize_heading(chapter.title)
    if title:
        for piece in split_long_sentence(title, max_chars):
            chunks.append(Chunk(chapter.index, len(chunks), piece, 0, True, True))
    for paragraph in chapter.paragraphs:
        text = " ".join(s.text for s in paragraph.segments)
        pieces = chunk_paragraph(text, max_chars)
        for k, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    chapter.index,
                    len(chunks),
                    piece,
                    paragraph.index,
                    ends_paragraph=k == len(pieces) - 1,
                    is_title=False,
                )
            )
    return chunks
