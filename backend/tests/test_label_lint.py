"""Label lint (SPEC §15.9) on synthetic label files."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from label_dialogue import Store, lint_files, lint_labels, run_lint  # noqa: E402

CAST = {"Alice": {"aliases": [], "gender": "female"}, "Hatter": {"aliases": ["the Hatter"], "gender": "male"}}
PARAGRAPHS = [
    "‘Well,’ said Alice, ‘I suppose so.’",  # 0
    "‘No room!’ they cried. ‘There’s plenty!’ said Alice.",  # 1
    "‘Very odd,’ thought Alice; ‘quite odd.’",  # 2
    "‘Oh dear! the Hatter said, sadly. ’Time is short.’",  # 3
    "‘Have some wine,’ the Hatter said.",  # 4
]


def line(p: int, start: int, end: int, utterance: str, speaker: str = "Alice", qid: int = 1) -> dict:
    return {
        "id": f"Q{qid}", "paragraph": p, "start": start, "end": end, "text": PARAGRAPHS[p][start:end],
        "speaker": speaker, "explicit": True, "utterance": utterance,
    }  # fmt: skip


def span(p: int, text: str) -> tuple[int, int]:
    start = PARAGRAPHS[p].index(text)
    return start, start + len(text)


def problems(lines: list[dict], items: list[dict] | None = None, cast: dict = CAST) -> list[tuple[str, int, str]]:
    return [(f.level, f.paragraph, f.problem) for f in lint_labels("t", PARAGRAPHS, lines, cast, items)]


def test_clean_labels_have_no_findings() -> None:
    lines = [
        line(0, *span(0, "‘Well,’"), "U1"),
        line(0, *span(0, "‘I suppose so.’"), "U1"),
        line(4, *span(4, "‘Have some wine,’"), "U2", "Hatter"),
    ]
    assert problems(lines) == []


def test_overlapping_spans_are_an_error() -> None:
    lines = [line(1, *span(1, "‘No room!’"), "U1", "UNKNOWN"), line(1, 0, len(PARAGRAPHS[1]), "U2")]
    found = problems(lines)
    assert any(level == "error" and "overlaps" in problem for level, _, problem in found)


def test_span_without_quote_marks_warns() -> None:
    start, end = span(1, "‘No room!’")
    found = problems([line(1, start, end - 1, "U1", "UNKNOWN")])
    assert found == [("warn", 1, "does not end with a single quote mark")]


def test_span_containing_narration_warns() -> None:
    found = problems([line(3, 0, len(PARAGRAPHS[3]), "U1", "Hatter")])
    assert ("warn", 3, "may contain narration (punctuation + speech tag inside the span)") in found


def test_not_a_quote_followed_by_thought_warns() -> None:
    start, end = span(2, "‘Very odd,’")
    items = [{"p": 2, "s": start, "e": end, "decision": "not_quote"}]
    found = problems([], items)
    assert len(found) == 1 and "thought" in found[0][2]


def test_unjoined_broken_quote_warns() -> None:
    lines = [line(0, *span(0, "‘Well,’"), "U1"), line(0, *span(0, "‘I suppose so.’"), "U2")]
    found = problems(lines)
    assert len(found) == 1 and found[0][0] == "warn" and "broken quote" in found[0][2]


def test_two_speakers_separated_by_a_full_stop_are_fine() -> None:
    lines = [
        line(1, *span(1, "‘No room!’"), "U1", "UNKNOWN"),
        line(1, *span(1, "‘There’s plenty!’"), "U2"),
    ]
    assert problems(lines) == []


def test_speaker_missing_from_cast_is_an_error() -> None:
    found = problems([line(4, *span(4, "‘Have some wine,’"), "U1", "Dormouse")])
    assert found == [("error", 4, "speaker 'Dormouse' is not in the cast")]


@pytest.fixture
def chapter_files(tmp_path: Path) -> Path:
    text = tmp_path / "text" / "toy.json"
    text.parent.mkdir()
    text.write_text(json.dumps({"source": "x", "chapter_index": 1, "title": "T", "paragraphs": PARAGRAPHS}),
                    encoding="utf-8")  # fmt: skip
    return text


def test_lint_files_and_exit_code(chapter_files: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    labels = tmp_path / "labels"
    labels.mkdir()
    lines = [line(1, *span(1, "‘No room!’"), "U1", "UNKNOWN"), line(1, 0, len(PARAGRAPHS[1]), "U2")]
    (labels / "toy.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    (labels / "toy.cast.json").write_text(json.dumps(CAST), encoding="utf-8")
    findings = lint_files(chapter_files, labels)
    assert any(f.level == "error" for f in findings)


def test_run_lint_reports_missing_labels(chapter_files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_lint(chapter_files) == 1  # labels dir has no toy.jsonl
    assert "1 error(s)" in capsys.readouterr().out


def test_done_is_refused_on_lint_errors(chapter_files: Path, tmp_path: Path) -> None:
    store = Store(chapter_files, tmp_path / "labels")
    store.state["items"] = [
        {"key": "a", "p": 1, "s": 0, "e": 10, "origin": "candidate", "decision": "quote", "speaker": "UNKNOWN",
         "explicit": False, "hint": False, "same": False},
        {"key": "b", "p": 1, "s": 0, "e": len(PARAGRAPHS[1]), "origin": "candidate", "decision": "quote",
         "speaker": "Alice", "explicit": False, "hint": False, "same": False},
    ]  # fmt: skip
    store.state["cast"] = CAST
    result = store.finish()
    assert not result["ok"] and any("overlaps" in e for e in result["errors"])
    assert store.state["done"] is False

    store.state["items"] = store.state["items"][:1]
    result = store.finish()
    assert result["ok"] and store.state["done"] is True
