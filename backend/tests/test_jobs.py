"""Job worker, registry and cleanup tests (fake engine, no network). Needs ffmpeg."""

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import ChapterState, run_cleanup_once
from app.jobs.models import utc_now
from app.jobs.worker import estimate_eta, final_status
from app.main import create_app
from app.tts.engines import FallbackEngine
from conftest import GateEngine, make_docx, no_sleep, wait_for

FINISHED = ("done", "partial", "failed")


def edge_only(engine: GateEngine):  # noqa: ANN201
    return lambda s: FallbackEngine(
        engine, None, max_retries=s.tts_max_retries, switch_after=s.tts_fallback_after_consecutive_failures,
        sleep=no_sleep,
    )  # fmt: skip


def upload(client: TestClient, path: Path) -> str:
    with path.open("rb") as fh:
        r = client.post("/api/upload", files={"file": (path.name, fh, "application/octet-stream")})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def wait_done(client: TestClient, job_id: str) -> dict:
    result: dict = {}

    def finished() -> bool:
        result.update(client.get(f"/api/jobs/{job_id}").json())
        return result.get("status") in FINISHED

    wait_for(finished)
    return result


def test_final_status_rules() -> None:
    def chapters(*statuses: str) -> list[ChapterState]:
        return [ChapterState(i, "t", s) for i, s in enumerate(statuses, 1)]  # type: ignore[arg-type]

    assert final_status(chapters("ready", "ready")) == "done"
    assert final_status(chapters("ready", "failed")) == "partial"
    assert final_status(chapters("failed", "failed")) == "failed"
    assert final_status([]) == "failed"


def test_eta_from_observed_rate() -> None:
    assert estimate_eta(0, 1000, 5.0) is None
    assert estimate_eta(100, 1000, 10.0) == 90  # 10 chars/s, 900 left
    assert estimate_eta(1000, 1000, 10.0) == 0


def test_five_consecutive_failures_fail_remaining_chapters(
    api_settings: Settings, mp3_clip: Path, tmp_path: Path
) -> None:
    # Chapter one works, then edge is "down": chapters 2-6 each fail one chunk -> unavailable at
    # the 5th; chapter 7 is failed without ever reaching the engine.
    engine = GateEngine(mp3_clip, fails=lambda t: "chapter one" not in t and t != "Chapter One")
    with TestClient(create_app(api_settings, edge_only(engine))) as client:
        job_id = upload(client, make_docx(tmp_path / "b.docx", chapters=7))
        final = wait_done(client, job_id)
    assert final["status"] == "partial"
    assert [c["status"] for c in final["chapters"]] == ["ready"] + ["failed"] * 6
    assert all(c["error"]["code"] == "TTS_FAILED" for c in final["chapters"][1:])
    assert not [t for t in engine.calls if "seven" in t.lower()]
    assert final["progress"]["chapters_done"] == 7


def test_every_chapter_failed_means_job_failed(api_settings: Settings, mp3_clip: Path, tmp_path: Path) -> None:
    engine = GateEngine(mp3_clip, fails=lambda _: True)
    with TestClient(create_app(api_settings, edge_only(engine))) as client:
        final = wait_done(client, upload(client, make_docx(tmp_path / "b.docx")))
        assert client.get(f"/api/jobs/{final['job_id']}/download/full").status_code == 409
    assert final["status"] == "failed"
    assert final["error"]["code"] == "TTS_FAILED"
    assert all(c["status"] == "failed" for c in final["chapters"])


def test_error_after_upload_fails_the_job(api_settings: Settings, mp3_clip: Path, tmp_path: Path) -> None:
    settings = api_settings.model_copy(update={"max_words": 50})
    with TestClient(create_app(settings, edge_only(GateEngine(mp3_clip)))) as client:
        final = wait_done(client, upload(client, make_docx(tmp_path / "b.docx")))
    assert final["status"] == "failed"
    assert final["error"]["code"] == "TOO_MANY_WORDS" and final["error"]["message"]
    assert final["chapters"] == []


