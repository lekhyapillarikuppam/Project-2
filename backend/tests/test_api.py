"""API tests with a fake TTS engine (no network). Needs ffmpeg for assembly."""

import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs.registry import JobRegistry
from app.main import create_app
from app.tts import audio
from app.tts.engines import FallbackEngine
from conftest import GateEngine, make_docx, no_sleep, wait_for

FINISHED = ("done", "partial", "failed")


def edge_only(engine: GateEngine):  # noqa: ANN201
    """The Render setup (D26): edge with retries and no fallback, around the fake engine."""
    return lambda s: FallbackEngine(
        engine, None, max_retries=s.tts_max_retries, switch_after=s.tts_fallback_after_consecutive_failures,
        sleep=no_sleep,
    )  # fmt: skip


@pytest.fixture
def engine(mp3_clip: Path, gate: threading.Event) -> GateEngine:
    return GateEngine(mp3_clip, gate=gate)


@pytest.fixture
def client(api_settings: Settings, engine: GateEngine, gate: threading.Event) -> Iterator[TestClient]:
    app = create_app(api_settings, edge_only(engine))
    with TestClient(app) as c:
        yield c
    gate.set()


def upload(client: TestClient, path: Path, name: str | None = None):  # noqa: ANN201
    with path.open("rb") as fh:
        return client.post("/api/upload", files={"file": (name or path.name, fh, "application/octet-stream")})


def wait_done(client: TestClient, job_id: str) -> dict:
    result: dict = {}

    def finished() -> bool:
        result.update(client.get(f"/api/jobs/{job_id}").json())
        return result.get("status") in FINISHED

    wait_for(finished)
    return result


def jobs_dir(settings: Settings) -> Path:
    return settings.storage_path / "jobs"


# -- happy path ------------------------------------------------------------------------------


