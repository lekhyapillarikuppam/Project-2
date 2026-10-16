# Test set results

- Date: 2026-10-16
- Commit: `3247049 + uncommitted changes`
- Command: `python scripts\evaluate.py` (from `backend\`)

## Overall

| Metric | Value |
|---|---|
| Reject accuracy | 3/3 |
| Chapter precision (micro) | 1.00 |
| Chapter recall (micro) | 1.00 |
| Chapter F1 (micro) | 1.00 |
| TP / detected / labeled | 60 / 60 / 60 |
| Total noise paragraphs | 0 |
| Slowest file | 04_with_outline.pdf (2.8 s) |

## Rejections

| File | Expected | Actual | Result | Warnings |
|---|---|---|---|---|
| 08_scanned.pdf | SCANNED_PDF | SCANNED_PDF | pass | - |
| 09_legacy.doc | LEGACY_DOC | LEGACY_DOC | pass | - |
| 10_fake.pdf | UNSUPPORTED_TYPE | UNSUPPORTED_TYPE | pass | - |

## Chapter detection

| File | Actual | Structure | Detected | Labeled | P | R | F1 | Order OK | Noise | Time (s) | Warnings |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01_novel.pdf | accept | typography | 12 | 12 | 1.00 | 1.00 | 1.00 | yes | 0 | 1.3 | - |
| 02_lecture_notes.pdf | accept | typography | 6 | 6 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.1 | - |
| 03_textbook_chapter.pdf | accept | typography | 6 | 6 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.2 | - |
| 04_with_outline.pdf | accept | outline | 16 | 16 | 1.00 | 1.00 | 1.00 | yes | 0 | 2.8 | - |
| 05_styles.docx | accept | styles | 4 | 4 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.8 | - |
| 06_no_styles.docx | accept | typography | 10 | 10 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.5 | - |
| 07_messy.pdf | accept | typography | 6 | 6 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.1 | - |

## Unmatched titles

- **01_novel.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **02_lecture_notes.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **03_textbook_chapter.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **04_with_outline.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **05_styles.docx**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **06_no_styles.docx**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **07_messy.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): Abstract
