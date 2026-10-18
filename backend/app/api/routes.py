"""HTTP endpoints (SPEC §9). Everything here is quick: heavy work runs in the job worker thread,
and the remaining blocking calls (validation, building full.mp3) run in the threadpool."""

import logging
import re
import shutil
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.concurrency import run_in_threadpool

from app.api.errors import FULL_BOOK_NOT_READY_MESSAGE, ApiError
from app.api.files import chapter_filename, content_disposition, full_book_filename
from app.api.ratelimit import UploadRateLimiter, client_ip
from app.config import Settings
from app.jobs import Job, JobRegistry, RegistryFull
from app.jobs.models import utc_now
from app.pipeline.errors import PipelineError
from app.pipeline.validate import validate_file
from app.tts import narrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

UPLOAD_PIECE_BYTES = 1024 * 1024  # the body is written to disk in 1 MB pieces (§6.1 step 1)
# Content-Length covers the multipart framing too (boundaries, part headers); allow for it so a
# file of exactly the maximum size is not rejected. The streamed byte count is exact.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
FIELD_MAX_BYTES = 100  # small form fields (narration_mode); longer values are cut
NARRATION_MODES = ("single", "characters")
CHARACTERS_UNAVAILABLE_MESSAGE = "Character voices aren't available on this server. Choose single narrator."


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request) -> JobRegistry:
    return request.app.state.registry


# -- upload ---------------------------------------------------------------------------------


class _TooLarge(Exception):
    pass


class _UploadSink:
    """python-multipart callbacks: writes the `file` part straight to disk, counting bytes; other
    parts are small form fields kept in `fields` (at most FIELD_MAX_BYTES each)."""

    def __init__(self, folder: Path, max_bytes: int) -> None:
        self.folder = folder
        self.max_bytes = max_bytes
        self.path = folder / "input.part"
        self.filename: str | None = None
        self.size = 0
        self._headers: dict[bytes, bytes] = {}
        self._field = b""
        self._value = b""
        self._file = None
        self._done = False
        self.fields: dict[str, str] = {}
        self._field_name: str | None = None
        self._field_value = b""

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_part_end": self._part_end,
        }

    def _part_begin(self) -> None:
        self._headers = {}

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def _header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def _header_end(self) -> None:
        self._headers[self._field.strip().lower()] = self._value.strip()
        self._field = self._value = b""

    def _headers_finished(self) -> None:
        _, params = parse_options_header(self._headers.get(b"content-disposition", b""))
        name, filename = params.get(b"name"), params.get(b"filename")
        if name == b"file" and filename is not None and not self._done:
            raw = filename.decode("utf-8", errors="replace").replace("\\", "/")
            self.filename = raw.rsplit("/", 1)[-1].strip()
            self._file = self.path.open("wb", buffering=UPLOAD_PIECE_BYTES)
        elif name and filename is None:
            self._field_name, self._field_value = name.decode("utf-8", errors="replace"), b""

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if self._file is None:
            if self._field_name is not None and len(self._field_value) < FIELD_MAX_BYTES:
                self._field_value += data[start:end][: FIELD_MAX_BYTES - len(self._field_value)]
            return
        self.size += end - start
        if self.size > self.max_bytes:
            raise _TooLarge
        self._file.write(data[start:end])

    def _part_end(self) -> None:
        if self._field_name is not None:
            self.fields[self._field_name] = self._field_value.decode("utf-8", errors="replace").strip()
            self._field_name = None
        if self._file is not None:
            self._file.close()
            self._file = None
            self._done = True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def received(self) -> bool:
        return self._done and bool(self.filename)


async def _receive_upload(request: Request, folder: Path, max_bytes: int) -> tuple[Path, str, dict[str, str]]:
    """Stream the multipart body to `folder/input.<ext>`; return (path, original filename, other
    form fields)."""
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise ApiError("MISSING_FILE")
    sink = _UploadSink(folder, max_bytes)
    parser = MultipartParser(boundary, sink.callbacks())
    try:
        async for piece in request.stream():
            parser.write(piece)
        parser.finalize()
    except _TooLarge:
        raise ApiError("FILE_TOO_LARGE") from None
    finally:
        sink.close()
    if not sink.received or not sink.filename:
        raise ApiError("MISSING_FILE")
    suffix = Path(sink.filename).suffix.lower()
    target = folder / f"input{suffix if _SAFE_SUFFIX.fullmatch(suffix) else ''}"
    sink.path.replace(target)
    return target, sink.filename, sink.fields


def _narration_mode(fields: dict[str, str], settings: Settings) -> str:
    """SPEC §15.1: `single` (default) or `characters`, which needs a configured LLM key."""
    mode = fields.get("narration_mode") or "single"
    if mode not in NARRATION_MODES:
        raise ApiError("BAD_REQUEST", "narration_mode must be 'single' or 'characters'.")
    if mode == "characters" and not settings.groq_api_key:
        raise ApiError("BAD_REQUEST", CHARACTERS_UNAVAILABLE_MESSAGE)
    return mode


