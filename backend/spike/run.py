"""Phase 2b spike measurements. Each subcommand runs in its own process so its peak memory is
isolated, and prints one JSON object on stdout (logs go to stderr).

    python -m spike.run memory
    python -m spike.run edge
    python -m spike.run piper --model models/en_US-lessac-low.onnx [--arena on|off] [--max-chars N]
    python -m spike.run extract [--pages 200]
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.config import get_settings
from app.models import Chapter, DocumentModel, Paragraph, Segment
from app.tts.engines import EdgeTTSEngine, PiperEngine, TTSEngine
from app.tts.narrator import narrate_document

ASSET = Path(__file__).resolve().parent / "assets" / "alice_ch1.txt"
SAMPLE_CHARS = 3000
CGROUP = Path("/sys/fs/cgroup")

logger = logging.getLogger("spike")


def peak_mb() -> float | None:
    """Peak resident memory of this process in MB (ru_maxrss is KB on Linux; on Windows, for
    local comparison runs, the peak working set)."""
    if sys.platform != "win32":
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    import ctypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (n, ctypes.c_size_t)
            for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                      "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")
        ]  # fmt: skip

    counters = Counters(cb=ctypes.sizeof(Counters))
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    get_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not get_info(get_process(), ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / 2**20, 1)


def emit(data: dict) -> None:
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def asset_parts() -> tuple[str, list[str]]:
    """The chapter title and its paragraphs (the asset is blank-line separated, title first)."""
    blocks = [b.strip() for b in ASSET.read_text(encoding="utf-8").split("\n\n") if b.strip()]
    return blocks[0], blocks[1:]


def sample_document() -> DocumentModel:
    """Whole paragraphs from the start of chapter 1 until about SAMPLE_CHARS characters."""
    title, paragraphs = asset_parts()
    chosen: list[str] = []
    for p in paragraphs:
        chosen.append(p)
        if sum(len(c) for c in chosen) >= SAMPLE_CHARS:
            break
    words = sum(len(p.split()) for p in chosen)
    chapter = Chapter(
        index=1,
        title=title,
        word_count=words,
        paragraphs=[Paragraph(index=i, segments=[Segment(text=p)]) for i, p in enumerate(chosen, 1)],
    )
    return DocumentModel(
        source_filename=ASSET.name,
        source_type="docx",  # the model only knows pdf/docx; this is a plain text sample
        page_count=None,
        word_count=words,
        structure_source="fallback",
        warnings=[],
        chapters=[chapter],
    )


def narrate(engine: TTSEngine, extra: dict) -> int:
    """Narrate the sample with the real chunker and narrator; emit the measurement."""
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="spike_") as tmp:
        start = time.perf_counter()
        result = asyncio.run(narrate_document(sample_document(), Path(tmp), engine, settings))
        seconds = time.perf_counter() - start
    chapter = result.chapters[0]
    ok = chapter.status == "ready" and chapter.failed_chunks == 0
    error = chapter.error or (f"{chapter.failed_chunks} of {chapter.chunks} chunks failed" if not ok else None)
    emit(
        {
            "ok": ok,
            "error": error,
            "chars": result.chars_done,
            "seconds": round(seconds, 2),
            "chars_per_sec": round(result.chars_done / seconds, 1) if seconds else 0.0,
            "audio_seconds": round(result.audio_seconds, 1),
            "peak_mb": peak_mb(),
            **extra,
        }
    )
    return 0 if ok else 1


def cmd_edge(_: argparse.Namespace) -> int:
    settings = get_settings()
    return narrate(EdgeTTSEngine(settings.edge_voice), {"voice": settings.edge_voice})


def cmd_piper(args: argparse.Namespace) -> int:
    engine = PiperEngine(args.model, disable_arena=args.arena == "off", max_chars=args.max_chars)
    start = time.perf_counter()
    engine._get_voice()  # load first so the synthesis rate excludes the one-off model load
    load_seconds = time.perf_counter() - start
    extra = {
        "model": args.model.name,
        "arena": args.arena,
        "max_chars": args.max_chars,
        "load_seconds": round(load_seconds, 2),
        "peak_mb_after_load": peak_mb(),
    }
    return narrate(engine, extra)


def build_pdf(out: Path, pages: int) -> int:
    """An A5, 11 pt book made of chapter 1 repeated as 'Chapter N', cut at exactly `pages` pages."""
    import pymupdf

    title, paragraphs = asset_parts()
    name = title.split(":", 1)[1].strip()
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    html = "".join(f"<h1>Chapter {n}: {name}</h1>{body}" for n in range(1, 60))
    css = "body {font-family: serif; font-size: 11pt;} h1 {font-size: 16pt;} p {margin: 0 0 6pt 0;}"
    story = pymupdf.Story(html=html, user_css=css)
    page_rect = pymupdf.paper_rect("a5")
    where = page_rect + (50, 50, -50, -50)
    writer = pymupdf.DocumentWriter(str(out))
    count = 0
    more = True
    while more and count < pages:
        device = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        count += 1
    writer.close()
    return count


def cmd_build_pdf(args: argparse.Namespace) -> int:
    emit({"pages": build_pdf(args.out, args.pages)})
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from app.pipeline import run_pipeline

    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="spike_") as tmp:
        pdf = Path(tmp) / "spike_book.pdf"
        # Build the PDF in a child process so this process's peak is the pipeline's alone.
        built = subprocess.run(
            [sys.executable, "-m", "spike.run", "build-pdf", "--out", str(pdf), "--pages", str(args.pages)],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
        pages = json.loads(built.stdout)["pages"]
        size_mb = round(pdf.stat().st_size / 2**20, 2)
        start = time.perf_counter()
        try:
            document = run_pipeline(pdf, pdf.name, settings)
        except Exception as e:  # noqa: BLE001 - report any failure as data
            emit({"ok": False, "error": f"{type(e).__name__}: {e}", "pages": pages, "peak_mb": peak_mb()})
            return 1
        seconds = time.perf_counter() - start
    emit(
        {
            "ok": True,
            "pages": pages,
            "pdf_mb": size_mb,
            "words": document.word_count,
            "chapters": len(document.chapters),
            "structure_source": document.structure_source,
            "seconds": round(seconds, 2),
            "peak_mb": peak_mb(),
        }
    )
    return 0


def _read_int(path: Path) -> int | str | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else raw  # cgroup v2 writes "max" for no limit


def _mb(value: int | str | None) -> float | str | None:
    return round(value / 2**20, 1) if isinstance(value, int) else value


def cmd_memory(_: argparse.Namespace) -> int:
    v2 = CGROUP / "memory.max"
    if v2.exists():
        limit, usage = _read_int(v2), _read_int(CGROUP / "memory.current")
        peak = _read_int(CGROUP / "memory.peak")
        source = "cgroup v2"
    else:
        v1 = CGROUP / "memory"
        limit, usage = _read_int(v1 / "memory.limit_in_bytes"), _read_int(v1 / "memory.usage_in_bytes")
        peak = _read_int(v1 / "memory.max_usage_in_bytes")
        source = "cgroup v1" if limit is not None else None
    emit(
        {
            "ok": source is not None,
            "source": source,
            "limit_mb": _mb(limit),
            "usage_mb": _mb(usage),
            "container_peak_mb": _mb(peak),
            "this_process_peak_mb": peak_mb(),
        }
    )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Phase 2b Render spike measurements.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("memory").set_defaults(func=cmd_memory)
    sub.add_parser("edge").set_defaults(func=cmd_edge)
    piper = sub.add_parser("piper")
    piper.add_argument("--model", type=Path, required=True)
    piper.add_argument("--arena", choices=["on", "off"], default="off")
    piper.add_argument("--max-chars", type=int, default=get_settings().piper_max_chars)
    piper.set_defaults(func=cmd_piper)
    extract = sub.add_parser("extract")
    extract.add_argument("--pages", type=int, default=200)
    extract.set_defaults(func=cmd_extract)
    build = sub.add_parser("build-pdf")  # internal: used by `extract`
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--pages", type=int, default=200)
    build.set_defaults(func=cmd_build_pdf)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
