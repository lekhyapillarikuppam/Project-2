"""TTS engines (SPEC §6.7): edge-tts (primary), Piper (local fallback), and the fallback policy."""

import asyncio
import logging
import threading
import wave
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

import edge_tts

from app.config import Settings
from app.tts import audio
from app.tts.chunk import split_text

logger = logging.getLogger(__name__)

EDGE_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)  # SPEC §6.7: delay after failed attempt 1, 2, 3, 4+ (capped)


class TTSEngine(Protocol):
    name: str

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None: ...


class SynthesisError(RuntimeError):
    pass


class ChunkFailed(SynthesisError):
    """A chunk failed on every available engine (its chapter fails, D27)."""


class NarrationUnavailable(SynthesisError):
    """Too many consecutive chunks failed with no fallback: stop narrating the job (D26)."""


def _require_text(text: str) -> None:
    if not text or not text.strip():
        raise ValueError("empty text must never be sent to a TTS engine")


def _check_output(path: Path, engine: str) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise SynthesisError(f"{engine} produced no audio for {path.name}")


class EdgeTTSEngine:
    """Microsoft Edge online voices via the `edge-tts` library (MP3 24 kHz mono 48 kbps)."""

    name = "edge"

    def __init__(self, voice: str) -> None:
        self.voice = voice

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        _require_text(text)
        tmp = out_path.with_name(out_path.name + ".part")
        try:
            await edge_tts.Communicate(text, voice or self.voice).save(str(tmp))
            _check_output(tmp, self.name)
            tmp.replace(out_path)
        finally:
            tmp.unlink(missing_ok=True)


