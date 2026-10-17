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


# -- duration from MP3 frame headers (no ffprobe per part) ------------------------------------


@pytest.mark.parametrize("ms", [audio.PARAGRAPH_PAUSE_MS, audio.TITLE_PAUSE_MS, audio.CHAPTER_PAUSE_MS, 37, 5000])
def test_frame_duration_matches_ffprobe_for_silence(tmp_path: Path, ms: int) -> None:
    clip = audio.make_silence(ms, tmp_path / f"s{ms}.mp3")
    parsed, probed = audio.mp3_info(clip), audio.probe(clip)
    assert parsed is not None
    assert abs(parsed.duration - probed.duration) <= 0.05
    assert parsed.format_key == probed.format_key == ("mp3", 24000, 1) and parsed.bit_rate == 48000


@pytest.mark.parametrize("seconds", [0.7, 3.3, 41.0])
def test_frame_duration_matches_ffprobe_for_speech_like_audio(tmp_path: Path, seconds: float) -> None:
    # A modulated tone through the same encode path as Piper output (WAV -> standard MP3).
    wav = tmp_path / "speech.wav"
    audio.run_ffmpeg(
        ["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=22050,tremolo=f=4:d=0.9", "-t", str(seconds), str(wav)]
    )
    mp3 = audio.encode_standard(wav, tmp_path / "speech.mp3")
    parsed, probed = audio.mp3_info(mp3), audio.probe(mp3)
    assert parsed is not None and parsed.is_standard
    assert abs(parsed.duration - probed.duration) <= 0.05


def test_frame_duration_of_joined_parts(tmp_path: Path) -> None:
    parts = [audio.make_silence(ms, tmp_path / f"p{ms}.mp3") for ms in (400, 1000, 2000)]
    joined = audio.concat(parts, tmp_path / "joined.mp3")
    parsed = audio.mp3_info(joined)
    assert parsed is not None
    assert abs(parsed.duration - audio.probe(joined).duration) <= 0.05
    assert parsed.duration == pytest.approx(sum(audio.mp3_info(p).duration for p in parts), abs=1e-9)


def test_other_formats_and_junk(tmp_path: Path) -> None:
    other = tmp_path / "other.mp3"  # 44.1 kHz stereo 128k with the encoder's Xing/LAME frame
    audio.run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-c:a", "libmp3lame", str(other)])
    parsed = audio.mp3_info(other)
    assert parsed is not None and parsed.format_key == ("mp3", 44100, 2) and not parsed.is_standard
    assert abs(parsed.duration - audio.probe(other).duration) <= 0.06  # LAME tag trims encoder delay
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not an mp3 at all" * 10)
    assert audio.mp3_info(junk) is None
    with pytest.raises(Exception):  # noqa: B017, PT011 - ffprobe fallback rejects it too
        audio.fast_probe(junk)


def test_id3_tag_is_skipped(tmp_path: Path) -> None:
    clip = audio.make_silence(1000, tmp_path / "s.mp3")
    tagged = tmp_path / "tagged.mp3"
    tag = b"ID3\x03\x00\x00" + bytes([0, 0, 0, 20]) + b"\x00" * 20
    tagged.write_bytes(tag + clip.read_bytes())
    assert audio.mp3_info(tagged).duration == pytest.approx(audio.mp3_info(clip).duration)
