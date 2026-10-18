"""Build the static sample audiobook for the frontend (D22, SPEC §11).

    python scripts\\make_sample.py [--source ..\\testset\\docs\\06_no_styles.docx] [--chapters 3]

Runs the normal pipeline and edge-tts narration on the first chapters of a public-domain test
document (by default the first 3 stories of 06_no_styles.docx), then writes to
frontend/public/sample/: ch_001.mp3 …, full.mp3 and job.json (a §7.2 job object, status "done").
"""

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.pipeline import PipelineError, run_pipeline  # noqa: E402
from app.tts.engines import build_engine  # noqa: E402
from app.tts.narrator import build_full_book, narrate_document  # noqa: E402

logger = logging.getLogger("make_sample")

DEFAULT_SOURCE = BACKEND_DIR.parent / "testset" / "docs" / "06_no_styles.docx"
OUT_DIR = BACKEND_DIR.parent / "frontend" / "public" / "sample"
MAX_TOTAL_BYTES = 10 * 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser(description="Build frontend/public/sample/ from a test document.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--chapters", type=int, default=3, help="use the first N chapters")
    parser.add_argument("--filename", default="Three Little Stories.docx", help="book filename shown in the UI")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    try:
        document = run_pipeline(args.source, args.source.name, settings)
    except PipelineError as e:
        sys.exit(f"REJECTED {e.code}: {e.message}")
    document = document.model_copy(update={"chapters": document.chapters[: args.chapters]})

    with tempfile.TemporaryDirectory(prefix="sample_") as tmp:
        work = Path(tmp) / "job"
        work.mkdir()
        result = asyncio.run(narrate_document(document, work, build_engine(settings, "edge"), settings))
        failed = [c.index for c in result.chapters if c.status != "ready"]
        if failed:
            sys.exit(f"chapters {failed} failed; not writing a partial sample")
        full = build_full_book(result, work)

        if OUT_DIR.exists():
            shutil.rmtree(OUT_DIR)
        OUT_DIR.mkdir(parents=True)
        chapters = []
        for position, c in enumerate(result.chapters, start=1):
            shutil.copyfile(c.path, OUT_DIR / f"ch_{position:03d}.mp3")
            chapters.append(
                {
                    "index": position,
                    "title": c.title,
                    "status": "ready",
                    "duration_seconds": round(c.duration_seconds, 2),
                    "error": None,
                }
            )
        shutil.copyfile(full, OUT_DIR / "full.mp3")

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    job = {
        "job_id": "sample",
        "status": "done",
        "created_at": now,
        "expires_at": now,  # not used by the UI; the sample never expires
        "filename": args.filename,
        "error": None,
        "warnings": [],
        "progress": {
            "chapters_total": len(chapters),
            "chapters_done": len(chapters),
            "chars_total": result.chars_total,
            "chars_done": result.chars_done,
            "eta_seconds": 0,
        },
        "chapters": chapters,
        "engine_used": result.engine_used,
    }
    (OUT_DIR / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = sum(f.stat().st_size for f in OUT_DIR.iterdir())
    for f in sorted(OUT_DIR.iterdir()):
        print(f"{f.name:<12} {f.stat().st_size:>10,} bytes")
    print(f"{'total':<12} {total:>10,} bytes")
    if total > MAX_TOTAL_BYTES:
        sys.exit(f"sample is {total:,} bytes, over the {MAX_TOTAL_BYTES:,}-byte budget: use fewer chapters")


if __name__ == "__main__":
    main()