@router.post("/upload", status_code=202)
async def upload(request: Request) -> dict:
    settings = _settings(request)
    registry = _registry(request)

    # D30: counted first, so rejected uploads count too; over the limit the body is never read.
    limiter: UploadRateLimiter = request.app.state.upload_limiter
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    retry_after = limiter.hit(ip)
    if retry_after is not None:
        logger.warning("Upload from %s rate-limited (retry in %d s)", ip, retry_after)
        raise ApiError("RATE_LIMITED", headers={"Retry-After": str(retry_after)})

    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_file_bytes + MULTIPART_OVERHEAD_BYTES:
        raise ApiError("FILE_TOO_LARGE")  # before reading the body
    if not registry.has_capacity():
        raise ApiError("SERVER_BUSY")

    job_id = str(uuid.uuid4())
    folder = settings.storage_path / "jobs" / job_id
    folder.mkdir(parents=True)
    try:
        path, filename, fields = await _receive_upload(request, folder, settings.max_file_bytes)
        mode = _narration_mode(fields, settings)
        try:
            validation = await run_in_threadpool(validate_file, path, filename, settings)
        except PipelineError as e:
            raise ApiError(e.code, e.message) from None
        created = utc_now()
        job = Job(
            job_id=job_id,
            filename=filename,
            folder=folder,
            input_path=path,
            created_at=created,
            expires_at=created + timedelta(hours=settings.retention_hours),
            warnings=list(validation.warnings),
            narration_mode=mode,  # type: ignore[arg-type]
        )
        try:
            registry.add(job)
        except RegistryFull:
            raise ApiError("SERVER_BUSY") from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # rejected uploads leave nothing behind
        raise

    request.app.state.worker.submit(job_id)
    logger.info("Job %s queued: %s (%s)", job_id, filename, mode)
    return {"job_id": job_id, "status": "queued"}


# -- reads ----------------------------------------------------------------------------------


def _job_or_error(request: Request, job_id: str) -> Job:
    job = _registry(request).get(job_id)
    if job is None:
        raise ApiError("JOB_NOT_FOUND")
    if job.status == "expired":
        raise ApiError("JOB_EXPIRED")
    return job


def _ready_chapter_path(job: Job, n: int) -> Path:
    chapter = job.chapter(n)
    if chapter is None:
        if job.status in ("queued", "extracting"):
            raise ApiError("CHAPTER_NOT_READY")  # chapters are not known yet
        raise ApiError("CHAPTER_NOT_FOUND")
    if chapter.status != "ready" or chapter.path is None or not chapter.path.exists():
        raise ApiError("CHAPTER_NOT_READY")
    return chapter.path


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    return _job_or_error(request, job_id).to_public()


@router.get("/jobs/{job_id}/chapters/{n}/audio")
def chapter_audio(job_id: str, n: int, request: Request) -> FileResponse:
    """Chapter MP3 for the player. Starlette's FileResponse answers `Range` requests (206)."""
    path = _ready_chapter_path(_job_or_error(request, job_id), n)
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/jobs/{job_id}/download/full")
async def download_full(job_id: str, request: Request) -> FileResponse:
    job = _job_or_error(request, job_id)
    if job.status not in ("done", "partial"):
        raise ApiError("CHAPTER_NOT_READY", FULL_BOOK_NOT_READY_MESSAGE)
    paths = [c.path for c in job.chapters if c.status == "ready" and c.path]
    builder: FullBookBuilder = request.app.state.full_books
    path = await run_in_threadpool(builder.build, job.job_id, paths, job.folder)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Content-Disposition": content_disposition(full_book_filename(job.book_name))},
    )


@router.get("/jobs/{job_id}/download/{n}")
def download_chapter(job_id: str, n: int, request: Request) -> FileResponse:
    job = _job_or_error(request, job_id)
    path = _ready_chapter_path(job, n)
    chapter = job.chapter(n)
    name = chapter_filename(job.book_name, n, chapter.title if chapter else "")
    return FileResponse(path, media_type="audio/mpeg", headers={"Content-Disposition": content_disposition(name)})


class FullBookBuilder:
    """Builds `full.mp3` lazily, at most once per job at a time (a per-job lock); then cached."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def build(self, job_id: str, chapter_paths: list[Path], folder: Path) -> Path:
        with self._guard:
            lock = self._locks.setdefault(job_id, threading.Lock())
        with lock:
            return narrator.join_full_book(chapter_paths, folder)

# -- service --------------------------------------------------------------------------------


@router.get("/health")
def health(request: Request) -> dict:
    return {"status": "ok", "version": _settings(request).version}


@router.get("/config")
def public_config(request: Request) -> dict:
    settings = _settings(request)
    return {
        "max_file_mb": settings.max_file_mb,
        "max_pdf_pages": settings.max_pdf_pages,
        "max_words": settings.max_words,
        "accepted_types": [".pdf", ".docx"],
        "retention_hours": settings.retention_hours,
        "tts_fallback": settings.tts_fallback,
        "character_voices_available": bool(settings.groq_api_key),
    }
