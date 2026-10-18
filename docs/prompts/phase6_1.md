# Phase 6.1 — Dialogue segmentation (find the quotes)

Read first: `CLAUDE.md`, `docs/DECISIONS.md` (**D33, D35, D37, D38**, D25), `docs/SPEC.md` **revision 9 §15.2 and §15.9** (quote style, open/close by position, apostrophe lists, multi-paragraph rule with the speech-tag exception, broken quotes, rule 5a conventions, label format).
Prerequisite: the user has finished labelling `alice_ch07` and `brown_glass` (`testset_dialogue/labels/`). If either label file is missing or the tool's validation fails on it, stop and tell the user.

**Held-out rule:** do not run anything on `ball_ch03` in this phase (its first evaluation happens once, in 6.5).

## 0. Housekeeping
- `testset_dialogue/README.md`: the two conventions are now decided (D38) — replace "proposed" wording with the final rules from SPEC §15.2 rule 5a.

## 1. Code
```
backend/app/dialogue/__init__.py
backend/app/dialogue/segment.py      (SPEC §15.2)
backend/tests/test_segment.py
backend/scripts/evaluate_dialogue.py (detection metrics now; attribution metrics added in 6.2)
```
- `app/models.py`: `Segment` gains `kind: Literal["narration","quote"] = "narration"` and `utterance_id: str | None = None`. Fully backwards compatible: single-narrator mode and all v1 code paths still produce one narration segment per paragraph. Existing tests must pass unchanged.
- `segment_chapter(chapter) -> Chapter` (pure): returns the chapter with paragraphs split into narration/quote segments, utterance ids (`U1…`, unique per chapter), quote spans **including** marks, plus a `SegmentStats` (marks seen, apostrophes skipped, unclosed quotes closed, continuations, speech-tag closures).
- `detect_quote_family(document) -> "double" | "single" | None` per rule 1.
- Every rule is a small, named, unit-tested function. The apostrophe lists are named constants at module top.
- No LLM, no TTS, no API or UI changes in this phase.

## 2. Tests (`test_segment.py`, synthetic text only)
Cover at least: curly and straight doubles; curly singles with **swapped glyphs** (`‘I didn’t know it was YOUR table,‘ said Alice; ’it‘s laid…’`); every apostrophe class (between letters incl. `doesn‘t`; leading-elision list; plural possessive outside a quote; trailing elisions incl. `-in'`); nested other-family quotes; broken quotes → one utterance id; multi-paragraph continuation; the speech-tag exception (damaged closing mark); unclosed quote at paragraph end; a paragraph with no dialogue; a document with no dialogue (family `None`).

## 3. Evaluation script (detection part)
`python scripts\evaluate_dialogue.py [--set dev|heldout|all] [--chapters alice_ch07,brown_glass]` (default `--set dev`):
- Loads `testset_dialogue/text/<name>.json` + labels, runs `segment_chapter`, and matches predicted quote spans to gold spans one-to-one (same paragraph, character overlap ≥ 80% of the longer span; greedy by overlap).
- Reports per chapter and micro-averaged: precision, recall, F1; **utterance grouping accuracy** (of gold utterances with ≥ 2 spans, the share whose matched predicted spans share one utterance id, and vice versa); counts of gold `NOT a quote` decisions the segmenter wrongly found.
- Lists every false positive and false negative with paragraph number and a 60-character excerpt (dev only).
- Writes `testset_dialogue/results.md` (dev) — and later `results_heldout.md` + `results_history.md` with `--history`, same pattern as v1's evaluate.py.
- Refuses `--set heldout` unless `--i-know-this-is-the-held-out-run` is passed (guard rail for D25).

## 4. Acceptance (dev only)
1. `pytest -q` — all pass (v1 tests unchanged).
2. `python scripts\evaluate.py` (v1 chapter detection, dev) — unchanged: F1 1.00, 3/3, noise 0.
3. `python scripts\evaluate_dialogue.py` — target **F1 ≥ 0.95 on each dev chapter** and grouping accuracy ≥ 0.9. If a target is missed, analyse the errors (from the listing) and fix with **general** rules only (no chapter-specific special cases); every changed rule or list entry is reported with the error it fixes.
4. Report the `SegmentStats` for both chapters.

## 5. Report and commit
Files, test counts, the full dev `results.md`, every rule/list change and why, anything in §15.2 you had to interpret. Commit `Phase 6.1: dialogue segmentation + detection evaluation` and push. Do not start 6.2.
