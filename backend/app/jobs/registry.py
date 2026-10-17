"""In-memory job registry (SPEC §5): a dict guarded by a `threading.Lock`.

API handlers only get deep-copied snapshots. Every state change rewrites `job.json` in the job
folder for debugging; it is never read back (D18).
"""

import copy
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from app.jobs.models import ACTIVE_STATUSES, Job

logger = logging.getLogger(__name__)


class RegistryFull(Exception):
    """The queue limit was reached (SERVER_BUSY)."""


class JobRegistry:
    def __init__(self, max_queued: int) -> None:
        self.max_queued = max_queued
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # -- capacity ---------------------------------------------------------------------------
    def _queued_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == "queued")

    def has_capacity(self) -> bool:
        """False when `max_queued` jobs are already waiting (the running job is not counted, §4)."""
        with self._lock:
            return self._queued_count() < self.max_queued

    def add(self, job: Job) -> Job:
        """Register a new queued job, re-checking capacity atomically. Raises RegistryFull."""
        with self._lock:
            if self._queued_count() >= self.max_queued:
                raise RegistryFull
            self._jobs[job.job_id] = job
            self._persist(job)
            return copy.deepcopy(job)

    # -- reads ------------------------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def all(self) -> list[Job]:
        with self._lock:
            return [copy.deepcopy(j) for j in self._jobs.values()]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": self._queued_count(),
                "active": sum(1 for j in self._jobs.values() if j.status in ACTIVE_STATUSES),
            }

    # -- writes -----------------------------------------------------------------------------
    def update(self, job_id: str, change: Callable[[Job], None], persist: bool = True) -> Job | None:
        """Apply `change` to the job under the lock, rewrite job.json, return a snapshot."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            change(job)
            if persist:
                self._persist(job)
            return copy.deepcopy(job)

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    @staticmethod
    def _persist(job: Job) -> None:
        """Write job.json atomically (debug only). Never fatal."""
        if job.status == "expired" or not job.folder.exists():
            return
        target = job.folder / "job.json"
        tmp: Path = job.folder / ".job.json.tmp"
        try:
            tmp.write_text(json.dumps(job.to_public(), indent=2), encoding="utf-8")
            tmp.replace(target)
        except OSError as e:
            logger.warning("Could not write %s: %s", target, e)
