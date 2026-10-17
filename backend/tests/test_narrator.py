"""Narrator and fallback tests with fake engines (no network). Needs ffmpeg for assembly."""

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from app.config import Settings
from app.models import Chapter, DocumentModel, Paragraph, Segment
from app.tts import audio
from app.tts.engines import ChunkFailed, FallbackEngine, NarrationUnavailable, PiperEngine
from app.tts.narrator import ProgressEvent, build_full_book, narrate_document

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tiny valid MP3 in the standard format; fake engines copy it."""
    return audio.make_silence(240, tmp_path_factory.mktemp("clip") / "clip.mp3")


class FakeEngine:
    """Writes `clip` to the output path. `fails(text)` decides whether a call raises."""

    def __init__(self, name: str, clip: Path, fails: Callable[[str], bool] = lambda _: False, delay: float = 0.0):
        self.name = name
        self.clip = clip
        self.fails = fails
        self.delay = delay
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.timeline: list[tuple[str, str]] = []  # ("start"|"end", text)

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        assert text.strip(), "empty text reached the engine"
        self.calls.append(text)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.timeline.append(("start", text))
        try:
            await asyncio.sleep(self.delay)
            if self.fails(text):
                raise ConnectionError(f"{self.name} failed")
            shutil.copyfile(self.clip, out_path)
        finally:
            self.active -= 1
            self.timeline.append(("end", text))


# Numbers are spelled out so the text the engines receive (normalized for speech) equals the input.
WORDS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen".split()


def document(chapters: list[list[str]]) -> DocumentModel:
    built = [
        Chapter(
            index=n,
            title=f"Title {WORDS[n]}",
            word_count=sum(len(p.split()) for p in paras),
            paragraphs=[Paragraph(index=i, segments=[Segment(text=t)]) for i, t in enumerate(paras, 1)],
        )
        for n, paras in enumerate(chapters, 1)
    ]
    return DocumentModel(
        source_filename="t.pdf",
        source_type="pdf",
        page_count=1,
        word_count=sum(c.word_count for c in built),
        structure_source="fallback",
        warnings=[],
        chapters=built,
    )


def settings(concurrency: int = 4) -> Settings:
    return Settings(_env_file=None, tts_concurrency=concurrency, tts_chunk_max_chars=1500)


def paragraphs(chapter: int, n: int) -> list[str]:
    return [f"Chapter {WORDS[chapter]} paragraph {WORDS[i]} text." for i in range(1, n + 1)]


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def test_chapters_in_order_with_bounded_concurrency(tmp_path: Path, clip: Path) -> None:
    engine = FakeEngine("edge", clip, delay=0.01)
    doc = document([paragraphs(1, 6), paragraphs(2, 6), paragraphs(3, 2)])
    result = run(narrate_document(doc, tmp_path, engine, settings(concurrency=3)))
    assert [c.status for c in result.chapters] == ["ready"] * 3
    assert engine.max_active == 3
    # No chunk of chapter N+1 starts before every chunk of chapter N has ended.
    def chapter_of(text: str) -> int:
        return WORDS.index(text.split()[1].rstrip("."))

    seen_end: dict[int, int] = {}
    for i, (kind, text) in enumerate(engine.timeline):
        if kind == "end":
            seen_end[chapter_of(text)] = i
    for i, (kind, text) in enumerate(engine.timeline):
        if kind == "start" and chapter_of(text) > 1:
            assert seen_end[chapter_of(text) - 1] < i
    for c in result.chapters:
        assert c.path is not None and c.path.exists() and c.path.name == f"ch_{c.index:03d}.mp3"
        assert c.engine_used == "edge"
    assert result.engine_used == "edge"


def test_chapter_duration_is_sum_of_parts(tmp_path: Path, clip: Path) -> None:
    doc = document([paragraphs(1, 3)])
    result = run(narrate_document(doc, tmp_path, FakeEngine("edge", clip), settings()))
    clip_s = audio.probe(clip).duration
    title_pause = audio.probe(tmp_path / "silence" / "silence_1000.mp3").duration
    para_pause = audio.probe(tmp_path / "silence" / "silence_400.mp3").duration
    expected = 4 * clip_s + title_pause + 2 * para_pause  # title + 3 paragraphs
    chapter = result.chapters[0]
    assert chapter.duration_seconds == pytest.approx(expected, abs=0.01)
    assert audio.probe(chapter.path).duration == pytest.approx(expected, abs=0.1)


def test_fallback_per_chunk_gives_mixed(tmp_path: Path, clip: Path) -> None:
    edge = FakeEngine("edge", clip, fails=lambda t: "paragraph two" in t)
    piper = FakeEngine("piper", clip)
    engine = FallbackEngine(edge, piper, sleep=no_sleep)
    result = run(narrate_document(document([paragraphs(1, 3)]), tmp_path, engine, settings()))
    assert piper.calls == ["Chapter one paragraph two text."]
    assert edge.calls.count("Chapter one paragraph two text.") == 3  # three edge attempts first
    assert result.chapters[0].engine_used == "mixed" and result.engine_used == "mixed"
    assert not engine.switched


def test_switch_to_fallback_after_five_consecutive_failures(tmp_path: Path, clip: Path) -> None:
    edge = FakeEngine("edge", clip, fails=lambda _: True)
    piper = FakeEngine("piper", clip)
    engine = FallbackEngine(edge, piper, max_retries=3, switch_after=5, sleep=no_sleep)
    doc = document([paragraphs(1, 6), paragraphs(2, 4)])
    result = run(narrate_document(doc, tmp_path, engine, settings(concurrency=1)))
    assert engine.switched
    assert len(edge.calls) == 5 * 3  # 5 chunks x 3 attempts, then edge is never called again
    assert len(piper.calls) == sum(c.chunks for c in result.chapters)
    assert result.engine_used == "piper"


def test_edge_success_resets_the_counter(tmp_path: Path, clip: Path) -> None:
    # Every 5th chunk succeeds on edge: never 5 failures in a row, so no switch.
    texts = paragraphs(1, 14)
    succeeding = set(texts[3::5])
    edge = FakeEngine("edge", clip, fails=lambda t: t not in succeeding)
    piper = FakeEngine("piper", clip)
    engine = FallbackEngine(edge, piper, switch_after=5, sleep=no_sleep)
    result = run(narrate_document(document([texts]), tmp_path, engine, settings(concurrency=1)))
    assert not engine.switched
    assert result.engine_used == "mixed"
    assert set(edge.calls) >= succeeding


def test_backoff_delays_are_used(tmp_path: Path, clip: Path) -> None:
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    edge = FakeEngine("edge", clip, fails=lambda _: True)
    engine = FallbackEngine(edge, FakeEngine("piper", clip), max_retries=3, backoff=(1, 2, 4), sleep=record)
    run(engine.synthesize("Hello there.", tmp_path / "x.mp3"))
    assert delays == [1, 2]  # wait after attempts 1 and 2; attempt 3 falls straight back


def test_backoff_with_five_retries_caps_at_eight(tmp_path: Path, clip: Path) -> None:
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    engine = FallbackEngine(FakeEngine("edge", clip, fails=lambda _: True), None, max_retries=6, sleep=record)
    with pytest.raises(ChunkFailed):
        run(engine.synthesize("Hello there.", tmp_path / "x.mp3"))
    assert delays == [1, 2, 4, 8, 8]


def test_fully_failed_chapter_is_failed_and_next_runs(tmp_path: Path, clip: Path) -> None:
    def bad(text: str) -> bool:
        return "Chapter two" in text or text == "Title two"

    engine = FallbackEngine(
        FakeEngine("edge", clip, fails=bad), FakeEngine("piper", clip, fails=bad), switch_after=50, sleep=no_sleep
    )
    doc = document([paragraphs(1, 2), paragraphs(2, 2), paragraphs(3, 2)])
    result = run(narrate_document(doc, tmp_path, engine, settings()))
    assert [c.status for c in result.chapters] == ["ready", "failed", "ready"]
    assert result.chapters[1].path is None and result.chapters[1].error
    assert not (tmp_path / "chapters" / "ch_002.mp3").exists()


def test_progress_events_in_order(tmp_path: Path, clip: Path) -> None:
    events: list[ProgressEvent] = []
    doc = document([paragraphs(1, 2), paragraphs(2, 3)])
    result = run(narrate_document(doc, tmp_path, FakeEngine("edge", clip), settings(), events.append))
    kinds = [(e.kind, e.chapter_index) for e in events]
    assert kinds == (
        [("chapter_started", 1)] + [("chunk_done", 1)] * 3 + [("chapter_ready", 1)]
        + [("chapter_started", 2)] + [("chunk_done", 2)] * 4 + [("chapter_ready", 2)]
    )  # fmt: skip
    done = [e.chars_done for e in events]
    assert done == sorted(done) and done[-1] == result.chars_total == events[-1].chars_total
    ready = [e for e in events if e.kind == "chapter_ready"]
    assert all(e.path and e.path.exists() and e.duration_seconds for e in ready)


def test_work_folder_is_cleaned(tmp_path: Path, clip: Path) -> None:
    run(narrate_document(document([paragraphs(1, 2)]), tmp_path, FakeEngine("edge", clip), settings()))
    assert not (tmp_path / "work").exists()
    assert (tmp_path / "chapters" / "ch_001.mp3").exists()


def test_full_book_has_two_second_gaps(tmp_path: Path, clip: Path) -> None:
    doc = document([paragraphs(1, 1), paragraphs(2, 1), paragraphs(3, 1)])
    result = run(narrate_document(doc, tmp_path, FakeEngine("edge", clip), settings()))
    full = build_full_book(result, tmp_path)
    gap = audio.probe(tmp_path / "silence" / "silence_2000.mp3").duration
    expected = sum(c.duration_seconds for c in result.chapters) + 2 * gap
    assert audio.probe(full).duration == pytest.approx(expected, abs=0.1)
    assert build_full_book(result, tmp_path) == full  # cached


def test_empty_text_never_reaches_an_engine(tmp_path: Path, clip: Path) -> None:
    engine = FakeEngine("edge", clip)
    doc = document([["* * *", "Real words here.", "  "]])
    result = run(narrate_document(doc, tmp_path, engine, settings()))
    assert engine.calls == ["Title one", "Real words here."]
    assert result.chapters[0].status == "ready"
    with pytest.raises(ValueError):
        run(FallbackEngine(engine, engine, sleep=no_sleep).synthesize("   ", tmp_path / "e.mp3"))


class FakeVoice:
    """Stands in for piper.PiperVoice: writes 0.1 s of silence per call, records each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def synthesize_wav(self, text: str, wav, set_wav_format: bool = True) -> None:  # noqa: ANN001
        self.calls.append((text, set_wav_format))
        if set_wav_format:
            wav.setframerate(22050)
            wav.setsampwidth(2)
            wav.setnchannels(1)
        wav.writeframes(bytes(2 * 2205))


