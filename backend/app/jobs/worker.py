"""The job worker (SPEC §5 threading model, D20): one daemon thread takes job ids from a FIFO
queue and runs each job end to end in its own event loop (`asyncio.run`). Blocking steps run via
`asyncio.to_thread`. Progress is written to the registry under its lock.
"""

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable

from app.config import Settings
from app.jobs.models import ChapterState, Job, error_body, utc_now
from app.jobs.registry import JobRegistry
from app.pipeline import PipelineError, run_pipeline
from app.tts.engines import TTSEngine
from app.tts.narrator import ProgressEvent, combine_engines, narrate_document, plan_narration

logger = logging.getLogger(__name__)

EngineFactory = Callable[[Settings], TTSEngine]


def final_status(chapters: list[ChapterState]) -> str:
    """done (all ready), partial (some ready, some failed), failed (none ready)."""
    ready = sum(1 for c in chapters if c.status == "ready")
    if ready and ready == len(chapters):
        return "done"
    return "partial" if ready else "failed"


def estimate_eta(chars_done: int, chars_total: int, elapsed: float) -> int | None:
    """Remaining characters / observed chars-per-second so far (SPEC §7.2); None before any."""
    if chars_done <= 0 or elapsed <= 0:
        return None
    rate = chars_done / elapsed
    return max(0, round((chars_total - chars_done) / rate))


class JobWorker:
    def __init__(self, registry: JobRegistry, settings: Settings, engine_factory: EngineFactory) -> None:
        self.registry = registry
        self.settings = settings
        self.engine_factory = engine_factory
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current_job_id: str | None = None

    # -- thread control ---------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to finish after the current job and wait up to `timeout` seconds.
        A job still running after that is abandoned with the (daemon) thread at process exit."""
        self._stop.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                logger.warning("Job worker still busy with %s at shutdown", self.current_job_id)

    def submit(self, job_id: str) -> None:
        self._queue.put(job_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            if job_id is None:
                break
            self.current_job_id = job_id
            try:
                asyncio.run(self.run_job(job_id))
            except Exception:  # noqa: BLE001 - one broken job must not stop the worker
                logger.exception("Job %s crashed", job_id)
                self._fail(job_id, error_body("TTS_FAILED", "Something went wrong while generating this audiobook."))
            finally:
                self.current_job_id = None

    # -- one job ----------------------------------------------------------------------------
    def _fail(self, job_id: str, error: dict[str, str]) -> None:
        def change(job: Job) -> None:
            if job.status in ("done", "partial", "failed", "expired"):
                return
            job.status = "failed"
            job.error = error
            job.eta_seconds = None
            job.finished_at = utc_now()
            for chapter in job.chapters:
                if chapter.status in ("pending", "generating"):
                    chapter.status = "failed"
                    chapter.error = error_body("TTS_FAILED")

        self.registry.update(job_id, change)

    async def run_job(self, job_id: str) -> None:
        settings = self.settings
        job = self.registry.update(job_id, lambda j: setattr(j, "status", "extracting"))
        if job is None:
            return  # removed meanwhile
        logger.info("Job %s: extracting %s", job_id, job.filename)

        try:
            document = await asyncio.to_thread(run_pipeline, job.input_path, job.filename, settings)
        except PipelineError as e:
            logger.info("Job %s failed: %s", job_id, e.code)
            self._fail(job_id, error_body(e.code, e.message))
            return
        except Exception:  # noqa: BLE001 - unexpected extraction failure
            logger.exception("Job %s: extraction crashed", job_id)
            self._fail(job_id, error_body("CORRUPT_FILE"))
            return

        await asyncio.to_thread(
            (job.folder / "document.json").write_text, document.model_dump_json(indent=2), encoding="utf-8"
        )
        plan = await asyncio.to_thread(plan_narration, document, settings.tts_chunk_max_chars)

        def start_synthesis(j: Job) -> None:
            j.warnings = list(document.warnings)
            j.chapters = [ChapterState(p.index, p.title) for p in plan]
            j.chars_total = sum(p.chars for p in plan)
            j.status = "synthesizing"
            j.synthesis_started = time.monotonic()

        self.registry.update(job_id, start_synthesis)
        logger.info("Job %s: synthesizing %d chapters", job_id, len(plan))

        engines: set[str] = set()

        def on_progress(event: ProgressEvent) -> None:
            if event.kind == "chunk_done" and event.engine:
                engines.add(event.engine)

            def change(j: Job) -> None:
                j.chars_done = event.chars_done
                j.engine_used = combine_engines(engines)
                chapter = j.chapter(event.chapter_index)
                if event.kind == "chapter_started" and chapter:
                    chapter.status = "generating"
                elif event.kind == "chapter_ready" and chapter:
                    chapter.status = "ready"
                    chapter.duration_seconds = event.duration_seconds
                    chapter.path = event.path
                elif event.kind == "chapter_failed" and chapter:
                    chapter.status = "failed"
                    chapter.error = error_body("TTS_FAILED")
                if j.synthesis_started is not None:
                    j.eta_seconds = estimate_eta(j.chars_done, j.chars_total, time.monotonic() - j.synthesis_started)

            self.registry.update(job_id, change)

        engine = self.engine_factory(settings)
        result = await narrate_document(document, job.folder, engine, settings, on_progress, plan)

        def finish(j: Job) -> None:
            j.status = final_status(j.chapters)
            j.chars_done = result.chars_done
            j.engine_used = result.engine_used
            j.eta_seconds = 0 if j.status != "failed" else None
            j.finished_at = utc_now()
            if j.status == "failed":
                j.error = error_body("TTS_FAILED")

        done = self.registry.update(job_id, finish)
        logger.info(
            "Job %s: %s in %.1f s (%.1f chars/s, engine %s)",
            job_id,
            done.status if done else "?",
            result.wall_seconds,
            result.chars_per_second,
            result.engine_used,
        )
