"""ffmpeg / ffprobe helpers (SPEC §6.8). Every call is a blocking `subprocess.run` (D20); async
callers wrap these functions in `asyncio.to_thread`.
"""

import json
import logging
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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
    "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "libmp3lame", "-b:a", BITRATE, "-threads", "1",
    "-write_xing", "0", "-id3v2_version", "0", "-f", "mp3",
]  # fmt: skip

SPEAKER_PAUSE_MS = 150  # between segments of different speakers inside a paragraph (SPEC §15.6)
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


@dataclass
class ProcessStats:
    """ffmpeg / ffprobe calls made while a `track_processes()` block is active (per chapter)."""

    ffmpeg_calls: int = 0
    ffprobe_calls: int = 0
    seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, tool: str, seconds: float) -> None:
        with self._lock:
            if tool == FFPROBE:
                self.ffprobe_calls += 1
            else:
                self.ffmpeg_calls += 1
            self.seconds += seconds


# Context-local, so concurrent narrations never share counters; asyncio.to_thread copies the
# context, so calls made in worker threads are counted too.
_stats: ContextVar[ProcessStats | None] = ContextVar("audio_process_stats", default=None)


@contextmanager
def track_processes() -> Iterator[ProcessStats]:
    stats = ProcessStats()
    token = _stats.set(stats)
    try:
        yield stats
    finally:
        _stats.reset(token)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    start = time.perf_counter()
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True)
    finally:
        stats = _stats.get()
        if stats is not None:
            stats.add(args[0], time.perf_counter() - start)


def run_ffmpeg(args: list[str]) -> None:
    # One thread per call: the server is a small VM, and these calls are short and many.
    _run([FFMPEG, "-hide_banner", "-threads", "1", "-nostdin", "-loglevel", "error", "-y", *args])


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


# MPEG audio frame header tables (Layer III). Index 0 = "free", 15 = invalid.
_BITRATES_KBPS = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),  # MPEG-1
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2 and 2.5
}
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}  # by version bits


def _id3v2_size(data: bytes) -> int:
    """Length of a leading ID3v2 tag (0 if none)."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
    return 10 + size + (10 if data[5] & 0x10 else 0)


def mp3_info(path: Path) -> AudioInfo | None:
    """Format and duration of an MP3 by walking its frame headers, without ffprobe.

    Duration = frames x samples-per-frame / sample rate (576 / 24000 = 24 ms per frame for ours).
    Counting frames is exact for what a decoder plays; a size-based estimate (bytes x 8 / bitrate)
    is only right when the file holds nothing but audio frames of one size, and is thrown off by an
    ID3 tag, a Xing/Info header frame (both appear in ffmpeg-joined files) or padding bits. Our files (edge-tts output and our own
    libmp3lame encodes) are CBR Layer III with no Xing/Info frame, for which this matches ffprobe.
    A leading Xing/Info frame (ffmpeg's mp3 muxer writes one in joined files) carries no audio and
    is skipped. Returns None for anything unexpected (not Layer III, a VBRI header, a broken
    frame, mixed sample rates), and callers then fall back to ffprobe.
    """
    data = path.read_bytes()
    pos = _id3v2_size(data)
    end = len(data) - (128 if data[-128:-125] == b"TAG" else 0)
    frames = 0
    header_frame_checked = False
    sample_rate = channels = 0
    bitrates: set[int] = set()
    samples_per_frame = 0
    while pos + 4 <= end:
        b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
        if data[pos] != 0xFF or b1 & 0xE0 != 0xE0:
            return None  # lost frame sync
        version = (b1 >> 3) & 0x03  # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5, 1 = reserved
        layer = (b1 >> 1) & 0x03  # 1 = Layer III
        bitrate_index = b2 >> 4
        rate_index = (b2 >> 2) & 0x03
        if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
            return None
        rate = _SAMPLE_RATES[version][rate_index]
        kbps = _BITRATES_KBPS[1 if version == 3 else 2][bitrate_index]
        mono = (b3 >> 6) == 3
        coefficient = 144 if version == 3 else 72
        size = coefficient * kbps * 1000 // rate + ((b2 >> 1) & 0x01)
        if not header_frame_checked:
            header_frame_checked = True
            sample_rate, channels = rate, 1 if mono else 2
            samples_per_frame = 1152 if version == 3 else 576
            side_info = (17 if mono else 32) if version == 3 else (9 if mono else 17)
            tag_at = pos + 4 + (0 if b1 & 0x01 else 2) + side_info
            if data[pos + 36 : pos + 40] == b"VBRI":
                return None
            if data[tag_at : tag_at + 4] in (b"Xing", b"Info"):
                pos += size  # header frame, no audio
                continue
        elif rate != sample_rate:
            return None
        if pos + size > end:
            break  # truncated last frame: not counted
        bitrates.add(kbps)
        frames += 1
        pos += size
    if frames == 0:
        return None
    duration = frames * samples_per_frame / sample_rate
    bit_rate = next(iter(bitrates)) * 1000 if len(bitrates) == 1 else int((end - _id3v2_size(data)) * 8 / duration)
    del data
    return AudioInfo(CODEC, sample_rate, channels, bit_rate, duration)


def fast_probe(path: Path) -> AudioInfo:
    """`mp3_info` when it can read the file, else ffprobe."""
    info = mp3_info(path) if path.suffix.lower() == ".mp3" else None
    if info is None:
        logger.debug("Frame parse not possible for %s; using ffprobe", path.name)
        return probe(path)
    return info


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
        info = infos.get(part) or fast_probe(part)
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
