# Phase 2 — Text-to-speech: normalize, chunk, synthesize, assemble

Read first: `CLAUDE.md`, `docs/DECISIONS.md`, `docs/SPEC.md` (now **revision 4**): §3 limits, §5 threading model, §6.5–6.8, §7.1, §14.
Implement **Phase 2 only**: no API endpoints, no job queue, no frontend. The held-out rule (D25) still applies: don't open `testset_heldout/` except where step 0 says so.

---

## 0. Housekeeping from Phase 1b (do first, commit separately)
1. `clean.py`: narrow the compound-hyphen rule to SPEC rev 4 (keep the hyphen only if the hyphenated form appears inside a line elsewhere; remove the "both halves are words" clause and `COMPOUND_MIN_PREFIX`). Update the unit tests. Re-run your 95-split listing on `01_novel` and report kept / joined counts.
2. `titles.py`: strip a leading bare arabic/roman number + `.` + space during normalization (SPEC rev 4 §13). Add tests: `"XVII. THE IDIOT"` ≈ `"The Idiot"`, `"3. Convexity"` ≈ `"Convexity"`, and `"I. A Discussion"` ≈ `"A Discussion"`.
3. `pytest -q`; `python scripts\evaluate.py` (dev) — must stay F1 1.00 / 3/3 / noise 0.
4. Commit: `Phase 1c: narrower compound-hyphen rule, bare-number title prefix`.
5. Re-run the held-out set **with history**: `python scripts\evaluate.py --testset ..\testset_heldout --history`. Commit `Held-out evaluation, second run (after rev 4 scoring change)`. Report the new overall line. The first-run 0.99 remains the reported held-out number.

---

## 1. Verify the installed libraries before writing code
Don't trust any API names in this prompt or the spec blindly. Check the installed versions (`edge-tts==7.3.1`, `piper-tts==1.8.0`):
- edge-tts: confirm how to synthesize to a file with a given voice, and the exact output format (expected: MP3, 24 kHz, mono, 48 kbps). Run `ffprobe` on a real output and report codec, sample rate, channels, bit rate.
- piper-tts: find the current Python API for loading a voice and synthesizing to WAV, and the official way to download a voice. Download `en_US-lessac-medium` (`.onnx` + `.onnx.json`) into `backend\models\` (gitignored). Confirm `check_env.py` passes and add a Piper check to it (load the model, synthesize one sentence).
Report what you found in one short paragraph.

## 2. Files to create
```
backend/app/tts/__init__.py
backend/app/tts/normalize.py     (§6.5)
backend/app/tts/chunk.py         (§6.6)
backend/app/tts/engines.py       (TTSEngine protocol, EdgeTTSEngine, PiperEngine, FallbackEngine)
backend/app/tts/audio.py         (ffmpeg/ffprobe helpers, silence clips, concat; all via subprocess.run)
backend/app/tts/narrator.py      (narrate_document — the orchestrator Phase 3 will call)
backend/scripts/narrate.py
backend/tests/test_normalize.py
backend/tests/test_chunk.py
backend/tests/test_narrator.py   (fake engines; no network)
backend/tests/test_audio.py      (needs ffmpeg; skip if missing)
testset/tts_samples.txt          (tricky sentences for a listening test, see §5)
```

## 3. Module contracts

### `normalize.py`
`normalize_for_speech(text: str) -> str` and `normalize_heading(title: str) -> str`, rules from §6.5. Use `num2words`. Must handle at least:
- integers with commas (`1,250` → "one thousand two hundred and fifty" is fine), decimals (`3.14`), percentages, ordinals (`21st`), years (`1865` → "eighteen sixty-five"; treat 4-digit numbers between 1100 and 2099 as years unless they have a comma or currency), currency `₹ $ € £` (`$5.50` → "five dollars fifty cents" or "five dollars and fifty cents"), simple ranges (`10–20` → "ten to twenty").
- abbreviations listed in §6.5 (`Dr.` → "Doctor", `Mr.` → "Mister", `e.g.` → "for example", `i.e.` → "that is", `etc.` → "et cetera", `vs.` → "versus", `St.` → "Saint" before a capitalised name, otherwise "Street"; `No.` → "number" only before a digit).
- roman numerals **only in headings** (`Chapter XII: Alice's Evidence` → "Chapter twelve. Alice's Evidence"). Never convert roman numerals in body text (the word "I" must survive).
- URLs → "link", emails dropped, stray `* # | • _ ~` removed, `&` → "and", repeated punctuation collapsed.
- ALL-CAPS words of 4+ letters in body text (Alice uses "VERY") → lowercase so the voice doesn't spell them out; leave 2–3 letter caps (likely acronyms: "AI", "USA") alone.
Keep every rule a small named function; test each.