def test_upload_to_done_with_progress(
    client: TestClient, tmp_path: Path, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    history: list[dict] = []
    original = JobRegistry._persist

    def spy(job):  # noqa: ANN001, ANN202
        history.append(job.to_public())
        original(job)

    monkeypatch.setattr(JobRegistry, "_persist", staticmethod(spy))
    r = upload(client, make_docx(tmp_path / "My Book.docx"))
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued" and body["job_id"]
    gate.set()
    final = wait_done(client, body["job_id"])

    statuses = [h["status"] for h in history]
    deduped = [s for i, s in enumerate(statuses) if i == 0 or statuses[i - 1] != s]
    assert deduped == ["queued", "extracting", "synthesizing", "done"]
    # Chapters become ready in order.
    first_ready: list[int] = []
    for h in history:
        for c in h["chapters"]:
            if c["status"] == "ready" and c["index"] not in first_ready:
                first_ready.append(c["index"])
    assert first_ready == [1, 2, 3]
    done_values = [h["progress"]["chars_done"] for h in history]
    assert done_values == sorted(done_values) and done_values[-1] == final["progress"]["chars_total"] > 0
    assert any(h["progress"]["eta_seconds"] is not None for h in history if h["status"] == "synthesizing")

    assert final["progress"]["chapters_done"] == final["progress"]["chapters_total"] == 3
    assert final["engine_used"] == "edge" and final["error"] is None
    assert [c["title"] for c in final["chapters"]] == ["Chapter One", "Chapter Two", "Chapter Three"]
    assert all(c["duration_seconds"] > 0 for c in final["chapters"])
    assert final["filename"] == "My Book.docx"
    assert final["created_at"].endswith("Z") and final["expires_at"] > final["created_at"]


# -- upload rejections -----------------------------------------------------------------------


def _pdf(path: Path, pages: int = 1, text: str = "Plenty of extractable text on this page here.") -> Path:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    return path


def _encrypted(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Secret text on this page is long enough.")
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    return path


def _scanned(path: Path) -> Path:
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
        pix.clear_with(200)
        page.insert_image(pymupdf.Rect(72, 72, 300, 300), pixmap=pix)
    doc.save(path)
    return path


def _bytes(content: bytes):  # noqa: ANN202
    def build(path: Path) -> Path:
        path.write_bytes(content)
        return path

    return build


def _zip_without_word(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "not word")
    return path


REJECTIONS = [
    ("old.doc", _bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100), "LEGACY_DOC", 415),
    ("notes.txt", _bytes(b"hello"), "UNSUPPORTED_TYPE", 415),
    ("fake.pdf", _bytes(b"This is plain text, not a PDF."), "UNSUPPORTED_TYPE", 415),
    ("fake.docx", _zip_without_word, "UNSUPPORTED_TYPE", 415),
    ("broken.pdf", _bytes(b"%PDF-1.7\n garbage garbage garbage"), "CORRUPT_FILE", 422),
    ("locked.pdf", _encrypted, "ENCRYPTED_FILE", 422),
    ("long.pdf", lambda p: _pdf(p, pages=6), "TOO_MANY_PAGES", 422),
    ("scan.pdf", _scanned, "SCANNED_PDF", 422),
    # 1 MB + 10 KB: under the Content-Length allowance, so the streamed byte count catches it.
    ("big.pdf", _bytes(b"%PDF" + b"x" * (1024 * 1024 + 10_000)), "FILE_TOO_LARGE", 413),
]


@pytest.mark.parametrize(("name", "build", "code", "status"), REJECTIONS, ids=[r[2] + ":" + r[0] for r in REJECTIONS])
def test_upload_rejections(
    client: TestClient, api_settings: Settings, tmp_path: Path, name: str, build, code: str, status: int  # noqa: ANN001
) -> None:
    r = upload(client, build(tmp_path / name))
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code and r.json()["error"]["message"]
    assert list(jobs_dir(api_settings).iterdir()) == []  # no job folder left behind
    assert client.app.state.registry.all() == []  # and no job created


def test_missing_file_field_is_400(client: TestClient, api_settings: Settings) -> None:
    r = client.post("/api/upload", data={"other": "value"}, files={"notfile": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MISSING_FILE"
    r = client.post("/api/upload", content=b"raw body", headers={"Content-Type": "application/pdf"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MISSING_FILE"
    assert list(jobs_dir(api_settings).iterdir()) == []


def test_content_length_too_large_rejected_without_reading_body(client: TestClient, api_settings: Settings) -> None:
    read: list[int] = []

    def body() -> Iterator[bytes]:
        for _ in range(3):
            read.append(1)
            yield b"x" * 1024

    size = 10 * 1024 * 1024
    r = client.post(
        "/api/upload",
        content=body(),
        headers={"Content-Length": str(size), "Content-Type": "multipart/form-data; boundary=abc"},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert len(read) <= 1  # the app never asked for the body (at most the client's first piece)
    assert list(jobs_dir(api_settings).iterdir()) == []


def test_server_busy_when_one_running_and_three_queued(
    client: TestClient, api_settings: Settings, tmp_path: Path, gate: threading.Event
) -> None:
    doc = make_docx(tmp_path / "b.docx")
    ids = [upload(client, doc).json()["job_id"]]
    wait_for(lambda: client.get(f"/api/jobs/{ids[0]}").json()["status"] == "synthesizing")
    for _ in range(3):
        r = upload(client, doc)
        assert r.status_code == 202
        ids.append(r.json()["job_id"])
    r = upload(client, doc)
    assert r.status_code == 503 and r.json()["error"]["code"] == "SERVER_BUSY"
    assert len(list(jobs_dir(api_settings).iterdir())) == 4
    gate.set()
    for job_id in ids:
        assert wait_done(client, job_id)["status"] == "done"
    assert upload(client, doc).status_code == 202  # room again


# -- audio and downloads ---------------------------------------------------------------------


def test_chapter_audio_full_and_range(client: TestClient, tmp_path: Path, gate: threading.Event) -> None:
    job_id = upload(client, make_docx(tmp_path / "b.docx")).json()["job_id"]
    wait_for(lambda: client.get(f"/api/jobs/{job_id}").json()["status"] == "synthesizing")
    r = client.get(f"/api/jobs/{job_id}/chapters/1/audio")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_NOT_READY"
    gate.set()
    wait_done(client, job_id)

    full = client.get(f"/api/jobs/{job_id}/chapters/1/audio")
    assert full.status_code == 200
    assert full.headers["content-type"] == "audio/mpeg" and full.headers["accept-ranges"] == "bytes"
    data = full.content
    assert len(data) > 2000 and int(full.headers["content-length"]) == len(data)

    part = client.get(f"/api/jobs/{job_id}/chapters/1/audio", headers={"Range": "bytes=1000-1999"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 1000-1999/{len(data)}"
    assert part.content == data[1000:2000]

    r = client.get(f"/api/jobs/{job_id}/chapters/9/audio")
    assert r.status_code == 404 and r.json()["error"]["code"] == "CHAPTER_NOT_FOUND"
    r = client.get("/api/jobs/nope/chapters/1/audio")
    assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_chapter_download_filename(client: TestClient, tmp_path: Path, gate: threading.Event) -> None:
    gate.set()
    job_id = upload(client, make_docx(tmp_path / "Café: notes.docx")).json()["job_id"]
    wait_done(client, job_id)
    r = client.get(f"/api/jobs/{job_id}/download/2")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    disposition = r.headers["content-disposition"]
    assert 'filename="Caf__notes_02_Chapter_Two.mp3"' in disposition
    assert "filename*=UTF-8''Caf%C3%A9_notes_02_Chapter_Two.mp3" in disposition


def test_full_book_409_before_done_then_built_once(
    client: TestClient, tmp_path: Path, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = upload(client, make_docx(tmp_path / "Book.docx")).json()["job_id"]
    r = client.get(f"/api/jobs/{job_id}/download/full")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_NOT_READY"
    gate.set()
    final = wait_done(client, job_id)

    builds: list[Path] = []
    real_concat = audio.concat

    def slow_concat(parts, out_path, infos=None):  # noqa: ANN001, ANN202
        if out_path.name == "full.mp3":
            builds.append(out_path)
            threading.Event().wait(0.3)  # make the two requests overlap
        return real_concat(parts, out_path, infos)

    monkeypatch.setattr(audio, "concat", slow_concat)
    responses: list = [None, None]

    def fetch(i: int) -> None:
        responses[i] = client.get(f"/api/jobs/{job_id}/download/full")

    threads = [threading.Thread(target=fetch, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in responses] == [200, 200]
    assert len(builds) == 1  # built once, then served from the cache
    assert responses[0].content == responses[1].content
    assert 'filename="Book.mp3"' in responses[0].headers["content-disposition"]
    full = client.app.state.settings.storage_path / "jobs" / job_id / "full.mp3"
    chapters = sum(c["duration_seconds"] for c in final["chapters"])
    assert audio.probe(full).duration == pytest.approx(chapters + 2 * 2.0, abs=0.5)


# -- D26 / D27 through the API ---------------------------------------------------------------


def test_failed_chunk_fails_chapter_and_job_is_partial(
    api_settings: Settings, mp3_clip: Path, tmp_path: Path
) -> None:
    engine = GateEngine(mp3_clip, fails=lambda t: "chapter two" in t)
    with TestClient(create_app(api_settings, edge_only(engine))) as client:
        job_id = upload(client, make_docx(tmp_path / "b.docx")).json()["job_id"]
        final = wait_done(client, job_id)
        assert final["status"] == "partial" and final["error"] is None
        assert [c["status"] for c in final["chapters"]] == ["ready", "failed", "ready"]
        assert final["chapters"][1]["error"]["code"] == "TTS_FAILED"
        assert final["chapters"][1]["duration_seconds"] is None
        r = client.get(f"/api/jobs/{job_id}/chapters/2/audio")
        assert r.status_code == 409
        assert client.get(f"/api/jobs/{job_id}/download/full").status_code == 200  # partial is allowed


def test_config_and_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    config = client.get("/api/config").json()
    assert config["tts_fallback"] == "none" and config["max_words"] == 100_000
    assert config["accepted_types"] == [".pdf", ".docx"] and config["retention_hours"] == 24


def test_unknown_job_and_route(client: TestClient) -> None:
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404 and r.json() == {
        "error": {"code": "JOB_NOT_FOUND", "message": r.json()["error"]["message"]}
    }
    assert client.get("/api/nothing-here").json()["error"]["code"] == "NOT_FOUND"