def test_worker_survives_a_crashing_job(api_settings: Settings, mp3_clip: Path, tmp_path: Path) -> None:
    calls = {"n": 0}
    healthy = edge_only(GateEngine(mp3_clip))

    def factory(settings: Settings):  # noqa: ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("engine exploded")
        return healthy(settings)

    with TestClient(create_app(api_settings, factory)) as client:
        first = upload(client, make_docx(tmp_path / "a.docx"))
        second = upload(client, make_docx(tmp_path / "b.docx"))
        crashed = wait_done(client, first)
        ok = wait_done(client, second)
    assert crashed["status"] == "failed" and crashed["error"]["code"] == "TTS_FAILED"
    assert all(c["status"] == "failed" for c in crashed["chapters"])
    assert ok["status"] == "done"


def test_expiry_deletes_files_then_stub_disappears(
    api_settings: Settings, mp3_clip: Path, tmp_path: Path
) -> None:
    with TestClient(create_app(api_settings, edge_only(GateEngine(mp3_clip)))) as client:
        registry = client.app.state.registry
        job_id = upload(client, make_docx(tmp_path / "b.docx"))
        wait_done(client, job_id)
        folder = api_settings.storage_path / "jobs" / job_id
        assert (folder / "job.json").exists() and (folder / "document.json").exists()

        run_cleanup_once(registry, api_settings)  # not expired yet: nothing happens
        assert folder.exists()

        later = utc_now() + timedelta(hours=api_settings.retention_hours, seconds=1)
        run_cleanup_once(registry, api_settings, now=later)
        assert not folder.exists()
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 410 and r.json()["error"]["code"] == "JOB_EXPIRED"
        assert client.get(f"/api/jobs/{job_id}/chapters/1/audio").status_code == 410

        run_cleanup_once(registry, api_settings, now=later + timedelta(minutes=59))
        assert client.get(f"/api/jobs/{job_id}").status_code == 410
        run_cleanup_once(registry, api_settings, now=later + timedelta(minutes=60))
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_locked_folder_is_retried(
    api_settings: Settings, mp3_clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(api_settings, edge_only(GateEngine(mp3_clip)))) as client:
        registry = client.app.state.registry
        job_id = upload(client, make_docx(tmp_path / "b.docx"))
        wait_done(client, job_id)
        later = utc_now() + timedelta(hours=api_settings.retention_hours, seconds=1)

        import app.jobs.cleanup as cleanup

        def locked(_: Path) -> None:
            raise PermissionError("file in use")

        monkeypatch.setattr(cleanup.shutil, "rmtree", locked)
        run_cleanup_once(registry, api_settings, now=later)
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"  # kept, retried later
        monkeypatch.undo()
        run_cleanup_once(registry, api_settings, now=later)
        assert client.get(f"/api/jobs/{job_id}").status_code == 410


def test_running_jobs_are_never_expired(
    api_settings: Settings, mp3_clip: Path, tmp_path: Path, gate: threading.Event
) -> None:
    with TestClient(create_app(api_settings, edge_only(GateEngine(mp3_clip, gate=gate)))) as client:
        job_id = upload(client, make_docx(tmp_path / "b.docx"))
        wait_for(lambda: client.get(f"/api/jobs/{job_id}").json()["status"] == "synthesizing")
        run_cleanup_once(client.app.state.registry, api_settings, now=utc_now() + timedelta(days=2))
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "synthesizing"
        gate.set()
        assert wait_done(client, job_id)["status"] == "done"


def test_startup_wipes_old_jobs(api_settings: Settings, mp3_clip: Path) -> None:
    stale = api_settings.storage_path / "jobs" / "old-job"
    stale.mkdir(parents=True)
    (stale / "input.pdf").write_bytes(b"%PDF")
    with TestClient(create_app(api_settings, edge_only(GateEngine(mp3_clip)))):
        assert not stale.exists()
