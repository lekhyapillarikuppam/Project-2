"""Per-IP upload limit (D30, SPEC §4/§9): trusted-proxy check, sliding window, 429 + Retry-After."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import UploadRateLimiter, client_ip, is_trusted_proxy
from app.config import Settings
from app.main import create_app
from app.pipeline.errors import ERROR_MESSAGES

PROXY = "172.18.0.3"  # Caddy on the compose network
OUTSIDER = "198.51.100.7"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("peer", "trusted"),
    [
        ("172.18.0.3", True),
        ("192.168.65.2", True),
        ("10.1.2.3", True),
        ("fd00::5", True),
        ("127.0.0.1", False),
        ("::1", False),
        ("169.254.1.1", False),
        ("8.8.8.8", False),
        (OUTSIDER, False),
        ("testclient", False),
        ("", False),
        (None, False),
    ],
)
def test_trusted_proxy(peer: str | None, trusted: bool) -> None:
    assert is_trusted_proxy(peer) is trusted


def test_client_ip_uses_forwarded_for_only_behind_the_proxy() -> None:
    assert client_ip(PROXY, "203.0.113.9, 172.18.0.3") == "203.0.113.9"
    assert client_ip(PROXY, " 2001:db8::1 ") == "2001:db8::1"
    assert client_ip(PROXY, None) == PROXY
    assert client_ip(PROXY, "not-an-ip") == PROXY
    assert client_ip(OUTSIDER, "203.0.113.9") == OUTSIDER  # spoofed header from a direct client
    assert client_ip(None, None) == "unknown"


def test_limiter_allows_ten_then_reports_retry_after() -> None:
    clock = FakeClock()
    limiter = UploadRateLimiter(10, clock=clock)
    for i in range(10):
        assert limiter.hit("a") is None
        clock.now += 60  # one upload a minute
    # oldest at 1000, now 1600: a slot frees at 4600
    assert limiter.hit("a") == 3000
    assert limiter.hit("b") is None  # other clients are independent


def test_limiter_window_slides() -> None:
    clock = FakeClock()
    limiter = UploadRateLimiter(2, window_seconds=100, clock=clock)
    assert limiter.hit("a") is None
    clock.now += 50
    assert limiter.hit("a") is None
    assert limiter.hit("a") == 50
    clock.now += 50  # the first hit leaves the window
    assert limiter.hit("a") is None
    assert limiter.hit("a") == 50  # the second (at +50) leaves at +150
    clock.now += 50
    assert limiter.hit("a") is None


def test_limiter_disabled_with_zero() -> None:
    limiter = UploadRateLimiter(0)
    assert all(limiter.hit("a") is None for _ in range(100))


# -- through the API --------------------------------------------------------------------------


@pytest.fixture
def limited_settings(api_settings: Settings) -> Settings:
    return api_settings.model_copy(update={"uploads_per_ip_per_hour": 10})


def post_txt(client: TestClient, forwarded_for: str | None = None):  # noqa: ANN201
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return client.post("/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")}, headers=headers)


def jobs_entries(settings: Settings) -> list[Path]:
    folder = settings.storage_path / "jobs"
    return list(folder.iterdir()) if folder.exists() else []


def test_eleventh_upload_is_429_with_retry_after(limited_settings: Settings) -> None:
    clock = FakeClock()
    app = create_app(limited_settings)
    with TestClient(app, client=(OUTSIDER, 50000)) as client:
        app.state.upload_limiter = UploadRateLimiter(10, clock=clock)
        for _ in range(10):  # rejected uploads count too
            assert post_txt(client).status_code == 415
        response = post_txt(client)
        assert response.status_code == 429
        assert response.json() == {"error": {"code": "RATE_LIMITED", "message": ERROR_MESSAGES["RATE_LIMITED"]}}
        assert response.headers["Retry-After"] == "3600"
        assert jobs_entries(limited_settings) == []  # no job folder: the body was not read

        clock.now += 3600  # the window slides
        assert post_txt(client).status_code == 415


def test_spoofed_forwarded_for_from_direct_client_is_ignored(limited_settings: Settings) -> None:
    app = create_app(limited_settings)
    with TestClient(app, client=(OUTSIDER, 50000)) as client:
        for i in range(10):
            assert post_txt(client, forwarded_for=f"203.0.113.{i}").status_code == 415
        assert post_txt(client, forwarded_for="203.0.113.99").status_code == 429


def test_clients_behind_the_proxy_are_counted_separately(limited_settings: Settings) -> None:
    app = create_app(limited_settings)
    with TestClient(app, client=(PROXY, 50000)) as client:
        for _ in range(10):
            assert post_txt(client, forwarded_for="203.0.113.1").status_code == 415
        assert post_txt(client, forwarded_for="203.0.113.1").status_code == 429
        assert post_txt(client, forwarded_for="203.0.113.2").status_code == 415


def test_cors_exposes_retry_after(limited_settings: Settings) -> None:
    app = create_app(limited_settings)
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "Retry-After" in response.headers["access-control-expose-headers"]
