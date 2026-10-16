from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_config_exposes_limits() -> None:
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["max_pdf_pages"] == 200
    assert body["accepted_types"] == [".pdf", ".docx"]
