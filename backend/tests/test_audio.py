import shutil
from pathlib import Path

import pytest

from app.tts import audio

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not on PATH"
)


def test_silence_clips_have_standard_parameters(tmp_path: Path) -> None:
    for ms in (audio.PARAGRAPH_PAUSE_MS, audio.TITLE_PAUSE_MS, audio.CHAPTER_PAUSE_MS):
        info = audio.probe(audio.silence_clip(ms, tmp_path))
        assert (info.codec, info.sample_rate, info.channels, info.bit_rate) == ("mp3", 24000, 1, 48000)
        # MP3 frames are 24 ms and the encoder adds a padding frame, so clips run up to ~70 ms long.
        assert ms / 1000 <= info.duration < ms / 1000 + 0.1


def test_silence_clip_is_cached(tmp_path: Path) -> None:
    first = audio.silence_clip(400, tmp_path)
    mtime = first.stat().st_mtime_ns
    assert audio.silence_clip(400, tmp_path) == first
    assert first.stat().st_mtime_ns == mtime


def test_concat_three_parts_duration(tmp_path: Path) -> None:
    parts = [audio.make_silence(ms, tmp_path / f"p{ms}.mp3") for ms in (500, 1000, 1500)]
    expected = sum(audio.probe(p).duration for p in parts)
    out = audio.concat(parts, tmp_path / "joined.mp3")
    assert abs(audio.probe(out).duration - expected) <= 0.1
    assert 3.0 <= expected < 3.0 + 3 * 0.1


def test_concat_path_with_spaces_and_apostrophe(tmp_path: Path) -> None:
    folder = tmp_path / "Alice's book folder"
    folder.mkdir()
    parts = [audio.make_silence(300, folder / f"part {i}'s.mp3") for i in range(2)]
    out = audio.concat(parts, folder / "it's joined.mp3")
    assert abs(audio.probe(out).duration - sum(audio.probe(p).duration for p in parts)) <= 0.1
    assert not list(folder.glob("*.txt")), "concat list file must be removed"


def test_concat_list_line_escaping(tmp_path: Path) -> None:
    line = audio.concat_list_line(tmp_path / "it's.mp3")
    assert line.startswith("file '") and line.endswith("it'\\''s.mp3'")
    assert "\\\\" not in line and line.count(":\\") == 0  # forward slashes only


def test_concat_reencodes_non_standard_parts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    odd = tmp_path / "odd.mp3"
    audio.run_ffmpeg(
        ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-c:a", "libmp3lame", "-b:a", "128k", str(odd)]
    )
    good = audio.make_silence(1000, tmp_path / "good.mp3")
    out = audio.concat([good, odd], tmp_path / "mixed.mp3")
    info = audio.probe(out)
    assert info.is_standard
    assert abs(info.duration - 2.0) <= 0.15
    assert "Re-encoding" in caplog.text


def test_encode_standard_matches_format(tmp_path: Path) -> None:
    wav = tmp_path / "tone.wav"
    audio.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", "0.5", str(wav)])
    info = audio.probe(audio.encode_standard(wav, tmp_path / "tone.mp3"))
    assert (info.codec, info.sample_rate, info.channels, info.bit_rate) == ("mp3", 24000, 1, 48000)
