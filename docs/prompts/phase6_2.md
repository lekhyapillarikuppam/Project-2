# Phase 6.2: Speaker attribution, cast, voices, multi-voice narration

Read these first:
- `CLAUDE.md`
- `docs/DECISIONS.md`: D31–D40. **D39 is the selection rule.** D25 and D35 cover the held-out set.
- `docs/SPEC.md` revision 10, §15.2–§15.7 and §15.9
- `testset_dialogue/results.md`

This phase merges the old 6.2 and 6.3. It is backend and scripts only: no job, API or UI changes (those come in 6.3).

**Held-out rule:** never run anything on `ball_ch03`, and never open its text or labels. Its first evaluation happens once, in 6.3, after everything in this phase is frozen.

**Voice vetoes from the user:** none. If the user adds names here, remove them from `voice_pool.json` before step 4.

## 0. Gate
1. Run `python scripts\label_dialogue.py --lint` on both dev chapters. It must report no errors.
2. Run `python scripts\evaluate_dialogue.py`.
   - Targets: F1 ≥ 0.95 on each dev chapter, and grouping ≥ 0.9.
   - If a target is missed, analyse the error listing as in 6.1.
   - Fix it only with **general** rules, and report every change.
   - If the cause is a label, don't edit it. Stop and tell the user which ones.

## 1. Baseline attribution: `app/dialogue/attribute.py` (SPEC §15.3)
- Implement the rules exactly as listed: speech-verb list, explicit tags, pronoun tags, turn-taking, everything else UNKNOWN.
- Each prediction also records **why** it was made: `explicit`, `pronoun`, `turn`, `unknown`. This tells `llm_with_baseline_override` which predictions are explicit.
- `rank_characters(document)` is the fast whole-book pass from §15.5 (line counts per character, no LLM).

## 2. LLM attribution
**Files:**
- `app/dialogue/llm.py`: Groq client over `httpx`, token limiter, header handling, cache, usage records.
- `app/dialogue/windows.py`: windowing. A window never splits an utterance. It carries 2 context paragraphs without ids, and quotes are marked inline as `[Qn]`.
- The prompt, as a constant in `attribute.py`. It covers the terse format, the running list capped at 30, and the rule 5a conventions.

**Validation:** every id answered exactly once. On failure, retry once with the error appended. If that also fails, use the baseline for the window (D34).

**Limiter:** token-based (`LLM_TOKENS_PER_MINUTE=7000`, `LLM_MAX_REQUESTS_PER_DAY=800`). It also honours the `x-ratelimit-*` headers, `retry-after` and 429. A window that waits more than 120 s falls back to the baseline.

**New config settings:** `LLM_WINDOW_WORDS=1200`, `LLM_TOKENS_PER_MINUTE=7000`, `ATTRIBUTION_METHOD` (set from D40 at the end), `LLM_CACHE_DIR` (eval only, default `storage/llm_cache`, gitignored). Add them to `.env.example`. Remove the now-unused `LLM_MAX_REQUESTS_PER_MINUTE`.

**Reasoning settings.** Verify each of these with a real request rather than trusting docs or memory:
- **qwen:** turn thinking off with the Groq parameter for Qwen3 (expected: `reasoning_effort: "none"`). Confirm that completion tokens actually drop.
- **gpt-oss:** use the lowest reasoning effort (expected: `"low"`). Thinking can't be turned off.
- Report completion tokens per window with thinking on and off for qwen on one Alice window. This is a reported result.
- If a model id is no longer listed on `/models`, use its current successor and report it.

**Tests:** use `httpx.MockTransport` and a fake clock. Cover validation and retry, the baseline fallback, the limiter budget and headers, 429 with retry-after, the daily cap, the cache, windowing (never splits a broken or multi-paragraph quote), and the context paragraphs carrying no ids.

## 3. Cast and voices
**Files:**
- `app/dialogue/cast.py` (§15.4)
- `app/dialogue/voices.py` (§15.5)

**Voice assignment:**
- Ranking comes from `rank_characters`.
- New LLM characters get the next free voice.
- Voices never change once assigned.
- UNKNOWN speakers use the narrator voice.
- Assignment is deterministic.

**Tests:** alias and subset merging, gender conflicts, the pool running out leading to minor voices, determinism.

## 4. Multi-voice narration (§15.6)
- **Chunking:** a chunk never mixes speakers, and each chunk carries its own voice. The edge engine takes the voice per chunk.
- **Pauses:** 150 ms of silence between segments with different speakers inside a paragraph. Make it a pre-generated silent MP3 in the exact output format (24 kHz mono 48 kbps CBR) so the concat demuxer `-c copy` and the frame-based durations still work. Add a test that durations stay exact.
- **Text:** quote marks are stripped before TTS, and chapter titles are always read by the narrator.
- **Single-narrator mode must be byte-for-byte unaffected.** All v1 tests pass unchanged.
- **Script:** `scripts\narrate.py` gains `--mode characters` (default `single`) and `--chapters N[,M]`. In characters mode it writes the chapter MP3s plus `<name>_attributed.md` (every quote with its paragraph, predicted speaker, method and reason, and voice label) and `<name>_cast.md`.

## 5. Evaluation and selection (§15.9, D39)
**Extend `evaluate_dialogue.py`:**
- Attribution accuracy: overall, explicit and implicit.
- % predicted UNKNOWN.
- Voice accuracy, using the system's own voice map, with each dev chapter treated as a whole book for ranking.
- LLM requests, prompt and completion tokens, and median and total latency.
- `--methods` and `--models` flags.
- Score only on spans that matched in detection, as in the spec. Also report how many gold utterances were missed by detection.

**Runs:**
- Run every D39 candidate on both dev chapters.
- Both models get the **same prompt**.
- Use the disk cache so re-scoring is free. Keep total Groq use well inside the daily limits you read from the headers.

**Tuning:**
- You may improve the prompt or the baseline rules while looking at dev errors.
- Keep changes general, report every one with the error it fixes, and re-run **all** candidates after each change.
- At most 3 prompt revisions.

**Selection:**
- Apply D39 mechanically to the final runs.
- Write D40 into `docs/DECISIONS.md`: model id, method, a one-line scores summary, and the date and commit. Add a change-log line.
- Set `LLM_MODEL` and `ATTRIBUTION_METHOD` defaults in `config.py` and `.env.example`.

**Results:** write `testset_dialogue/results.md` with the detection table (as before), a full candidates table and an error listing for the chosen candidate. Use `--history` once on the final dev run.

## 6. Listening test output
Run the chosen setup:
```
python scripts\narrate.py ..\testset\docs\01_novel.pdf --mode characters --chapters 7
```
Output goes to `testset/output/characters/` (gitignored), with the MP3, `_attributed.md` and `_cast.md`. Also do Brown story 1 from `05_styles.docx`.

## 7. Report and commit
Include in the report:
- Gate result.
- Files changed.
- Test counts.
- The verified Groq parameters and the thinking-on/off token numbers.
- The full candidates table and the D39 decision.
- Every prompt and rule change and why.
- Spec interpretations.
- The paths of the listening files.

Commit `Phase 6.2: attribution, cast, voices, multi-voice narration` and push. **Stop. Do not start 6.3, and do not run anything on ball_ch03.**
