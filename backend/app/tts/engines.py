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

logger = logging.getLogger(__name__)

EDGE_BACKOFF_SECONDS = (1.0, 2.0, 4.0)  # SPEC §6.7; delay after failed attempt 1, 2, (3)


class TTSEngine(Protocol):
    name: str

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None: ...


class SynthesisError(RuntimeError):
    pass


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


class PiperEngine:
    """Local Piper voice. The model is loaded once (lazily, thread-safe); synthesis and the
    WAV -> MP3 re-encode run in a worker thread so the event loop stays free."""

    name = "piper"

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._voice = None
        self._load_lock = threading.Lock()
        # espeak-ng (used by Piper for phonemes) is not documented as thread-safe: one synthesis
        # at a time. The ffmpeg re-encode runs outside this lock.
        self._synth_lock = threading.Lock()

    def _get_voice(self):  # noqa: ANN202 - piper.PiperVoice, imported lazily
        if self._voice is None:
            with self._load_lock:
                if self._voice is None:
                    from piper import PiperVoice

                    logger.info("Loading Piper voice %s", self.model_path)
                    self._voice = PiperVoice.load(str(self.model_path))
        return self._voice

    def _synthesize_blocking(self, text: str, out_path: Path) -> None:
        wav_path = out_path.with_name(out_path.name + ".wav")
        try:
            voice = self._get_voice()
            with self._synth_lock, wave.open(str(wav_path), "wb") as wav:
                voice.synthesize_wav(text, wav)
            audio.encode_standard(wav_path, out_path)
            _check_output(out_path, self.name)
        finally:
            wav_path.unlink(missing_ok=True)

    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None:
        # Piper has a single narrator voice; other voice ids are ignored (SPEC §6.7).
        _require_text(text)
        await asyncio.to_thread(self._synthesize_blocking, text, out_path)


class FallbackEngine:
    """SPEC §6.7 policy: each chunk tries the primary up to `max_retries` times with backoff, then
    uses the fallback. After `switch_after` consecutive chunk failures (counted in finish order,
    reset by any primary success) every remaining chunk goes straight to the fallback."""

    name = "fallback"

    def __init__(
        self,
        primary: TTSEngine,
        fallback: TTSEngine,
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
        self.switched = False
        self.used: set[str] = set()

    @property
    def engine_used(self) -> str | None:
        """'edge' | 'piper' | 'mixed' over every chunk so far (None before the first)."""
        if not self.used:
            return None
        return next(iter(self.used)) if len(self.used) == 1 else "mixed"

    def _record(self, success: bool) -> None:
        with self._lock:
            if success:
                self.consecutive_failures = 0
                return
            self.consecutive_failures += 1
            if not self.switched and self.consecutive_failures >= self.switch_after:
                self.switched = True
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
        if not self.switched:
            if await self._try_primary(text, out_path, voice):
                self._record(True)
                self.used.add(self.primary.name)
                return self.primary.name
            # The chunk counts as failed unless the job switched while it was retrying.
            if not self.switched:
                self._record(False)
                logger.warning(
                    "Using %s for %s (%s failed %d times; %d consecutive chunk failures)",
                    self.fallback.name,
                    out_path.name,
                    self.primary.name,
                    self.max_retries,
                    self.consecutive_failures,
                )
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


def build_engine(settings: Settings, kind: str = "fallback") -> TTSEngine:
    """'edge' | 'piper' | 'fallback' (edge with Piper fallback, the normal production setup)."""
    edge = EdgeTTSEngine(settings.edge_voice)
    if kind == "edge":
        return edge
    piper = PiperEngine(settings.piper_model_full_path)
    if kind == "piper":
        return piper
    if kind != "fallback":
        raise ValueError(f"unknown engine {kind!r}")
    return FallbackEngine(
        edge,
        piper,
        max_retries=settings.tts_max_retries,
        switch_after=settings.tts_fallback_after_consecutive_failures,
    )