def test_piper_splits_long_text_into_one_file(tmp_path: Path) -> None:
    engine = PiperEngine(tmp_path / "unused.onnx", max_chars=40)
    voice = FakeVoice()
    engine._voice = voice
    text = "First sentence is here. Second sentence is here. Third sentence is also here."
    run(engine.synthesize(text, tmp_path / "out.mp3"))
    assert [t for t, _ in voice.calls] == ["First sentence is here.", "Second sentence is here.",
                                           "Third sentence is also here."]  # fmt: skip
    assert [f for _, f in voice.calls] == [True, False, False]  # WAV format set once
    info = audio.probe(tmp_path / "out.mp3")
    assert info.is_standard and info.duration == pytest.approx(0.3, abs=0.1)
    assert not list(tmp_path.glob("*.wav"))


# D27: no silent gaps. D26: edge-only mode stops the job after 5 consecutive failed chunks.


def test_one_failed_chunk_fails_the_whole_chapter(tmp_path: Path, clip: Path) -> None:
    def bad(text: str) -> bool:
        return text == "Chapter two paragraph two text."

    engine = FallbackEngine(
        FakeEngine("edge", clip, fails=bad), FakeEngine("piper", clip, fails=bad), switch_after=50, sleep=no_sleep
    )
    events: list[ProgressEvent] = []
    doc = document([paragraphs(1, 2), paragraphs(2, 3), paragraphs(3, 2)])
    result = run(narrate_document(doc, tmp_path, engine, settings(), events.append))
    assert [c.status for c in result.chapters] == ["ready", "failed", "ready"]
    assert not (tmp_path / "chapters" / "ch_002.mp3").exists()  # partial audio discarded
    assert result.chapters[1].path is None and result.chapters[1].duration_seconds is None
    assert ("chapter_failed", 2) in [(e.kind, e.chapter_index) for e in events]
    assert not result.unavailable


