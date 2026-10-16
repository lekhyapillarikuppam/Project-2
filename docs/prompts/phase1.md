# Phase 1 — Extraction, cleaning and chapter detection

You are implementing **Phase 1 only**. Before writing code, read fully: `CLAUDE.md`, `docs/DECISIONS.md`, and `docs/SPEC.md` §4, §6.1–6.4, §7.1, §12, §13, §16.
The spec is the source of truth. If something in this prompt seems to conflict with the spec, follow the spec and mention the conflict in your final report.

No API endpoints, no TTS, no job queue, no frontend in this phase.

---

## 0. Housekeeping (do first)
- In `backend/pytest.ini`, replace the filterwarnings line with one that actually silences the Starlette test-client warning, e.g. `ignore:Using .httpx. with .starlette.testclient.*` (try it, confirm `pytest -q` shows no warnings).
- Always `import pymupdf` (never `import fitz`).

## 1. Files to create

```
backend/app/models.py
backend/app/pipeline/__init__.py      (exports run_pipeline)
backend/app/pipeline/errors.py
backend/app/pipeline/validate.py
backend/app/pipeline/extract_pdf.py
backend/app/pipeline/extract_docx.py
backend/app/pipeline/clean.py
backend/app/pipeline/structure.py
backend/app/pipeline/titles.py        (title normalization + matching; shared by structure.py and evaluate.py)
backend/scripts/extract.py
backend/scripts/evaluate.py
backend/tests/test_clean.py
backend/tests/test_structure.py
backend/tests/test_titles.py
backend/tests/test_validate.py
backend/tests/test_testset.py         (integration; skips if testset/docs is missing)
```
Do not create any other files unless you explain why in the report.

## 2. Module contracts

### `app/models.py`
Pydantic v2 models matching SPEC §7.1 exactly:
- `Segment(speaker: str = "narrator", text: str)`
- `Paragraph(index: int, segments: list[Segment])`
- `Chapter(index: int, title: str, word_count: int, paragraphs: list[Paragraph])`
- `DocumentModel(source_filename, source_type: Literal["pdf","docx"], page_count: int | None, word_count, structure_source: Literal["outline","styles","typography","patterns","fallback"], warnings: list[str], chapters: list[Chapter])`

Plus an internal dataclass used between extract → clean → structure (not serialized):
- `TextLine(text, font_size: float, bold: bool, page: int | None, bbox: tuple | None, style: str | None, block_id: int | None, is_math_font: bool)`

### `app/pipeline/errors.py`
- `class PipelineError(Exception)` with `code: str` and `message: str`.
- A dict `ERROR_MESSAGES` with every code and user-facing message from SPEC §12 (copy the text exactly).
- `def fail(code) -> PipelineError` helper.

### `app/pipeline/validate.py`
`validate_file(path: Path, original_filename: str, settings) -> Literal["pdf","docx"]`
Implements SPEC §6.1 steps 1–7 against a file already on disk (the streaming/Content-Length part of step 1 belongs to Phase 3; here just check the file size). Raises `PipelineError`. Returns the detected type.
Also returns/records image-only page indices for PDFs so extraction can skip them and add the warning — design the return shape as you see fit (e.g. a small `ValidationResult` dataclass), but keep it simple.

### `app/pipeline/extract_pdf.py` / `extract_docx.py`
`extract_pdf(path, skip_pages: set[int]) -> tuple[list[TextLine], list[tuple[int,str,int]], int]` → lines, outline (`doc.get_toc()`), page_count.
- Follow SPEC §6.2 including the **multi-column reading order** algorithm exactly.
- Mark math fonts (`CMMI`, `CMSY`, `CMEX`, `Math`, `Symbol` in the font name) on the line.
- Exclude text inside `page.find_tables()` regions.

`extract_docx(path) -> list[TextLine]` — body paragraphs in order, style name, bold if all non-empty runs are bold, font size from runs or style (fall back to 11). Skip tables. `page` and `bbox` are `None`.

### `app/pipeline/clean.py`
Pure functions, each unit-testable, following SPEC §6.3 in this order:
1. `remove_running_headers_footers(lines, page_count, page_height_by_page)` — threshold `max(3, 30% of pages)`, alternating headers counted separately, page-number-only lines always removed.
2. `drop_captions_footnotes_equations(lines, body_size)` — rules from §6.3.5.
3. `merge_into_paragraphs(lines, body_size)` — hyphenation join + line merge rules.
4. `normalize_chars(text)` — ligatures, soft hyphens, NBSP, whitespace.
- `body_font_size(lines)` = most common size by character count.

Headings must survive cleaning as their own paragraph units (structure needs them), so the merged unit should keep `font_size`, `bold`, `style` of its first line.

