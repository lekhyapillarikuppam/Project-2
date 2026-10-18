"""Narration orchestrator (SPEC §6.7-6.8): chapters in order, chunks of one chapter in parallel,
then assembly into `chapters/ch_NNN.mp3`. The job worker calls `narrate_document`.

No silent gaps (D27): if any chunk of a chapter fails on every available engine, the chapter is
`failed` and its partial audio is discarded. If the engine reports `NarrationUnavailable` (D26),
the current and every remaining chapter are `failed` and narration stops.
"""

import asyncio
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.models import Chapter, DocumentModel
from app.tts import audio
from app.tts.chunk import Chunk, chunk_chapter
from app.tts.engines import NarrationUnavailable, TTSEngine, synthesize_tracked

logger = logging.getLogger(__name__)

DURATION_TOLERANCE_SECONDS = 1.0  # summed vs probed chapter duration (SPEC §6.8)

ChapterStatus = Literal["pending", "generating", "ready", "failed"]


@dataclass
class ProgressEvent:
    kind: Literal["chapter_started", "chunk_done", "chapter_ready", "chapter_failed"]
    chapter_index: int
    chars_done: int  # whole document, characters of normalized text synthesized so far
    chars_total: int
    engine: str | None = None  # engine of this chunk / engine(s) of this chapter
    chunk_seq: int | None = None
    chapter_chunks_done: int = 0
    chapter_chunks_total: int = 0
    path: Path | None = None  # chapter MP3 (chapter_ready)
    duration_seconds: float | None = None  # chapter duration (chapter_ready)
    error: str | None = None  # chapter_failed


@dataclass
class ChapterResult:
    index: int
    title: str
    status: ChapterStatus = "pending"
    path: Path | None = None
    duration_seconds: float | None = None
    engine_used: str | None = None  # edge | piper | mixed
    chars: int = 0
    chunks: int = 0
    error: str | None = None


@dataclass
class NarrationResult:
    chapters: list[ChapterResult] = field(default_factory=list)
    engine_used: str | None = None
    chars_total: int = 0
    chars_done: int = 0
    wall_seconds: float = 0.0
    unavailable: bool = False  # narration stopped early (NarrationUnavailable)

    @property
    def chars_per_second(self) -> float:
        return self.chars_done / self.wall_seconds if self.wall_seconds else 0.0

    @property
    def audio_seconds(self) -> float:
        return sum(c.duration_seconds or 0.0 for c in self.chapters if c.status == "ready")

    def to_dict(self) -> dict:
        data = asdict(self)
        for c in data["chapters"]:
            c["path"] = str(c["path"]) if c["path"] else None
        data["chars_per_second"] = round(self.chars_per_second, 1)
        data["audio_seconds"] = round(self.audio_seconds, 2)
        return data


def combine_engines(names: set[str]) -> str | None:
    if not names:
        return None
    return next(iter(names)) if len(names) == 1 else "mixed"


def chapter_filename(index: int) -> str:
    return f"ch_{index:03d}.mp3"


@dataclass(frozen=True)
class ChapterPlan:
    index: int
    title: str
    chunks: list[Chunk]

    @property
    def chars(self) -> int:
        return sum(len(c.text) for c in self.chunks)


def plan_narration(doc: DocumentModel, max_chars: int, voices: Mapping[str, str] | None = None) -> list[ChapterPlan]:
    """Chunk every chapter (normalized text). The worker uses it to know `chars_total` up front.
    `voices` (speaker -> voice id) is given in characters mode only (SPEC §15.6)."""
    return [plan_chapter(c, max_chars, voices) for c in doc.chapters]


def plan_chapter(chapter: Chapter, max_chars: int, voices: Mapping[str, str] | None = None) -> ChapterPlan:
    return ChapterPlan(chapter.index, chapter.title, chunk_chapter(chapter, max_chars, voices))


@dataclass
class _Run:
    """Mutable counters of one narration (local to a single narrate_document call)."""

    chars_total: int
    chars_done: int = 0
    engines: set[str] = field(default_factory=set)