def test_edge_only_failed_chunk_fails_chapter_without_fallback(tmp_path: Path, clip: Path) -> None:
    edge = FakeEngine("edge", clip, fails=lambda t: "Chapter two" in t)
    engine = FallbackEngine(edge, None, max_retries=5, sleep=no_sleep)
    doc = document([paragraphs(1, 2), paragraphs(2, 2), paragraphs(3, 2)])
    result = run(narrate_document(doc, tmp_path, engine, settings(concurrency=1)))
    assert [c.status for c in result.chapters] == ["ready", "failed", "ready"]
    assert edge.calls.count("Chapter two paragraph one text.") == 5  # five patient attempts
    assert result.engine_used == "edge" and not result.unavailable


def test_edge_only_five_consecutive_failures_stop_the_job(tmp_path: Path, clip: Path) -> None:
    # Chapter one succeeds; from chapter two on edge is down. Each failed chapter adds one failed
    # chunk (the others are cancelled), so the fifth failed chapter makes narration unavailable.
    edge = FakeEngine("edge", clip, fails=lambda t: "Chapter one" not in t and t != "Title one")
    engine = FallbackEngine(edge, None, max_retries=2, switch_after=5, sleep=no_sleep)
    events: list[ProgressEvent] = []
    doc = document([paragraphs(n, 2) for n in range(1, 9)])
    result = run(narrate_document(doc, tmp_path, engine, settings(concurrency=1), events.append))
    assert [c.status for c in result.chapters] == ["ready"] + ["failed"] * 7
    assert result.unavailable and engine.switched
    # After the stop no later chapter reaches the engine.
    assert not [t for t in edge.calls if "seven" in t or "eight" in t]
    failed = [e.chapter_index for e in events if e.kind == "chapter_failed"]
    assert failed == [2, 3, 4, 5, 6, 7, 8]
    assert "unavailable" in (result.chapters[-1].error or "")
    assert not list((tmp_path / "chapters").glob("ch_00[2-8].mp3"))


def test_unavailable_engine_raises_for_every_later_chunk(tmp_path: Path, clip: Path) -> None:
    engine = FallbackEngine(FakeEngine("edge", clip, fails=lambda _: True), None, max_retries=1, switch_after=2,
                            sleep=no_sleep)  # fmt: skip
    with pytest.raises(ChunkFailed):
        run(engine.synthesize("One.", tmp_path / "a.mp3"))
    with pytest.raises(NarrationUnavailable):
        run(engine.synthesize("Two.", tmp_path / "b.mp3"))
    with pytest.raises(NarrationUnavailable):
        run(engine.synthesize("Three.", tmp_path / "c.mp3"))
