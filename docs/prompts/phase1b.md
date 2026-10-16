# Phase 1b — Fixes, spec sync, and the held-out evaluation

Read `CLAUDE.md`, `docs/DECISIONS.md` (note **D25**), and `docs/SPEC.md` §6.3.6 and §13 / §13.1 (SPEC is now revision 3; it records the rules you added in Phase 1 plus three new ones).

## Ground rule (most important)
`testset_heldout/` contains a held-out test set. **Do not open, list, extract, dump, or inspect anything inside `testset_heldout/` until step 4.** Do not read `testset_heldout/labels.json` either. Steps 1–3 must be done using only the dev set (`testset/`) and unit tests.

## 1. Code fixes (dev set only)
a. **Short-document running headers** (SPEC §6.3.6, "Short documents"): for 2-page PDFs, a top/bottom-zone line whose normalized text appears on every page is a running header/footer. Confirm `07_messy.pdf`'s "Workshop on Document Understanding (Preprint)" no longer appears in its dump.
b. **Compound hyphens** (SPEC §6.3.6, "Hyphenation and compounds"): implement the keep-the-hyphen rule. Confirm on the `01_novel` dump that `rose-tree` keeps its hyphen and an ordinary split word (find one in the dump) is still joined without one.
c. **Empty normalized titles** (SPEC §13): in `titles.py`, if `normalize_title` would return `""`, fall back to the label-keeping normalization. Empty titles must never match each other. Add unit tests for `"Chapter 12"` vs `"Chapter 12"` (match), `"Chapter 12"` vs `"Chapter 13"` (no match), and `""` vs `""` (no match).
d. Make sure every rule listed in SPEC §6.3.6 matches what the code does. If any wording differs from your implementation, tell me (don't edit the spec).

Add unit tests for a–c. Run `pytest -q` and `python scripts\evaluate.py` (dev set) and confirm nothing regressed.

## 2. evaluate.py support for the held-out set
- `--testset <dir>` already exists; make sure output dumps go to `<dir>/output/` and results to `<dir>/results.md`.
- Add `--history`: appends a dated section to `<dir>/results_history.md` with the commit hash, overall P/R/F1, reject accuracy, total noise and the per-file F1 line. Never overwrite the history file.
- Rejected files whose expected code is something other than the three dev codes must work (`TOO_MANY_WORDS`, `TOO_MANY_PAGES`, `ENCRYPTED_FILE`, `CORRUPT_FILE`).
- Accepted files may carry warnings; include each file's warnings in the results table.

## 3. Commit before looking
Add to `.gitignore`:
```
testset_heldout/docs/
testset_heldout/output/
```
(`testset_heldout/labels.json`, `results.md` and `results_history.md` are committed.)
Commit with message `Phase 1b: short-doc headers, compound hyphens, empty-title matching, held-out support`. This commit is the code being evaluated.

## 4. Held-out run (once)
Now, and only now:
```
python scripts\evaluate.py --testset ..\testset_heldout --history
```
Do **not** change any code after this run in this phase, even if results are bad.

## 5. Report
- Files changed in steps 1–3.
- Dev-set results (overall line + any file that changed).
- The full `testset_heldout/results.md`.
- For each held-out failure or score below 1.0: now you may open the dumps in `testset_heldout/output/`. Explain the cause in one or two sentences each and propose a fix (do not implement it).
- Commit the held-out results (`results.md`, `results_history.md`) with message `Held-out evaluation, first run`, and push.

Do not start Phase 2.