async def _synthesize_chunks(
    chunks: list[Chunk],
    work: Path,
    engine: TTSEngine,
    concurrency: int,
    run: _Run,
    emit: Callable[[ProgressEvent], None],
) -> set[str]:
    """Synthesize one chapter's chunks, at most `concurrency` at once, and return the engines used.
    The first chunk that fails on every engine cancels the others and its error is raised."""
    semaphore = asyncio.Semaphore(concurrency)
    used: set[str] = set()
    done = 0

    async def one(chunk: Chunk) -> None:
        nonlocal done
        async with semaphore:
            path = work / f"{chunk.seq + 1:04d}.mp3"
            engine_name = await synthesize_tracked(engine, chunk.text, path, chunk.voice)
        used.add(engine_name)
        run.chars_done += len(chunk.text)
        run.engines.add(engine_name)
        done += 1
        emit(
            ProgressEvent(
                "chunk_done",
                chunk.chapter_index,
                run.chars_done,
                run.chars_total,
                engine=engine_name,
                chunk_seq=chunk.seq,
                chapter_chunks_done=done,
                chapter_chunks_total=len(chunks),
            )
        )

    tasks = [asyncio.create_task(one(c)) for c in chunks]
    finished, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    errors = [t.exception() for t in finished if not t.cancelled() and t.exception() is not None]
    if errors:
        # NarrationUnavailable wins: it stops the whole job, not just this chapter.
        raise next((e for e in errors if isinstance(e, NarrationUnavailable)), errors[0])
    return used


def _assemble_chapter(chunks: list[Chunk], work: Path, out_path: Path) -> float:
    """Join title, pauses and chunks into the chapter MP3 (blocking; run in a thread).
    Returns the chapter duration: the sum of the measured parts (SPEC §6.8)."""
    silences = work.parent.parent / "silence"
    title_pause = audio.silence_clip(audio.TITLE_PAUSE_MS, silences)
    paragraph_pause = audio.silence_clip(audio.PARAGRAPH_PAUSE_MS, silences)

    parts: list[Path] = []
    for i, chunk in enumerate(chunks):
        parts.append(work / f"{chunk.seq + 1:04d}.mp3")
        is_last = i == len(chunks) - 1
        if chunk.is_title and (is_last or not chunks[i + 1].is_title):
            parts.append(title_pause)
        elif chunk.ends_paragraph and not is_last:
            parts.append(paragraph_pause)
        elif not is_last and chunks[i + 1].speaker_change:
            parts.append(audio.silence_clip(audio.SPEAKER_PAUSE_MS, silences))

    # Part durations from the MP3 frame headers (no ffprobe per part); one ffprobe of the joined
    # chapter is the cross-check.
    infos = {p: audio.fast_probe(p) for p in set(parts)}
    summed = sum(infos[p].duration for p in parts)
    audio.concat(parts, out_path, infos)
    probed = audio.probe(out_path).duration
    if abs(probed - summed) > DURATION_TOLERANCE_SECONDS:
        logger.warning(
            "%s: joined file reports %.2f s but the parts sum to %.2f s; using the sum",
            out_path.name,
            probed,
            summed,
        )
    return summed


def _fail_chapter(result: ChapterResult, error: str, run: _Run, emit: Callable[[ProgressEvent], None]) -> None:
    result.status = "failed"
    result.error = error
    logger.error("Chapter %d failed: %s", result.index, error)
    emit(ProgressEvent("chapter_failed", result.index, run.chars_done, run.chars_total, error=error))


