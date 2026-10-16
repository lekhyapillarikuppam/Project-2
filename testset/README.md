# Test set

Documents used to develop and evaluate extraction + chapter detection.
The documents themselves go in `testset/docs/` (gitignored). Only `labels.json` is committed.

## Documents to collect (put in `testset/docs/` with these exact names)

| File name | What it is | Where to get it | Tests |
|---|---|---|---|
| `01_novel.pdf` | A public-domain novel with chapters (e.g. *Alice's Adventures in Wonderland*) | Project Gutenberg → download as EPUB, open in Calibre/Word and export PDF, **or** any public-domain PDF edition | Chapter headings, running headers, page numbers |
| `02_lecture_notes.pdf` | Your own course notes / lecture slides exported as PDF | Any IISc course PDF you have | Section numbering (1, 1.1), equations skipped |
| `03_textbook_chapter.pdf` | One chapter of a textbook with figures, tables, footnotes | An open textbook (e.g. OpenStax) — one chapter | Captions, tables, footnotes skipped |
| `04_with_outline.pdf` | A PDF that has a sidebar bookmarks/outline | Most LaTeX-generated PDFs, OpenStax PDFs | Outline strategy |
| `05_styles.docx` | A Word file using real Heading 1/Heading 2 styles | Write one or take an existing report | DOCX style strategy |
| `06_no_styles.docx` | A Word file where headings are just bold/large normal text | Copy 05, change all headings to "Normal" + bold + 16pt | Typography fallback on DOCX |
| `07_messy.pdf` | Two-column or oddly formatted PDF (research paper) | Any arXiv paper | Robustness |
| `08_scanned.pdf` | A scanned/image-only PDF | Print a page to PDF as an image, or scan a page with your phone | Must be **rejected** (`SCANNED_PDF`) |
| `09_legacy.doc` | An old Word 97 `.doc` | In Word: Save As → "Word 97-2003 Document" | Must be **rejected** (`LEGACY_DOC`) |
| `10_fake.pdf` | A `.txt` file renamed to `.pdf` | Rename any text file | Must be **rejected** (`UNSUPPORTED_TYPE`) |

Keep each document under 200 pages. Prefer variety over size.

> A ready-made set of all 10 files (built from public-domain books and original content) was provided as `testset_docs.zip`. Unzip it into the repo root. You can still swap in or add real documents (an 11th file of your own long course notes is a good addition) as long as you label them.

## Labels

`labels.json` holds, for every file:
- `expect`: `"accept"` or an error code from SPEC §12
- `chapters`: the chapter titles you would expect a human to pick, in order (empty for rejected files)
- `optional`: titles that may or may not be detected without counting for or against the detector (e.g. a front-matter `Introduction`)
- `notes`: anything unusual

Matching rules (normalization, fuzzy threshold, one-to-one matching) are in SPEC §13.

Label by looking at the document yourself — **don't** copy the detector's output. Label at the level a listener would want to navigate (usually chapters, or top-level numbered sections for notes).
