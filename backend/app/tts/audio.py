"""ffmpeg / ffprobe helpers (SPEC §6.8). Every call is a blocking `subprocess.run` (D20); async
callers wrap these functions in `asyncio.to_thread`.
"""

import json
import logging
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# The one audio format used everywhere (edge-tts' native output, SPEC §6.7).
CODEC = "mp3"
SAMPLE_RATE = 24_000
CHANNELS = 1
BITRATE = "48k"
# libmp3lame options: no Xing/Info header frame and no ID3 tag, so parts can be joined with
# `-c copy` without a stray header frame in the middle of the file.
_ENCODE_ARGS = [
    "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "libmp3lame", "-b:a", BITRATE,
    "-write_xing", "0", "-id3v2_version", "0", "-f", "mp3",
]  # fmt: skip

PARAGRAPH_PAUSE_MS = 400
TITLE_PAUSE_MS = 1000
CHAPTER_PAUSE_MS = 2000


@dataclass(frozen=True)
class AudioInfo:
    codec: str
    sample_rate: int
    channels: int
    bit_rate: int
    duration: float

    @property
    def format_key(self) -> tuple[str, int, int]:
        return (self.codec, self.sample_rate, self.channels)

    @property
    def is_standard(self) -> bool:
        return self.format_key == (CODEC, SAMPLE_RATE, CHANNELS)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def run_ffmpeg(args: list[str]) -> None:
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args])


def probe(path: Path) -> AudioInfo:
    """Codec, sample rate, channels, bit rate and duration of the first audio stream."""
    out = _run(
        [
            FFPROBE, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate:format=duration",
            "-of", "json", str(path),
        ]
    )  # fmt: skip
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    return AudioInfo(
        codec=stream["codec_name"],
        sample_rate=int(stream["sample_rate"]),
        channels=int(stream["channels"]),
        bit_rate=int(stream.get("bit_rate") or 0),
        duration=float(data["format"]["duration"]),
    )


def _tmp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.stem}.{uuid.uuid4().hex[:8]}.tmp{path.suffix}")


def encode_standard(src: Path, out_path: Path) -> Path:
    """Re-encode any audio file to the standard MP3 format (written atomically)."""
    tmp = _tmp_sibling(out_path)
    run_ffmpeg(["-i", str(src), *_ENCODE_ARGS, str(tmp)])
    tmp.replace(out_path)
    return out_path


def make_silence(ms: int, out_path: Path) -> Path:
    """A silent clip in the standard format."""
    tmp = _tmp_sibling(out_path)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
            "-t", f"{ms / 1000:.3f}", *_ENCODE_ARGS, str(tmp),
        ]
    )  # fmt: skip
    tmp.replace(out_path)
    return out_path


def silence_clip(ms: int, folder: Path) -> Path:
    """Cached silence clip `folder/silence_<ms>.mp3` (created on first use)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"silence_{ms}.mp3"
    if not path.exists():
        make_silence(ms, path)
    return path


def concat_list_line(path: Path) -> str:
    """One concat-demuxer line: absolute path, forward slashes, single-quoted, `'` escaped (§6.8)."""
    posix = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{posix}'"


def concat(parts: list[Path], out_path: Path, infos: dict[Path, AudioInfo] | None = None) -> Path:
    """Join parts with the concat demuxer and `-c copy`.

    All parts must share codec / sample rate / channels; any part that differs from the standard
    format is first re-encoded (with a warning) so the copy-join stays valid.
    """
    if not parts:
        raise ValueError("concat needs at least one part")
    infos = infos or {}
    fixed: list[Path] = []
    temporaries: list[Path] = []
    for part in parts:
        info = infos.get(part) or probe(part)
        if info.is_standard:
            fixed.append(part)
            continue
        logger.warning("Re-encoding %s (%s) to the standard format before joining", part, info.format_key)
        converted = encode_standard(part, _tmp_sibling(out_path).with_suffix(".part.mp3"))
        temporaries.append(converted)
        fixed.append(converted)

    list_file = _tmp_sibling(out_path).with_suffix(".txt")
    tmp_out = _tmp_sibling(out_path)
    list_file.write_text("\n".join(concat_list_line(p) for p in fixed) + "\n", encoding="utf-8")
    try:
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", "-f", "mp3", str(tmp_out)])
        tmp_out.replace(out_path)
    finally:
        list_file.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
        for t in temporaries:
            t.unlink(missing_ok=True)
    return out_path
