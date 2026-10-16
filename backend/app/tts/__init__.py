"""Text-to-speech: normalize -> chunk -> synthesize -> assemble (SPEC §6.5-6.8)."""

from app.tts.engines import build_engine
from app.tts.narrator import NarrationResult, ProgressEvent, build_full_book, narrate_document

__all__ = ["NarrationResult", "ProgressEvent", "build_engine", "build_full_book", "narrate_document"]
