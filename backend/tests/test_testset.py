"""Integration test on the real test set. Skipped when testset/docs is not present (it is gitignored)."""

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.pipeline import PipelineError, run_pipeline

TESTSET = Path(__file__).resolve().parents[2] / "testset"
DOCS = TESTSET / "docs"
LABELS = json.loads((TESTSET / "labels.json").read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(not DOCS.is_dir(), reason="testset/docs is not present")

REJECTS = {name: label["expect"] for name, label in LABELS.items() if label["expect"] != "accept"}
ACCEPTS = [name for name, label in LABELS.items() if label["expect"] == "accept"]


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.mark.parametrize(("name", "code"), sorted(REJECTS.items()))
def test_rejects_give_expected_code(name: str, code: str, settings: Settings) -> None:
    with pytest.raises(PipelineError) as info:
        run_pipeline(DOCS / name, name, settings)
    assert info.value.code == code


@pytest.mark.parametrize("name", ACCEPTS)
def test_accepted_files_produce_chapters(name: str, settings: Settings) -> None:
    document = run_pipeline(DOCS / name, name, settings)
    assert len(document.chapters) >= 1
    assert all(c.paragraphs for c in document.chapters)