async def _narrate_chapter(
    plan: ChapterPlan,
    result: ChapterResult,
    out_dir: Path,
    engine: TTSEngine,
    settings: Settings,
    run: _Run,
    emit: Callable[[ProgressEvent], None],
) -> None:
    """Narrate one chapter. Any failure marks it failed and deletes its partial audio;
    NarrationUnavailable is re-raised after that so the caller stops."""
    work = out_dir / "work" / f"ch_{plan.index:03d}"
    work.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "chapters" / chapter_filename(plan.index)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.status = "generating"
    emit(
        ProgressEvent(
            "chapter_started", plan.index, run.chars_done, run.chars_total, chapter_chunks_total=len(plan.chunks)
        )
    )
    try:
        with audio.track_processes() as processes:
            started = time.perf_counter()
            used = await _synthesize_chunks(plan.chunks, work, engine, settings.tts_concurrency, run, emit)
            synthesized = time.perf_counter()
            duration = await asyncio.to_thread(_assemble_chapter, plan.chunks, work, out_path)
            assembled = time.perf_counter()
        logger.info(
            "Chapter %d timing: synthesis %.1f s (%d chunks, %d chars), assembly %.1f s; "
            "%d ffmpeg + %d ffprobe calls, %.1f s in subprocesses",
            plan.index,
            synthesized - started,
            len(plan.chunks),
            plan.chars,
            assembled - synthesized,
            processes.ffmpeg_calls,
            processes.ffprobe_calls,
            processes.seconds,
        )
    except Exception as e:  # noqa: BLE001 - a failed chapter must not stop the book (unless unavailable)
        out_path.unlink(missing_ok=True)
        _fail_chapter(result, str(e) or type(e).__name__, run, emit)
        if isinstance(e, NarrationUnavailable):
            raise
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)

    result.status = "ready"
    result.path = out_path
    result.duration_seconds = duration
    result.engine_used = combine_engines(used)
    emit(
        ProgressEvent(
            "chapter_ready",
            plan.index,
            run.chars_done,
            run.chars_total,
            engine=result.engine_used,
            chapter_chunks_done=len(plan.chunks),
            chapter_chunks_total=len(plan.chunks),
            path=out_path,
            duration_seconds=duration,
        )
    )


async def narrate_document(
    doc: DocumentModel,
    out_dir: Path,
    engine: TTSEngine,
    settings: Settings,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    plan: list[ChapterPlan] | None = None,
    prepare: Callable[[int], Awaitable[ChapterPlan]] | None = None,
) -> NarrationResult:
    """Narrate every chapter of `doc` into `out_dir/chapters/`, strictly in order. `plan` (from
    `plan_narration`) can be passed to avoid chunking the document twice. `prepare(position)`
    (characters mode) returns the final plan of a chapter just before it is narrated: attribution
    and voice casting happen there, lazily (D41); its chunks replace the planned ones."""
    start = time.perf_counter()
    if plan is None:
        plan = plan_narration(doc, settings.tts_chunk_max_chars)
    run = _Run(chars_total=sum(p.chars for p in plan))
    result = NarrationResult(
        chapters=[ChapterResult(p.index, p.title, chars=p.chars, chunks=len(p.chunks)) for p in plan],
        chars_total=run.chars_total,
    )

    def emit(event: ProgressEvent) -> None:
        if on_progress is not None:
            on_progress(event)

    for position, (chapter_plan, chapter_result) in enumerate(zip(plan, result.chapters)):
        if prepare is not None:
            prepared = await prepare(position)
            run.chars_total += prepared.chars - chapter_plan.chars
            result.chars_total = run.chars_total
            chapter_result.chars, chapter_result.chunks = prepared.chars, len(prepared.chunks)
            chapter_plan = prepared
        if not chapter_plan.chunks:
            _fail_chapter(chapter_result, "chapter has no text to narrate", run, emit)
            continue
        try:
            await _narrate_chapter(chapter_plan, chapter_result, out_dir, engine, settings, run, emit)
        except NarrationUnavailable as e:
            result.unavailable = True
            for later in result.chapters[position + 1 :]:
                _fail_chapter(later, str(e), run, emit)
            break

    shutil.rmtree(out_dir / "work", ignore_errors=True)
    result.chars_done = run.chars_done
    result.engine_used = combine_engines(run.engines)
    result.wall_seconds = time.perf_counter() - start
    return result


def build_full_book(result: NarrationResult, out_dir: Path) -> Path:
    """`full.mp3` from a narration result; see `join_full_book`."""
    return join_full_book([c.path for c in result.chapters if c.status == "ready" and c.path], out_dir)


def join_full_book(chapter_paths: list[Path], out_dir: Path) -> Path:
    """`full.mp3`: the given chapter files with 2 s between chapters. Cached after the first build.
    Blocking (ffmpeg); call it from a thread in async code."""
    target = out_dir / "full.mp3"
    if target.exists():
        return target
    if not chapter_paths:
        raise ValueError("no ready chapters to join")
    pause = audio.silence_clip(audio.CHAPTER_PAUSE_MS, out_dir / "silence")
    parts: list[Path] = []
    for i, path in enumerate(chapter_paths):
        if i:
            parts.append(pause)
        parts.append(path)
    return audio.concat(parts, target)