def load_piper_voice(model_path: Path, disable_arena: bool):  # noqa: ANN201 - piper.PiperVoice
    """Load a Piper voice on the CPU. piper-tts 1.8.0's `PiperVoice.load` always builds the
    onnxruntime session with default `SessionOptions`, so this builds the session itself (the
    same way `load` does: config from `<model>.onnx.json`, CPUExecutionProvider) with the CPU
    memory arena and memory pattern optionally disabled."""
    import json

    import onnxruntime
    from piper import PiperVoice
    from piper.config import PiperConfig

    config = json.loads(Path(f"{model_path}.json").read_text(encoding="utf-8"))
    options = onnxruntime.SessionOptions()
    if disable_arena:
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
    session = onnxruntime.InferenceSession(
        str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    return PiperVoice(config=PiperConfig.from_dict(config), session=session)


class PiperEngine:
    """Local Piper voice. The model is loaded once (lazily, thread-safe); synthesis and the
    WAV -> MP3 re-encode run in a worker thread so the event loop stays free. Text longer than
    `max_chars` is synthesized in pieces into one WAV, which bounds peak memory."""

    name = "piper"

    def __init__(self, model_path: Path, disable_arena: bool = True, max_chars: int = 300) -> None:
        self.model_path = model_path
        self.disable_arena = disable_arena
        self.max_chars = max_chars
        self._voice = None
        self._load_lock = threading.Lock()
        # espeak-ng (used by Piper for phonemes) is not documented as thread-safe: one synthesis
        # at a time. The ffmpeg re-encode runs outside this lock.
        self._synth_lock = threading.Lock()

    def _get_voice(self):  # noqa: ANN202 - piper.PiperVoice, imported lazily
        if self._voice is None:
            with self._load_lock:
                if self._voice is None:
                    arena = "off" if self.disable_arena else "on"
                    logger.info("Loading Piper voice %s (memory arena %s)", self.model_path, arena)
                    self._voice = load_piper_voice(self.model_path, self.disable_arena)
        return self._voice

    def _synthesize_blocking(self, text: str, out_path: Path) -> None:
        wav_path = out_path.with_name(out_path.name + ".wav")
        try:
            voice = self._get_voice()
            pieces = split_text(text, self.max_chars)
            with self._synth_lock, wave.open(str(wav_path), "wb") as wav:
                for i, piece in enumerate(pieces):
                    # The WAV format is set by the first piece; later pieces append frames.
                    voice.synthesize_wav(piece, wav, set_wav_format=i == 0)
            audio.encode_standard(wav_path, out_path)
            _check_output(out_path, self.name)
        finally:
            wav_path.unlink(missing_ok=True)

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        # Piper has a single narrator voice; other voice ids are ignored (SPEC §6.7).
        _require_text(text)
        await asyncio.to_thread(self._synthesize_blocking, text, out_path)


class FallbackEngine:
    """SPEC §6.7 policy. Each chunk tries the primary up to `max_retries` times with backoff.

    With a `fallback` engine (TTS_FALLBACK=piper) the chunk then uses the fallback, and after
    `switch_after` consecutive chunk failures every remaining chunk goes straight to the fallback.
    Without one (TTS_FALLBACK=none, D26) the chunk raises `ChunkFailed`, and at `switch_after`
    consecutive failures `NarrationUnavailable` is raised for this and every later chunk.
    The counter is updated in the order chunks finish, under a lock; a primary success resets it.
    """

    def __init__(
        self,
        primary: TTSEngine,
        fallback: TTSEngine | None,
        max_retries: int = 3,
        backoff: Sequence[float] = EDGE_BACKOFF_SECONDS,
        switch_after: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.max_retries = max_retries
        self.backoff = tuple(backoff)
        self.switch_after = switch_after
        self._sleep = sleep
        self._lock = threading.Lock()
        self.consecutive_failures = 0
        self.switched = False  # to the fallback, or (without one) narration unavailable
        self.used: set[str] = set()

    @property
    def name(self) -> str:
        return self.primary.name if self.fallback is None else "fallback"

    @property
    def engine_used(self) -> str | None:
        """'edge' | 'piper' | 'mixed' over every chunk so far (None before the first)."""
        if not self.used:
            return None
        return next(iter(self.used)) if len(self.used) == 1 else "mixed"

    def _unavailable(self) -> NarrationUnavailable:
        return NarrationUnavailable(
            f"{self.primary.name} failed on {self.switch_after} consecutive chunks; narration is unavailable"
        )

    def _record(self, success: bool) -> None:
        with self._lock:
            if success:
                self.consecutive_failures = 0
                return
            self.consecutive_failures += 1
            if not self.switched and self.consecutive_failures >= self.switch_after:
                self.switched = True
                if self.fallback is None:
                    logger.error(
                        "%s failed on %d consecutive chunks: narration unavailable, stopping the job",
                        self.primary.name,
                        self.consecutive_failures,
                    )
                else:
                    logger.warning(
                        "%s failed on %d consecutive chunks: switching the rest of the job to %s",
                        self.primary.name,
                        self.consecutive_failures,
                        self.fallback.name,
                    )

    async def _try_primary(self, text: str, out_path: Path, voice: str | None) -> bool:
        for attempt in range(1, self.max_retries + 1):
            if self.switched:
                return False
            try:
                await self.primary.synthesize(text, out_path, voice)
                return True
            except Exception as e:  # noqa: BLE001 - any engine failure triggers a retry
                logger.warning(
                    "%s attempt %d/%d failed for %s: %s",
                    self.primary.name,
                    attempt,
                    self.max_retries,
                    out_path.name,
                    e,
                )
                if attempt < self.max_retries:
                    await self._sleep(self.backoff[min(attempt - 1, len(self.backoff) - 1)])
        return False

    async def synthesize_tracked(self, text: str, out_path: Path, voice: str | None = None) -> str:
        """Synthesize one chunk and return the engine that produced it ('edge' or 'piper')."""
        _require_text(text)
        if self.switched and self.fallback is None:
            raise self._unavailable()
        if not self.switched:
            if await self._try_primary(text, out_path, voice):
                self._record(True)
                self.used.add(self.primary.name)
                return self.primary.name
            # The chunk counts as failed unless the job switched while it was retrying.
            if not self.switched:
                self._record(False)
                if self.fallback is not None:
                    logger.warning(
                        "Using %s for %s (%s failed %d times; %d consecutive chunk failures)",
                        self.fallback.name,
                        out_path.name,
                        self.primary.name,
                        self.max_retries,
                        self.consecutive_failures,
                    )
            if self.fallback is None:
                if self.switched:
                    raise self._unavailable()
                raise ChunkFailed(f"{self.primary.name} failed {self.max_retries} times for {out_path.name}")
        assert self.fallback is not None
        await self.fallback.synthesize(text, out_path, voice)
        self.used.add(self.fallback.name)
        return self.fallback.name

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        await self.synthesize_tracked(text, out_path, voice)


async def synthesize_tracked(engine: TTSEngine, text: str, out_path: Path) -> str:
    """Synthesize with any engine and return the name of the engine that produced the audio."""
    tracked = getattr(engine, "synthesize_tracked", None)
    if tracked is not None:
        return await tracked(text, out_path)
    await engine.synthesize(text, out_path)
    return engine.name


def build_engine(settings: Settings, kind: str | None = None) -> TTSEngine:
    """The engine for a job. `kind`:
    - 'fallback': edge-tts with retries, then Piper per chunk (TTS_FALLBACK=piper);
    - 'edge': edge-tts with retries and no fallback (TTS_FALLBACK=none, D26);
    - 'piper': Piper only (local measurements).
    Default: from `settings.tts_fallback`."""
    if kind is None:
        kind = "fallback" if settings.tts_fallback == "piper" else "edge"
    if kind == "piper":
        return PiperEngine(settings.piper_model_full_path, settings.piper_disable_arena, settings.piper_max_chars)
    if kind not in ("edge", "fallback"):
        raise ValueError(f"unknown engine {kind!r}")
    fallback = (
        PiperEngine(settings.piper_model_full_path, settings.piper_disable_arena, settings.piper_max_chars)
        if kind == "fallback"
        else None
    )
    return FallbackEngine(
        EdgeTTSEngine(settings.edge_voice),
        fallback,
        max_retries=settings.tts_max_retries,
        switch_after=settings.tts_fallback_after_consecutive_failures,
    )
