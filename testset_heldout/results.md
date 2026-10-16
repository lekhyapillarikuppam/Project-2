# Test set results

- Date: 2026-10-16
- Commit: `1dbf9ac`
- Command: `python scripts\evaluate.py` (from `backend\`)

## Overall

| Metric | Value |
|---|---|
| Reject accuracy | 4/4 |
| Chapter precision (micro) | 0.99 |
| Chapter recall (micro) | 0.99 |
| Chapter F1 (micro) | 0.99 |
| TP / detected / labeled | 78 / 79 / 79 |
| Total noise paragraphs | 0 |
| Slowest file | H05_moby_dick.docx (5.4 s) |

## Rejections

| File | Expected | Actual | Result | Warnings |
|---|---|---|---|---|
| H05_moby_dick.docx | TOO_MANY_WORDS | TOO_MANY_WORDS | pass | - |
| H06_encrypted.pdf | ENCRYPTED_FILE | ENCRYPTED_FILE | pass | - |
| H07_corrupt.pdf | CORRUPT_FILE | CORRUPT_FILE | pass | - |
| H09_too_many_pages.pdf | TOO_MANY_PAGES | TOO_MANY_PAGES | pass | - |

## Chapter detection

| File | Actual | Structure | Detected | Labeled | P | R | F1 | Order OK | Noise | Time (s) | Warnings |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H01_persuasion.pdf | accept | typography | 24 | 24 | 1.00 | 1.00 | 1.00 | yes | 0 | 2.0 | - |
| H02_ball_and_cross.pdf | accept | typography | 20 | 20 | 0.95 | 0.95 | 0.95 | yes | 0 | 2.0 | - |
| H03_buster_bear.docx | accept | styles | 23 | 23 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.6 | - |
| H04_optimisation_notes.pdf | accept | outline | 6 | 6 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.0 | - |
| H08_partial_scan.pdf | accept | typography | 6 | 6 | 1.00 | 1.00 | 1.00 | yes | 0 | 0.7 | 2 image-only pages were skipped |

## Unmatched titles

- **H01_persuasion.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **H02_ball_and_cross.pdf**
  - unmatched detected: XVII. THE IDIOT
  - unmatched labeled: THE IDIOT
  - optional titles detected (not counted): none
- **H03_buster_bear.docx**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **H04_optimisation_notes.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
- **H08_partial_scan.pdf**
  - unmatched detected: none
  - unmatched labeled: none
  - optional titles detected (not counted): none
