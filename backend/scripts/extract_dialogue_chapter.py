"""Write one chapter of a document as a dialogue-evaluation text file (SPEC §15.9, Phase 6.0).

    python scripts\\extract_dialogue_chapter.py <file> <name> (--title TEXT | --chapter-number N | --index N)

Runs the Phase 1 pipeline on <file> and writes testset_dialogue/text/<name>.json:
{"source", "chapter_index", "title", "paragraphs": ["…", …]} with the paragraph texts exactly as
the pipeline produces them (quote marks preserved). Only a short summary is printed, never the text.

    --title "A Mad Tea-Party"   chapter whose title contains this text (case-insensitive)
    --chapter-number 3          chapter whose title starts with "Chapter 3" / "Chapter III" / "Chapter Three"
    --index 7                   chapter number 7 in the pipeline's own numbering
Exactly one chapter must match. Exit code 2 when the document is rejected or the match fails.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.models import Chapter, DocumentModel  # noqa: E402
from app.pipeline import PipelineError, run_pipeline  # noqa: E402

DEFAULT_OUT_DIR = REPO_DIR / "testset_dialogue" / "text"

_ROMAN = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii", 8: "viii", 9: "ix", 10: "x",
          11: "xi", 12: "xii", 13: "xiii", 14: "xiv", 15: "xv", 16: "xvi", 17: "xvii", 18: "xviii",
          19: "xix", 20: "xx"}  # fmt: skip
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
          9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}  # fmt: skip


def chapter_number_pattern(n: int) -> re.Pattern[str]:
    """`Chapter 3`, `Chapter III`, `Chapter Three`, `III.` or `3.` at the start of a title."""
    forms = [str(n)] + [f for f in (_ROMAN.get(n), _WORDS.get(n)) if f]
    alt = "|".join(re.escape(f) for f in forms)
    return re.compile(rf"^\s*(?:chapter\s+)?(?:{alt})\b(?![\-–])", re.IGNORECASE)


def select_chapter(
    document: DocumentModel, title: str | None, number: int | None, index: int | None
) -> Chapter:
    if index is not None:
        matches = [c for c in document.chapters if c.index == index]
    elif number is not None:
        pattern = chapter_number_pattern(number)
        matches = [c for c in document.chapters if pattern.match(c.title)]
    else:
        assert title is not None
        matches = [c for c in document.chapters if title.casefold() in c.title.casefold()]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} chapters match (need exactly 1)")
    return matches[0]


def chapter_record(document_path: Path, chapter: Chapter) -> dict:
    try:
        source = document_path.resolve().relative_to(REPO_DIR).as_posix()
    except ValueError:
        source = document_path.name
    return {
        "source": source,
        "chapter_index": chapter.index,
        "title": chapter.title,
        "paragraphs": [" ".join(s.text for s in p.segments) for p in chapter.paragraphs],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract one chapter for dialogue labelling.")
    parser.add_argument("file", type=Path)
    parser.add_argument("name", help="output name, e.g. alice_ch07")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--title")
    which.add_argument("--chapter-number", type=int)
    which.add_argument("--index", type=int)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        document = run_pipeline(args.file, args.file.name, get_settings())
        chapter = select_chapter(document, args.title, args.chapter_number, args.index)
    except PipelineError as e:
        print(f"REJECTED {e.code}: {e.message}")
        sys.exit(2)
    except LookupError as e:
        print(f"NO MATCH: {e}")
        sys.exit(2)

    record = chapter_record(args.file, chapter)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    target = args.out_dir / f"{args.name}.json"
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    words = sum(len(p.split()) for p in record["paragraphs"])
    print(f"wrote {target}: chapter {chapter.index}, {len(record['paragraphs'])} paragraphs, {words} words")


if __name__ == "__main__":
    main()
