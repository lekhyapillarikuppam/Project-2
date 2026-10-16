"""Run the Phase 1 pipeline on one document.

    python scripts\\extract.py <file> [--out document.json] [--dump]

Prints the detected structure. --out writes document.json; --dump writes testset/output/<stem>.txt
so a human can check the narration text for leaked headers, page numbers and broken words.
Exit code 2 when the document is rejected.
"""

import argparse
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.models import DocumentModel  # noqa: E402
from app.pipeline import PipelineError, run_pipeline  # noqa: E402

DEFAULT_DUMP_DIR = REPO_DIR / "testset" / "output"


def dump_text(document: DocumentModel) -> str:
    """Each chapter as `===== [n] Title (words) =====` and its paragraphs separated by blank lines."""
    parts = []
    for chapter in document.chapters:
        parts.append(f"===== [{chapter.index}] {chapter.title} ({chapter.word_count}) =====")
        for paragraph in chapter.paragraphs:
            parts.append(" ".join(s.text for s in paragraph.segments))
    return "\n\n".join(parts) + "\n"


def write_dump(document: DocumentModel, stem: str, out_dir: Path = DEFAULT_DUMP_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.txt"
    target.write_text(dump_text(document), encoding="utf-8")
    return target


def print_summary(document: DocumentModel) -> None:
    print(f"type:      {document.source_type}")
    print(f"pages:     {document.page_count if document.page_count is not None else '-'}")
    print(f"words:     {document.word_count}")
    print(f"structure: {document.structure_source}")
    print(f"warnings:  {'; '.join(document.warnings) if document.warnings else '-'}")
    print()
    print(f"{'#':>3}  {'title':<50} {'words':>6}  first 80 characters")
    for chapter in document.chapters:
        first = " ".join(
            s.text for p in chapter.paragraphs[:3] for s in p.segments
        )[:80]
        print(f"{chapter.index:>3}  {chapter.title[:50]:<50} {chapter.word_count:>6}  {first}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract chapters from a PDF or DOCX.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--out", type=Path, help="write the DocumentModel JSON here")
    parser.add_argument("--dump", action="store_true", help="write testset/output/<stem>.txt")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        document = run_pipeline(args.file, args.file.name, get_settings())
    except PipelineError as e:
        print(f"REJECTED {e.code}: {e.message}")
        sys.exit(2)

    print_summary(document)
    if args.out:
        args.out.write_text(document.model_dump_json(indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    if args.dump:
        print(f"\nwrote {write_dump(document, args.file.stem)}")


if __name__ == "__main__":
    main()
