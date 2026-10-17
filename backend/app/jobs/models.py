"""Job state (SPEC §7.2, §8). Plain dataclasses, mutated only under the registry lock."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.pipeline.errors import ERROR_MESSAGES

JobStatus = Literal["queued", "extracting", "synthesizing", "done", "partial", "failed", "expired"]
ChapterStatus = Literal["pending", "generating", "ready", "failed"]

ACTIVE_STATUSES: frozenset[str] = frozenset({"extracting", "synthesizing"})
FINISHED_STATUSES: frozenset[str] = frozenset({"done", "partial", "failed"})


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def error_body(code: str, message: str | None = None) -> dict[str, str]:
    return {"code": code, "message": message or ERROR_MESSAGES.get(code, code)}


@dataclass
class ChapterState:
    index: int
    title: str
    status: ChapterStatus = "pending"
    duration_seconds: float | None = None
    error: dict[str, str] | None = None  # {"code": "TTS_FAILED", ...} when failed
    path: Path | None = None  # internal: the chapter MP3 once ready

    def to_public(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 2) if self.duration_seconds is not None else None,
            "error": self.error,
        }


@dataclass
class Job:
    job_id: str
    filename: str
    folder: Path
    input_path: Path
    created_at: datetime
    expires_at: datetime
    status: JobStatus = "queued"
    error: dict[str, str] | None = None
    warnings: list[str] = field(default_factory=list)
    chapters: list[ChapterState] = field(default_factory=list)
    chars_total: int = 0
    chars_done: int = 0
    eta_seconds: int | None = None
    engine_used: str | None = None
    # internal
    synthesis_started: float | None = None  # time.monotonic() when narration began
    finished_at: datetime | None = None
    expired_at: datetime | None = None

    @property
    def chapters_done(self) -> int:
        """Chapters that are finished (ready or failed)."""
        return sum(1 for c in self.chapters if c.status in ("ready", "failed"))

    @property
    def book_name(self) -> str:
        return Path(self.filename).stem

    def chapter(self, n: int) -> ChapterState | None:
        return next((c for c in self.chapters if c.index == n), None)

    def to_public(self) -> dict:
        """The §7.2 job object."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": iso(self.created_at),
            "expires_at": iso(self.expires_at),
            "filename": self.filename,
            "error": self.error,
            "warnings": list(self.warnings),
            "progress": {
                "chapters_total": len(self.chapters),
                "chapters_done": self.chapters_done,
                "chars_total": self.chars_total,
                "chars_done": self.chars_done,
                "eta_seconds": self.eta_seconds,
            },
            "chapters": [c.to_public() for c in self.chapters],
            "engine_used": self.engine_used,
        }
