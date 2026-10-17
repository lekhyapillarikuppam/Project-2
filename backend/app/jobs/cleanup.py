"""Storage cleanup (SPEC §8, §10). On startup `storage/jobs/` is emptied (jobs are not restored,
D18). Every CLEANUP_INTERVAL_MINUTES finished jobs past `expires_at` lose their folder and become
`expired` stubs; stubs are dropped EXPIRED_STUB_MINUTES later (then the id is JOB_NOT_FOUND).
"""

import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.jobs.models import FINISHED_STATUSES, Job, utc_now
from app.jobs.registry import JobRegistry

logger = logging.getLogger(__name__)


def wipe_jobs_folder(jobs_dir: Path) -> None:
    """Empty `storage/jobs/` (startup). Entries that cannot be removed are logged and left."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    for entry in jobs_dir.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as e:
            logger.warning("Startup cleanup could not remove %s: %s", entry, e)


def run_cleanup_once(registry: JobRegistry, settings: Settings, now: datetime | None = None) -> None:
    now = now or utc_now()
    stub_life = timedelta(minutes=settings.expired_stub_minutes)
    for job in registry.all():
        if job.status == "expired":
            if job.expired_at and now - job.expired_at >= stub_life:
                registry.remove(job.job_id)
                logger.info("Job %s: expired stub removed", job.job_id)
            continue
        if job.status not in FINISHED_STATUSES or now < job.expires_at:
            continue
        try:
            if job.folder.exists():
                shutil.rmtree(job.folder)
        except OSError as e:  # PermissionError on Windows while a file is being streamed
            logger.warning("Job %s: could not delete its folder yet (%s); retrying next run", job.job_id, e)
            continue

        def expire(j: Job) -> None:
            j.status = "expired"
            j.expired_at = now
            j.eta_seconds = None
            for chapter in j.chapters:
                chapter.path = None

        registry.update(job.job_id, expire)
        logger.info("Job %s: expired, files deleted", job.job_id)


class CleanupThread:
    """Runs `run_cleanup_once` every CLEANUP_INTERVAL_MINUTES in a daemon thread."""

    def __init__(self, registry: JobRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-cleanup", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        interval = self.settings.cleanup_interval_minutes * 60
        while not self._stop.wait(interval):
            try:
                run_cleanup_once(self.registry, self.settings)
            except Exception:  # noqa: BLE001 - the cleanup loop must keep running
                logger.exception("Cleanup run failed")