### `app/pipeline/structure.py`
`detect_structure(units, outline, source_type) -> tuple[list[Chapter], str]` implementing SPEC §6.4:
- Strategies 1–5 in order, first plausible wins.
- The **heading text** rule (combine `Chapter I` + next heading line → `Chapter I: Title`).
- Post-processing steps 1–6 **in the spec's order** (TOC pages, back matter, front matter, merge < 40 words, split > 12,000 words, numbering).
- For the outline strategy, matching an outline entry to the text: find the line on that page whose normalized text best matches the entry title (use `titles.py`); chapter text runs from that line to the next entry's line.
- Paragraph `segments` always contain exactly one `Segment(speaker="narrator", ...)` in v1.

### `app/pipeline/titles.py`
- `normalize_title(s) -> str` exactly as SPEC §13 describes.
- `match_titles(detected, labeled, optional) -> MatchResult` (one-to-one greedy by highest `fuzz.ratio`, threshold 80, optional titles handled as in §13; returns TP, precision, recall, F1, order_ok, the matched pairs, unmatched lists).

### `app/pipeline/__init__.py`
`run_pipeline(path: Path, original_filename: str, settings) -> DocumentModel`
validate → extract → clean → structure → word-count checks (`TOO_MANY_WORDS`, `EMPTY_DOCUMENT`) → `DocumentModel`. Log each stage's duration at INFO level.

## 3. Scripts

### `scripts/extract.py`
```
python scripts\extract.py <file> [--out document.json] [--dump]
```
- Prints: type, pages, words, structure_source, warnings, then a table of chapters (index, title, words, first 80 characters of text).
- `--out` writes the `DocumentModel` JSON.
- `--dump` writes `testset/output/<stem>.txt`: each chapter as `===== [n] Title (words) =====` followed by its paragraphs separated by blank lines. This is how a human checks for leaked headers/page numbers.
- On `PipelineError`, print `REJECTED <code>: <message>` and exit with code 2.

### `scripts/evaluate.py`
```
python scripts\evaluate.py [--testset ..\testset]
```
For every file in `labels.json`: run the pipeline, compare with labels per SPEC §13, and also run a **noise check** on the accepted output: count paragraphs that (a) are only a number/roman numeral/"Page N", or (b) exactly equal a running header string that appears ≥ 3 times in the output. Also dump each document (same format as `--dump`).
Print a per-file table and write `testset/results.md` with:
- per file: expected, actual (accept / error code), pass/fail for rejects; for accepts: structure_source, #detected, #labeled, P, R, F1, order_ok, noise count, unmatched detected titles, unmatched labeled titles;
- overall micro-averaged P/R/F1, reject accuracy, total noise;
- the date and the git commit hash (`git rev-parse --short HEAD`, ignore errors).

## 4. Tests
- Unit tests for every pure function in `clean.py`, `titles.py`, and the heading-text rule and post-processing steps in `structure.py`. Build small synthetic `TextLine` inputs in the tests; don't depend on the test documents.
- `test_validate.py`: build tiny files in `tmp_path` (fake PDF from text, empty file, a real one-page PDF made with pymupdf, a `.doc` extension, a zip without `word/document.xml`) and assert the right error codes.
- `test_testset.py`: if `testset/docs` exists, assert all 3 rejects give the expected codes and every accepted file produces ≥ 1 chapter with no exception. Skip otherwise.

## 5. Rules for tuning (important for an honest evaluation)
- Do **not** special-case file names, titles, or exact sizes from the test documents. Every threshold must come from the spec or be a named constant at the top of the module with a one-line comment explaining it.
- If a spec threshold clearly fails on the test set, you may change it, but list every changed value, the old value, the new value, and why, in the report. Do not edit SPEC.md yourself.
- Do not edit `labels.json`.

## 6. Acceptance criteria
Run and report:
1. `pytest -q` — all pass, no warnings.
2. `python scripts\evaluate.py` — targets:
   - 3/3 rejects correct;
   - `01_novel.pdf`, `04_with_outline.pdf`, `05_styles.docx`: F1 = 1.0 and order_ok;
   - overall F1 ≥ 0.90;
   - noise count 0 on `01_novel.pdf` and `04_with_outline.pdf`.
3. `python scripts\extract.py ..\testset\docs\01_novel.pdf --dump`, then open the dump and confirm by reading: no "Alice's Adventures in Wonderland" running header lines, no page numbers, no words split by a hyphen across a line break (e.g. search for `- ` patterns mid-word), and the verse passages are present.
4. Time `evaluate.py` and report the slowest file's pipeline time.

## 7. Final report (what to tell me)
- Files created/changed.
- The full contents of `testset/results.md`.
- Any targets missed and your best explanation (look at the dumps).
- Every threshold you changed from the spec (old → new, why).
- Spec ambiguities you had to resolve yourself.
- Commit everything with message `Phase 1: extraction, cleaning, chapter detection, evaluation` and push.

Do not start Phase 2.
