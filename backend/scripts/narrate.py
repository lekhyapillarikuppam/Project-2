"""Narrate a document into chapter MP3s (Phase 2 CLI).

    python scripts\\narrate.py <document.json | .pdf | .docx> --out <dir>
        [--chapters 1,3-4] [--engine edge|piper|fallback] [--fail-edge N] [--full]
    (edge = edge-tts with retries and no fallback, as on the server; default from TTS_FALLBACK)
    python scripts\\narrate.py --text-file <lines.txt> --out <dir> [--engine ...]
    python scripts\\narrate.py <document> --mode characters [--chapters 7] [--out <dir>]

--mode characters (SPEC §15.6) segments and attributes the chosen chapters (ATTRIBUTION_METHOD,
LLM_MODEL), casts voices and narrates multi-voice; besides the chapter MP3s it writes
<stem>_attributed.md (every quote: paragraph, speaker, method, reason, voice) and <stem>_cast.md.
Its default --out is testset/output/characters/.

--fail-edge N (dev only) makes the edge engine raise on its first N calls, to show the per-chunk
fallback and the switch to Piper (or, with --engine edge, failed chapters and the stop). --text-file narrates a text file as one chapter with one
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
REPO_DIR = BACKEND_DIR.parent
CHARACTERS_OUT_DIR = REPO_DIR / "testset" / "output" / "characters"
sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, get_settings  # noqa: E402
from app.dialogue.attribute import (  # noqa: E402
    UNKNOWN,
    Attribution,
    Roster,
    apply_speakers,
    attribute_chapter,
    chapter_texts,
    rank_characters,
)
from app.dialogue.cast import Cast  # noqa: E402
from app.dialogue.llm import GroqClient, TokenLimiter  # noqa: E402
from app.dialogue.segment import QuoteFamily, detect_quote_family, find_quote_spans, segment_chapter  # noqa: E402
from app.dialogue.voices import VoiceCasting, cast_with_voices, new_casting  # noqa: E402
from app.dialogue.windows import collect_utterances  # noqa: E402
from app.models import Chapter, DocumentModel, Paragraph, Segment  # noqa: E402
from app.pipeline import PipelineError, run_pipeline  # noqa: E402
from app.tts import audio  # noqa: E402
from app.tts.engines import FallbackEngine, TTSEngine, build_engine  # noqa: E402
from app.tts.narrator import ProgressEvent, build_full_book, narrate_document, plan_narration  # noqa: E402

logger = logging.getLogger("narrate")
EXCERPT_CHARS = 70


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


def make_engine(kind: str | None, fail_edge: int) -> TTSEngine:
    engine = build_engine(get_settings(), kind)
    if fail_edge and isinstance(engine, FallbackEngine):
        engine.primary = FlakyEngine(engine.primary, fail_edge)
    return engine


def _excerpt(text: str) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1] + "…"


async def attribute_document(
    full: DocumentModel, chapters: list[Chapter], settings: Settings
) -> tuple[list[Chapter], dict[str, str], Cast, VoiceCasting, list[tuple[Chapter, dict[str, Attribution], str]]]:
    """Characters mode: rank the whole book, attribute the chosen chapters, cast voices.
    Returns the segmented chapters with speakers, speaker -> voice id, the cast, the casting, and
    per (unsegmented) chapter its attributions and the method used."""
    family = detect_quote_family(full)
    ranking, genders = rank_characters(full)
    method = settings.attribution_method
    client = None
    if method != "baseline":
        if settings.groq_api_key:
            limiter = TokenLimiter(settings.llm_tokens_per_minute, settings.llm_max_requests_per_day)
            client = GroqClient(settings.groq_api_key, settings.llm_model, limiter)
        else:
            logger.warning("GROQ_API_KEY is not set: using the baseline")
    roster = Roster()
    attributed: list[tuple[Chapter, dict[str, Attribution], str]] = []
    for chapter in chapters:
        texts = chapter_texts(chapter)
        spans, _ = find_quote_spans(texts, family)
        result, _ = await attribute_chapter(texts, spans, method, client, roster, settings.llm_window_words)
        used = "baseline" if client is None else result.method_used
        attributed.append((chapter, result.attributions, used))

    casting = new_casting(settings.edge_voice, settings.max_cast)
    cast = cast_with_voices(casting, ranking, genders, roster.reports(), roster.distinct)
    voices: dict[str, str] = {}
    out: list[Chapter] = []
    for chapter, attributions, _ in attributed:
        segmented, _ = segment_chapter(chapter, family)
        speakers: dict[str, str] = {}
        for uid, a in attributions.items():
            member = cast.resolve(a.speaker) if a.speaker != UNKNOWN else None
            if member is not None:
                speakers[uid] = member.name
                voices[member.name] = casting.voice_for(member).id
        out.append(apply_speakers(segmented, speakers))
    return out, voices, cast, casting, attributed


def write_character_reports(
    stem: str,
    out_dir: Path,
    family: QuoteFamily | None,
    chapters: list[tuple[Chapter, dict[str, Attribution], str]],
    cast: Cast,
    casting: VoiceCasting,
    settings: Settings,
) -> tuple[Path, Path]:
    method = settings.attribution_method
    model = settings.llm_model if method != "baseline" else "-"
    lines = [f"# {stem}: attributed quotes", "", f"Method `{method}`, model `{model}`.", ""]
    for chapter, attributions, used in chapters:
        texts = chapter_texts(chapter)
        spans, _ = find_quote_spans(texts, family)
        utterances = {u.id: u for u in collect_utterances(spans)}
        lines += [f"## Chapter {chapter.index}: {chapter.title} (attribution: {used})", "",
                  "| ¶ | Quote | Speaker | Method | Reason | Voice |", "|---|---|---|---|---|---|"]  # fmt: skip
        for uid, a in attributions.items():
            u = utterances[uid]
            quote = " … ".join(texts[p][s:e] for p, s, e in u.parts)
            member = cast.resolve(a.speaker) if a.speaker != UNKNOWN else None
            voice = casting.voice_for(member)
            speaker = member.name if member else UNKNOWN
            lines.append(f"| {u.first_paragraph} | {_excerpt(quote)} | {speaker} | {a.method} | {a.reason} | {voice.label} |")
        lines.append("")
    attributed_path = out_dir / f"{stem}_attributed.md"
    attributed_path.write_text("\n".join(lines), encoding="utf-8")

    rows = [f"# {stem}: cast", "", "| Name | Gender | Lines | Voice | Aliases |", "|---|---|---|---|---|",
            f"| Narrator | | | {casting.narrator.label} ({casting.narrator.id}) | |"]  # fmt: skip
    for m in cast.members:
        voice = casting.voice_for(m)
        rows.append(f"| {m.name} | {m.gender} | {m.lines} | {voice.label} ({voice.id}) | {', '.join(m.aliases)} |")
    cast_path = out_dir / f"{stem}_cast.md"
    cast_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return attributed_path, cast_path


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Narrate a document into chapter MP3s.")
    parser.add_argument("source", type=Path, nargs="?", help="document.json, .pdf or .docx")
    parser.add_argument("--text-file", type=Path, help="narrate a text file, one paragraph per line")
    parser.add_argument("--out", type=Path, help="output folder (characters mode: testset/output/characters)")
    parser.add_argument("--chapters", help="e.g. 1,3-4")
    parser.add_argument("--mode", choices=["single", "characters"], default="single", help="narration mode (SPEC §15)")
    parser.add_argument(
        "--engine",
        choices=["edge", "piper", "fallback"],
        help="edge: edge-tts with retries, no fallback; fallback: edge then Piper; piper: Piper only "
        "(default: from TTS_FALLBACK)",
    )
    parser.add_argument("--fail-edge", type=int, default=0, metavar="N", help="dev: first N edge calls fail")
    parser.add_argument("--full", action="store_true", help="also build full.mp3")
    args = parser.parse_args()
    if not args.source and not args.text_file:
        parser.error("give a document or --text-file")
    characters = args.mode == "characters"
    if characters and args.text_file:
        parser.error("--mode characters needs a document")
    if args.out is None:
        if not characters:
            parser.error("--out is required in single mode")
        args.out = CHARACTERS_OUT_DIR

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        document = load_document(args)
    except PipelineError as e:
        print(f"REJECTED {e.code}: {e.message}")
        sys.exit(2)
    full_document = document
    if args.chapters:
        wanted = parse_chapters(args.chapters)
        document = document.model_copy(update={"chapters": [c for c in document.chapters if c.index in wanted]})

    settings = get_settings()
    plan = None
    reports: tuple[Path, Path] | None = None
    if characters:
        chapters, voices, cast, casting, attributed = asyncio.run(
            attribute_document(full_document, document.chapters, settings)
        )
        document = document.model_copy(update={"chapters": chapters})
        plan = plan_narration(document, settings.tts_chunk_max_chars, voices)
        args.out.mkdir(parents=True, exist_ok=True)
        reports = write_character_reports(
            args.source.stem, args.out, detect_quote_family(full_document), attributed, cast, casting, settings
        )

    engine_label = args.engine or ("fallback" if settings.tts_fallback == "piper" else "edge")
    out_dir = args.out
    if args.text_file:
        out_dir = args.out / f"{args.text_file.stem}_{engine_label}"
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

    result = asyncio.run(narrate_document(document, out_dir, engine, settings, on_progress, plan))
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
    if reports:
        print(f"attributed:    {reports[0]}")
        print(f"cast:          {reports[1]}")
    if args.text_file:
        ready = [c for c in result.chapters if c.status == "ready" and c.path]
        if ready:
            target = args.out / f"{args.text_file.stem}_{engine_label}.mp3"
            shutil.copyfile(ready[0].path, target)
            print(f"sample:        {target}")


if __name__ == "__main__":
    main()
