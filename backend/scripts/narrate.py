"""Narrate a document into chapter MP3s (Phase 2 CLI).

    python scripts\\narrate.py <document.json | .pdf | .docx> --out <dir>
        [--chapters 1,3-4] [--engine edge|piper|fallback] [--fail-edge N] [--full]
    python scripts\\narrate.py --text-file <lines.txt> --out <dir> [--engine ...]

--fail-edge N (dev only) makes the edge engine raise on its first N calls, to show the per-chunk
fallback and the switch to Piper. --text-file narrates a text file as one chapter with one
paragraph per line and copies the result to <out>/<stem>_<engine>.mp3.
"""

import argparse
import asyncio
import ctypes
import json
import logging
import shutil
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.models import Chapter, DocumentModel, Paragraph, Segment  # noqa: E402
from app.pipeline import PipelineError, run_pipeline  # noqa: E402
from app.tts import audio  # noqa: E402
from app.tts.engines import EdgeTTSEngine, FallbackEngine, TTSEngine, build_engine  # noqa: E402
from app.tts.narrator import ProgressEvent, build_full_book, narrate_document  # noqa: E402

logger = logging.getLogger("narrate")


class FlakyEngine:
    """Dev-only wrapper: raises on the first `failures` calls, then delegates."""

    def __init__(self, inner: TTSEngine, failures: int) -> None:
        self.inner = inner
        self.name = inner.name
        self.remaining = failures

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        if self.remaining > 0:
            self.remaining -= 1
            raise ConnectionError(f"simulated {self.name} failure ({self.remaining} left)")
        await self.inner.synthesize(text, out_path, voice)


def parse_chapters(spec: str) -> set[int]:
    """'1,3-4' -> {1, 3, 4}."""
    selected: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            selected.update(range(int(a), int(b) + 1))
        elif part:
            selected.add(int(part))
    return selected


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process, from the standard library (no psutil)."""
    try:
        if sys.platform == "win32":

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            get_process = ctypes.windll.kernel32.GetCurrentProcess
            get_process.restype = ctypes.c_void_p  # a HANDLE is pointer-sized on 64-bit Windows
            get_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
            ok = get_info(get_process(), ctypes.byref(counters), counters.cb)
            return counters.PeakWorkingSetSize / 2**20 if ok else None
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux
    except Exception:  # noqa: BLE001 - memory reporting is informational only
        return None


def load_document(args: argparse.Namespace) -> DocumentModel:
    if args.text_file:
        lines = [ln.strip() for ln in args.text_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        paragraphs = [Paragraph(index=i, segments=[Segment(text=t)]) for i, t in enumerate(lines, start=1)]
        title = args.text_file.stem.replace("_", " ").capitalize()
        chapter = Chapter(index=1, title=title, word_count=sum(len(t.split()) for t in lines), paragraphs=paragraphs)
        return DocumentModel(
            source_filename=args.text_file.name,
            source_type="docx",  # the model only knows pdf/docx; this is a plain text sample
            page_count=None,
            word_count=chapter.word_count,
            structure_source="fallback",
            warnings=[],
            chapters=[chapter],
        )
    if args.source.suffix.lower() == ".json":
        return DocumentModel.model_validate_json(args.source.read_text(encoding="utf-8"))
    return run_pipeline(args.source, args.source.name, get_settings())


def make_engine(kind: str, fail_edge: int) -> TTSEngine:
    engine = build_engine(get_settings(), kind)
    if fail_edge:
        if isinstance(engine, FallbackEngine):
            engine.primary = FlakyEngine(engine.primary, fail_edge)
        elif isinstance(engine, EdgeTTSEngine):
            engine = FlakyEngine(engine, fail_edge)
    return engine


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Narrate a document into chapter MP3s.")
    parser.add_argument("source", type=Path, nargs="?", help="document.json, .pdf or .docx")
    parser.add_argument("--text-file", type=Path, help="narrate a text file, one paragraph per line")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--chapters", help="e.g. 1,3-4")
    parser.add_argument("--engine", choices=["edge", "piper", "fallback"], default="fallback")
    parser.add_argument("--fail-edge", type=int, default=0, metavar="N", help="dev: first N edge calls fail")
    parser.add_argument("--full", action="store_true", help="also build full.mp3")
    args = parser.parse_args()
    if not args.source and not args.text_file:
        parser.error("give a document or --text-file")

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        document = load_document(args)
    except PipelineError as e:
        print(f"REJECTED {e.code}: {e.message}")
        sys.exit(2)
    if args.chapters:
        wanted = parse_chapters(args.chapters)
        document = document.model_copy(update={"chapters": [c for c in document.chapters if c.index in wanted]})

    out_dir = args.out
    if args.text_file:
        out_dir = args.out / f"{args.text_file.stem}_{args.engine}"
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(args.engine, args.fail_edge)
    total = len(document.chapters)
    start = time.perf_counter()

    def on_progress(event: ProgressEvent) -> None:
        if event.kind not in ("chapter_ready", "chapter_failed"):
            return
        pct = 100 * event.chars_done / event.chars_total if event.chars_total else 100
        position = next(i for i, c in enumerate(document.chapters, 1) if c.index == event.chapter_index)
        status = (
            f"ready {_fmt_duration(event.duration_seconds or 0)} ({event.engine})"
            if event.kind == "chapter_ready"
            else f"FAILED: {event.error}"
        )
        print(
            f"[{position:>2}/{total}] chapter {event.chapter_index} {status} | "
            f"{pct:5.1f}% of text | elapsed {_fmt_duration(time.perf_counter() - start)}",
            flush=True,
        )

    result = asyncio.run(narrate_document(document, out_dir, engine, get_settings(), on_progress))
    (out_dir / "manifest.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    full_path = None
    if args.full:
        full_path = build_full_book(result, out_dir)

    print()
    print(f"{'#':>3}  {'title':<46} {'status':<8} {'duration':>9}  engine")
    for c in result.chapters:
        duration = _fmt_duration(c.duration_seconds) if c.duration_seconds else "-"
        print(f"{c.index:>3}  {c.title[:46]:<46} {c.status:<8} {duration:>9}  {c.engine_used or '-'}")
    peak = peak_memory_mb()
    print()
    print(f"total audio:   {_fmt_duration(result.audio_seconds)} ({result.audio_seconds:.1f} s)")
    print(f"wall time:     {_fmt_duration(result.wall_seconds)} ({result.wall_seconds:.1f} s)")
    print(f"chars/second:  {result.chars_per_second:.1f} ({result.chars_done} chars)")
    print(f"engine used:   {result.engine_used}")
    print(f"peak memory:   {f'{peak:.0f} MB' if peak else 'n/a'}")
    if full_path:
        print(f"full book:     {full_path} ({audio.probe(full_path).duration:.1f} s by ffprobe)")
    print(f"manifest:      {out_dir / 'manifest.json'}")
    if args.text_file:
        ready = [c for c in result.chapters if c.status == "ready" and c.path]
        if ready:
            target = args.out / f"{args.text_file.stem}_{args.engine}.mp3"
            shutil.copyfile(ready[0].path, target)
            print(f"sample:        {target}")


if __name__ == "__main__":
    main()
