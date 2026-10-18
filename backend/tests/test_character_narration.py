"""Multi-voice narration (SPEC §15.6): chunks by speaker, a voice per chunk, quote marks stripped,
titles by the narrator, 150 ms between speakers, exact durations; single mode unchanged."""

import asyncio
import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.dialogue.attribute import apply_speakers
from app.dialogue.segment import segment_chapter
from app.models import Chapter, DocumentModel, Paragraph, Segment
from app.tts import audio
from app.tts.chunk import chunk_chapter, speaker_runs
from app.tts.narrator import narrate_document, plan_narration

VOICES = {"Alice": "voice-alice", "Hatter": "voice-hatter"}


def chapter(*texts: str, speakers: dict[str, str] | None = None) -> Chapter:
    raw = Chapter(
        index=1, title="Chapter One", word_count=sum(len(t.split()) for t in texts),
        paragraphs=[Paragraph(index=i, segments=[Segment(text=t)]) for i, t in enumerate(texts, 1)],
    )  # fmt: skip
    segmented, _ = segment_chapter(raw, "single")
    return apply_speakers(segmented, speakers or {})


def test_runs_join_same_speaker_segments_and_strip_quote_marks() -> None:
    ch = chapter("‘Well,’ said Alice. ‘No,’ said the Hatter.", speakers={"U1": "Alice", "U2": "Hatter"})
    assert speaker_runs(ch.paragraphs[0].segments) == [
        ("Alice", "Well,"), ("narrator", "said Alice."), ("Hatter", "No,"), ("narrator", "said the Hatter."),
    ]  # fmt: skip


def test_chunks_never_mix_speakers_and_carry_their_voice() -> None:
    ch = chapter("‘Well,’ said Alice. ‘No,’ said the Hatter.", "Plain narration.", speakers={"U1": "Alice", "U2": "Hatter"})
    chunks = chunk_chapter(ch, 1500, VOICES)
    title, *body = chunks
    assert title.is_title and title.voice is None
    assert [(c.text, c.voice, c.speaker_change, c.ends_paragraph) for c in body] == [
        ("Well,", "voice-alice", False, False),
        ("said Alice.", None, True, False),
        ("No,", "voice-hatter", True, False),
        ("said the Hatter.", None, True, True),
        ("Plain narration.", None, False, True),
    ]
    assert not any(m in c.text for c in chunks for m in "‘’“”")


def test_segments_with_nothing_to_say_are_dropped() -> None:
    segments = [
        Segment(text="‘Well—’", kind="quote", utterance_id="U1", speaker="Alice"),
        Segment(text="(", speaker="narrator"),
        Segment(text="‘—so there.’", kind="quote", utterance_id="U1", speaker="Alice"),
        Segment(text="‘—’", kind="quote", utterance_id="U2", speaker="Hatter"),
    ]
    assert speaker_runs(segments) == [("Alice", "Well— —so there.")]


def test_unknown_speakers_stay_with_the_narrator() -> None:
    ch = chapter("‘No room!’ they cried.", speakers={})
    body = chunk_chapter(ch, 1500, VOICES)[1:]
    assert [(c.text, c.voice) for c in body] == [("No room! they cried.", None)]


def test_single_mode_chunks_are_unchanged() -> None:
    ch = chapter("‘Well,’ said Alice, ‘I suppose so.’", "Plain narration.", speakers={"U1": "Alice"})
    plain = Chapter(
        index=1, title="Chapter One", word_count=ch.word_count,
        paragraphs=[Paragraph(index=p.index, segments=[Segment(text=" ".join(s.text for s in p.segments))])
                    for p in ch.paragraphs],
    )  # fmt: skip
    assert chunk_chapter(ch, 1500) == chunk_chapter(plain, 1500)
    assert all(c.voice is None and not c.speaker_change for c in chunk_chapter(ch, 1500))


class VoiceEngine:
    """Copies `clip` and records the voice of every call."""

    name = "edge"

    def __init__(self, clip: Path) -> None:
        self.clip = clip
        self.voices: list[tuple[str, str | None]] = []

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        self.voices.append((text, voice))
        shutil.copyfile(self.clip, out_path)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_voices_reach_the_engine_and_pauses_keep_durations_exact(tmp_path: Path) -> None:
    clip = audio.make_silence(240, tmp_path / "clip.mp3")
    ch = chapter("‘Well,’ said Alice. ‘No,’ said the Hatter.", speakers={"U1": "Alice", "U2": "Hatter"})
    doc = DocumentModel(
        source_filename="t.pdf", source_type="pdf", page_count=1, word_count=ch.word_count,
        structure_source="fallback", warnings=[], chapters=[ch],
    )  # fmt: skip
    settings = Settings(_env_file=None, tts_concurrency=2, tts_chunk_max_chars=1500)
    engine = VoiceEngine(clip)
    out = tmp_path / "out"
    plan = plan_narration(doc, 1500, VOICES)
    result = asyncio.run(narrate_document(doc, out, engine, settings, plan=plan))
    assert dict(engine.voices) == {
        "Chapter One": None, "Well,": "voice-alice", "said Alice.": None, "No,": "voice-hatter", "said the Hatter.": None,
    }  # fmt: skip
    pause = out / "silence" / f"silence_{audio.SPEAKER_PAUSE_MS}.mp3"
    info = audio.mp3_info(pause)
    assert info is not None and info.is_standard and info.bit_rate == 48_000
    title_pause = audio.mp3_info(out / "silence" / "silence_1000.mp3")
    clip_info = audio.mp3_info(clip)
    assert title_pause is not None and clip_info is not None
    expected = 5 * clip_info.duration + title_pause.duration + 3 * info.duration  # 3 speaker changes
    chapter_info = audio.mp3_info(result.chapters[0].path)
    assert chapter_info is not None
    assert result.chapters[0].duration_seconds == pytest.approx(expected, abs=1e-9)
    assert chapter_info.duration == pytest.approx(expected, abs=1e-9)
