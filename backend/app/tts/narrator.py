"""Narration orchestrator (SPEC §6.7-6.8): chapters in order, chunks of one chapter in parallel,
then assembly into `chapters/ch_NNN.mp3`. Phase 3 calls `narrate_document` from the job worker.
"""

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.models import DocumentModel
from app.tts import audio
from app.tts.chunk import Chunk, chunk_chapter
from app.tts.engines import TTSEngine, synthesize_tracked

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
    failed_chunks: int = 0
    error: str | None = None


@dataclass
class NarrationResult:
    chapters: list[ChapterResult] = field(default_factory=list)
    engine_used: str | None = None
    chars_total: int = 0
    chars_done: int = 0
    wall_seconds: float = 0.0

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
) -> dict[int, str | None]:
    """Synthesize one chapter's chunks, at most `concurrency` at once. Returns seq -> engine name
    (None for chunks that failed on every engine)."""
    semaphore = asyncio.Semaphore(concurrency)
    results: dict[int, str | None] = {}
    done = 0

    async def one(chunk: Chunk) -> None:
        nonlocal done
        async with semaphore:
            path = work / f"{chunk.seq + 1:04d}.mp3"
            try:
                used = await synthesize_tracked(engine, chunk.text, path)
            except Exception as e:  # noqa: BLE001 - a failed chunk must not stop the chapter
                logger.error("Chunk %s of chapter %d failed on every engine: %s", path.name, chunk.chapter_index, e)
                results[chunk.seq] = None
                return
        results[chunk.seq] = used
        run.chars_done += len(chunk.text)
        run.engines.add(used)
        done += 1
        emit(
            ProgressEvent(
                "chunk_done",
                chunk.chapter_index,
                run.chars_done,
                run.chars_total,
                engine=used,
                chunk_seq=chunk.seq,
                chapter_chunks_done=done,
                chapter_chunks_total=len(chunks),
            )
        )

    await asyncio.gather(*(one(c) for c in chunks))
    return results


def _assemble_chapter(chunks: list[Chunk], ok: dict[int, str | None], work: Path, out_path: Path) -> float:
    """Join title, pauses and chunks into the chapter MP3 (blocking; run in a thread).
    Returns the chapter duration: the sum of the measured parts (SPEC §6.8)."""
    silences = work.parent.parent / "silence"
    title_pause = audio.silence_clip(audio.TITLE_PAUSE_MS, silences)
    paragraph_pause = audio.silence_clip(audio.PARAGRAPH_PAUSE_MS, silences)

    present = [c for c in chunks if ok.get(c.seq)]
    parts: list[Path] = []
    for i, chunk in enumerate(present):
        parts.append(work / f"{chunk.seq + 1:04d}.mp3")
        is_last = i == len(present) - 1
        if chunk.is_title and (is_last or not present[i + 1].is_title):
            parts.append(title_pause)
        elif chunk.ends_paragraph and not is_last:
            parts.append(paragraph_pause)

    infos = {p: audio.probe(p) for p in set(parts)}
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


async def _narrate_chapter(
    chapter_index: int,
    chunks: list[Chunk],
    result: ChapterResult,
    out_dir: Path,
    engine: TTSEngine,
    settings: Settings,
    run: _Run,
    emit: Callable[[ProgressEvent], None],
) -> None:
    work = out_dir / "work" / f"ch_{chapter_index:03d}"
    work.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "chapters" / chapter_filename(chapter_index)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.status = "generating"
    emit(ProgressEvent("chapter_started", chapter_index, run.chars_done, run.chars_total, chapter_chunks_total=len(chunks)))
    try:
        ok = await _synthesize_chunks(chunks, work, engine, settings.tts_concurrency, run, emit)
        used = {name for name in ok.values() if name}
        result.failed_chunks = sum(1 for name in ok.values() if name is None)
        if not used:
            raise RuntimeError("every chunk failed on every engine")
        if result.failed_chunks:
            logger.warning(
                "Chapter %d: %d of %d chunks failed on every engine and are left out",
                chapter_index,
                result.failed_chunks,
                len(chunks),
            )
        duration = await asyncio.to_thread(_assemble_chapter, chunks, ok, work, out_path)
    except Exception as e:  # noqa: BLE001 - a failed chapter must not stop the book
        result.status = "failed"
        result.error = str(e)
        logger.error("Chapter %d failed: %s", chapter_index, e)
        emit(ProgressEvent("chapter_failed", chapter_index, run.chars_done, run.chars_total, error=str(e)))
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
            chapter_index,
            run.chars_done,
            run.chars_total,
            engine=result.engine_used,
            chapter_chunks_done=len(chunks) - result.failed_chunks,
            chapter_chunks_total=len(chunks),
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
) -> NarrationResult:
    """Narrate every chapter of `doc` into `out_dir/chapters/`, strictly in order."""
    start = time.perf_counter()
    plan = [(c.index, c.title, chunk_chapter(c, settings.tts_chunk_max_chars)) for c in doc.chapters]
    run = _Run(chars_total=sum(len(ch.text) for _, _, chunks in plan for ch in chunks))
    result = NarrationResult(
        chapters=[
            ChapterResult(index, title, chars=sum(len(c.text) for c in chunks), chunks=len(chunks))
            for index, title, chunks in plan
        ],
        chars_total=run.chars_total,
    )

    def emit(event: ProgressEvent) -> None:
        if on_progress is not None:
            on_progress(event)

    for (index, _, chunks), chapter_result in zip(plan, result.chapters):
        if not chunks:
            chapter_result.status = "failed"
            chapter_result.error = "chapter has no text to narrate"
            emit(ProgressEvent("chapter_failed", index, run.chars_done, run.chars_total, error=chapter_result.error))
            continue
        await _narrate_chapter(index, chunks, chapter_result, out_dir, engine, settings, run, emit)

    shutil.rmtree(out_dir / "work", ignore_errors=True)
    result.chars_done = run.chars_done
    result.engine_used = combine_engines(run.engines)
    result.wall_seconds = time.perf_counter() - start
    return result


def build_full_book(result: NarrationResult, out_dir: Path) -> Path:
    """`full.mp3`: every ready chapter with 2 s between chapters. Cached after the first build.
    Blocking (ffmpeg); call it via asyncio.to_thread from async code."""
    target = out_dir / "full.mp3"
    if target.exists():
        return target
    ready = [c.path for c in result.chapters if c.status == "ready" and c.path]
    if not ready:
        raise ValueError("no ready chapters to join")
    pause = audio.silence_clip(audio.CHAPTER_PAUSE_MS, out_dir / "silence")
    parts: list[Path] = []
    for i, path in enumerate(ready):
        if i:
            parts.append(pause)
        parts.append(path)
    return audio.concat(parts, target)