### `chunk.py`
`chunk_chapter(chapter: Chapter, max_chars: int) -> list[Chunk]` where `Chunk(chapter_index, seq, text, paragraph_index, ends_paragraph: bool, is_title: bool)`.
- Chunk 0 of each chapter is the spoken title (`normalize_heading`), `is_title=True`.
- Sentence splitting must not break on `Dr.`, `Mr.`, `e.g.`, decimals, or initials (`G. K. Chesterton`). Use a regex-based splitter with an abbreviation list; no new dependencies.
- Pack sentences up to `max_chars`; never split a sentence unless it alone exceeds `max_chars` (then split at `;` `,` or spaces, §6.6).
- A chunk never spans two paragraphs (so pauses land correctly).

### `engines.py`
```python
class TTSEngine(Protocol):
    name: str
    async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None: ...
```
- `EdgeTTSEngine(voice)`.
- `PiperEngine(model_path)`: model loaded **once** (lazy, thread-safe), synthesis + WAV→MP3 re-encode via `asyncio.to_thread`. Output MP3 must have the **same codec parameters as edge-tts output** (verify with ffprobe in a test).
- `FallbackEngine(primary, fallback, max_retries, backoff, switch_after)`: implements SPEC §6.7 exactly — 3 attempts with 1/2/4 s backoff, then Piper for that chunk; a lock-protected consecutive-failure counter updated in finish order; reset on any edge success; at 5, all remaining chunks go straight to Piper. Exposes `engine_used` per call and overall (`edge | piper | mixed`). Backoff delays must be injectable so tests run instantly.
- Empty / whitespace-only text never reaches an engine.

### `audio.py`
- `probe(path) -> AudioInfo(codec, sample_rate, channels, bit_rate, duration)` via ffprobe JSON.
- `make_silence(ms, out_path)` matching the speech MP3 parameters; cache the 400 / 1000 / 2000 ms clips per output folder.
- `concat(parts: list[Path], out_path)` with the concat demuxer, `-safe 0`, forward-slash absolute paths, `'` escaping (§6.8), `-c copy`. Before concatenating, verify all parts have identical codec/sample rate/channels; if not, re-encode the odd ones to the standard format first and log a warning.
- Everything via `subprocess.run([...], check=True, capture_output=True)`. Never `asyncio.create_subprocess_exec` (D20).

### `narrator.py`
```python
async def narrate_document(
    doc: DocumentModel,
    out_dir: Path,
    engine: TTSEngine,
    settings: Settings,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> NarrationResult
```
- Chapters strictly in order; inside a chapter, up to `TTS_CONCURRENCY` chunks at once (`asyncio.Semaphore`); chunks from different chapters never overlap (§6.7).
- Chunk files go in `out_dir/work/ch_001/0001.mp3`…; a chapter file is `out_dir/chapters/ch_001.mp3` = title, 1000 ms, then paragraphs with 400 ms between paragraphs (§6.8).
- Chapter duration = sum of measured parts; compare with ffprobe of the joined file, warn if > 1 s apart (§6.8).
- A chapter is `ready` only after its MP3 exists. If a chapter fails entirely (every chunk failed on both engines), mark it `failed` and continue with the next.
- Delete `work/ch_NNN/` after the chapter is assembled (disk use on Render).
- `build_full_book(result, out_dir) -> Path`: `full.mp3` = all ready chapters with 2000 ms between them (lazy; Phase 3 calls it on first download).
- `ProgressEvent` carries: `kind` (`chunk_done | chapter_ready | chapter_failed`), `chapter_index`, `chars_done`, `chars_total`, `engine`, and for chapter events the path and duration. Phase 3 will turn these into the job object (§7.2), so include whatever it needs.
- `NarrationResult`: per-chapter status, path, duration, engine used; overall engine (`edge | piper | mixed`); total chars; wall time; chars/second.

### `scripts/narrate.py`
```
python scripts\narrate.py <document.json | .pdf | .docx> --out <dir> [--chapters 1,3-4] [--engine edge|piper|fallback] [--fail-edge N] [--full]
```
- Accepts a `document.json` from `extract.py --out`, or a PDF/DOCX (then runs the Phase 1 pipeline first).
- `--chapters` limits which chapters are narrated (for quick tests).
- `--engine` default `fallback`.
- `--fail-edge N` (dev only): makes the edge engine raise on its first N calls, to demonstrate the fallback and the switch-to-Piper rule.
- `--full` also builds `full.mp3`.
- Prints a live progress line per chapter and a final summary: per chapter (title, status, duration, engine), total audio duration, wall time, chars/second, peak RSS memory (use `psutil` if already installed; otherwise report "n/a" — do **not** add a dependency without saying so).
- Writes `out_dir/manifest.json` with the `NarrationResult`.

## 4. Tests (no network in unit tests)
- `test_normalize.py`: one test per rule, including the "I must survive" and "VERY → very" cases.
- `test_chunk.py`: abbreviations and initials don't split; long-sentence splitting; chunks never cross paragraphs; title chunk first.
- `test_narrator.py`: fake engines that write a tiny valid MP3 (generate one with ffmpeg once per session, or copy a fixture made by `make_silence`). Cover: chapter order with concurrency; fallback per chunk; switch-to-Piper after 5 consecutive failures (with instant backoff); a success resets the counter; a fully failed chapter is `failed` and the next still runs; progress events in the right order; `work/` folder cleaned.
- `test_audio.py`: silence clips have the standard parameters; concat of three parts has the expected duration (±0.1 s); a path with spaces and an apostrophe works.

## 5. Listening test material
Create `testset/tts_samples.txt` (committed) with ~15 short lines covering: numbers, years, currency, percentages, ordinals, abbreviations, initials, a URL, an ALL-CAPS word, a roman-numeral heading, a sentence with "I", quotes and an em dash, a very long sentence. Narrate it with **both** engines into `testset/output/tts_samples_edge.mp3` and `..._piper.mp3` (add a `--text-file` option to `narrate.py` for this, treating the file as one chapter with one paragraph per line).

## 6. Acceptance
Run and report:
1. `pytest -q` — all pass, no warnings.
2. `python scripts\narrate.py ..\testset\docs\01_novel.pdf --out ..\testset\output\alice --full` (all 12 chapters, fallback engine). Report the summary: total audio duration, wall time, chars/second, peak memory, engine used. Check `ffprobe` duration of `full.mp3` ≈ sum of chapters + 11 × 2 s.
3. `python scripts\narrate.py ..\testset\docs\01_novel.pdf --out ..\testset\output\alice_piper --chapters 1 --engine piper` — report Piper chars/second on your laptop.
4. `python scripts\narrate.py ..\testset\docs\01_novel.pdf --out ..\testset\output\alice_fail --chapters 1-2 --fail-edge 20` — show the log lines proving: per-chunk fallback, the switch to Piper after 5 consecutive failures, and `engine_used = mixed`.
5. The two `tts_samples` files exist.
6. Estimate, from the measured chars/second, the generation time for a 200-page / 100,000-word book with edge-tts and with Piper only.

## 7. Report
- Files created/changed; library findings from step 1.
- Results of steps 1–6.
- Anything in SPEC §6.5–6.8 that you had to interpret or that didn't work as written.
- Commit `Phase 2: TTS normalize, chunk, engines, assembly, narrate CLI` and push.

Then **stop**. I (the user) will listen to `tts_samples_edge.mp3`, `tts_samples_piper.mp3` and chapter 1 of Alice before Phase 2b (Render deployment check).
