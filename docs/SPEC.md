# AI-Powered Audiobook Generator — Specification (v1)

Course: UMC301 Applied Data Science and Artificial Intelligence — Project 2
Status: Phase 0 (spec). Locked decisions live in `DECISIONS.md`; this file describes *how* the system works.

---

## 1. Overview

A web app that turns a PDF or DOCX document into an audiobook. The user uploads a file, the backend extracts and cleans the text, detects chapters/sections, narrates each chapter with neural text-to-speech, and the frontend lets the user play chapters (with speed control) and download individual chapters or the full book as MP3.

No accounts. Generated audio is temporary.

## 2. Goals and non-goals

### Goals (v1)
- Accept `.pdf` and `.docx` up to 200 pages.
- Produce clean narration: no page numbers, running headers/footers, broken hyphenation, or stray captions read aloud.
- Split the document into chapters/sections that match the document's real structure.
- Let the user start listening to chapter 1 while later chapters are still generating.
- In-browser player with play/pause/seek, previous/next chapter, auto-advance, and 0.5×–2× speed.
- Download any single chapter or the full book as one MP3.
- Deployed publicly so the professor can open a link and use it.
- Measurable evaluation of chapter detection on a labeled test set.

### Non-goals (v1)
- User accounts, saved libraries, permanent storage.
- OCR / scanned PDFs (rejected).
- Legacy `.doc`, `.txt`, `.epub`, or any other format (rejected).
- Languages other than English.
- Reading tables, figure/table captions, footnotes, or equations.
- Multiple voices (that is v2, see §15).
- Paid APIs of any kind.

## 3. User flow

1. User opens the site → sees upload area and a **Try a sample** button.
2. User drops a PDF/DOCX → client-side check (type, size) → upload.
3. Server validates (type, size, page/word count, encryption, scanned) → either rejects with a clear message or returns a `job_id`.
4. User is taken to `/book/{job_id}` → progress view: overall bar, per-chapter status, ETA.
5. As soon as chapter 1 is ready, its play button turns on. Chapters become playable one by one.
6. When all chapters finish, **Download full book** appears.
7. A notice shows when the audio expires (24 h).

## 4. Limits

| Limit | Value | Where enforced |
|---|---|---|
| Accepted types | `.pdf`, `.docx` only (checked by extension **and** file signature) | client + server |
| Max file size | 50 MB | client + server |
| Max PDF pages | 200 | server |
| Max words (both formats) | 100,000 | server (this is the effective DOCX "page" cap) |
| Concurrent jobs being processed | 1 | server |
| Max queued jobs | 3 (further uploads get `SERVER_BUSY`) | server |
| Audio retention | 24 h after job creation | server cleanup task |

All values live in config (`.env`), not hard-coded.

## 5. Architecture

```
 Browser (React + Vite, on Vercel)
   │  upload / poll status / stream audio / download
   ▼
 FastAPI backend (on Render, single worker)
   ├── api/          HTTP endpoints
   ├── jobs/         in-memory job registry + background runner + cleanup
   ├── pipeline/     validate → extract → clean → structure  → document.json
   └── tts/          normalize → chunk → synthesize → assemble → chapter MP3s
                       ├── EdgeTTSEngine   (primary)
                       └── PiperEngine     (fallback, local)
   │
   ▼
 storage/jobs/{job_id}/   (temporary, deleted after 24 h)
   ├── input.pdf|docx
   ├── document.json
   ├── job.json
   ├── chapters/ch_001.mp3 …
   └── full.mp3           (built lazily on first full download)
```

The backend runs **one uvicorn worker** because the job registry is in memory. `job.json` is also written to disk after every state change so the state is inspectable and survives a reload during local development.

## 6. Processing pipeline

### 6.1 Validation (`pipeline/validate.py`)
In order, stop at first failure:
1. Extension is `.pdf` or `.docx` (`.doc` → `LEGACY_DOC`, anything else → `UNSUPPORTED_TYPE`).
2. File signature matches the extension (PDF starts with `%PDF`; DOCX is a ZIP containing `word/document.xml`). Mismatch → `UNSUPPORTED_TYPE`.
3. Size ≤ max → else `FILE_TOO_LARGE`.
4. Opens without error → else `CORRUPT_FILE`.
5. PDF not encrypted / password-protected → else `ENCRYPTED_FILE`.
6. PDF page count ≤ 200 → else `TOO_MANY_PAGES`.
7. Scanned check (PDF): count pages with fewer than 30 extractable characters. If more than 50% of pages are like this → `SCANNED_PDF`. If some (but ≤ 50%) are, skip those pages and add a warning to the job.
8. After extraction: total words ≤ 100,000 → else `TOO_MANY_WORDS`; total words ≥ 20 → else `EMPTY_DOCUMENT`.

### 6.2 Extraction
- **PDF** (`pipeline/extract_pdf.py`, PyMuPDF): `page.get_text("dict")` to get blocks → lines → spans with text, font size, bold flag, and bounding box. Also read the built-in outline via `doc.get_toc()`.
- **DOCX** (`pipeline/extract_docx.py`, python-docx): iterate paragraphs in body order, keeping style name (`Heading 1`, `Title`, …), run-level bold, and font size. Tables are skipped. Footnotes/endnotes are not in the body and are ignored.

Both extractors produce the same intermediate form: an ordered list of **lines/paragraphs** with `text`, `font_size`, `bold`, `page` (PDF only), `bbox` (PDF only), `style` (DOCX only).

### 6.3 Cleaning (`pipeline/clean.py`)
1. **Running headers/footers and page numbers**: text in the top ~8% or bottom ~8% of a page that repeats (after stripping digits) on ≥ 30% of pages is removed. Lines that are only a number or roman numeral or "Page N"/"N of M" are removed.
2. **Hyphenation**: a line ending in `-` followed by a line starting with a lowercase letter is joined without the hyphen.
3. **Line merging**: consecutive lines of the same block and same font size are merged into one paragraph; a paragraph break is kept when there is a vertical gap, indentation change, or the previous line ends with terminal punctuation and is noticeably shorter than the column width.
4. **Normalization of characters**: ligatures (ﬁ, ﬂ), smart quotes kept but normalized, non-breaking spaces, soft hyphens, repeated whitespace.
5. **Skipped content**:
   - Tables: PDF — `page.find_tables()` regions are excluded; DOCX — table elements skipped.
   - Captions: paragraphs starting with `Figure N`, `Fig. N`, `Table N` (case-insensitive) are dropped.
   - Footnotes: PDF lines in the bottom region with a noticeably smaller font than the body text, typically starting with a number or symbol, are dropped. Inline footnote markers (superscript digits) are removed.
   - Equations: lines with a high ratio of math symbols/Greek letters to letters, or using math fonts (e.g. names containing `CMMI`, `CMSY`, `Math`), are dropped.

Body font size = the most common font size by character count; it is used as the reference in cleaning and structure detection.

### 6.4 Structure detection (`pipeline/structure.py`)
Produces an ordered list of chapters, each with a title and paragraphs. Strategies are tried in order; the first that yields a plausible result wins:

1. **Built-in outline** (PDF `get_toc()`): use level-1 entries (or level-2 if there is only one level-1 entry). Match each entry to its page and find the heading line on that page. Plausible = ≥ 2 entries.
2. **Heading styles** (DOCX): `Title`/`Heading 1` as chapter boundaries (`Heading 2` if there are fewer than 2 `Heading 1`s).
3. **Typography**: lines that are short (≤ 12 words), not ending in a period, and either ≥ 1.2× body font size or bold-on-their-own-line with whitespace above. Cluster heading sizes; the largest frequent level becomes the chapter level.
4. **Text patterns**: lines matching `^(chapter|part|book|section|unit|lecture)\s+([0-9]+|[ivxlcdm]+|one|two|…)\b` or numbered headings like `^\d+(\.\d+)?\s+[A-Z]`.
5. **Fallback**: no structure found → split into `Part 1`, `Part 2`, … of roughly 3,000 words each, at paragraph boundaries.

Post-processing:
- Text before the first chapter (title page, preface without a heading) becomes a chapter titled `Introduction` if it has ≥ 150 words; otherwise it is dropped.
- Table of contents pages inside the body (many short lines ending in page numbers) are removed.
- Chapters with < 150 words are merged into the next chapter.
- Chapters with > 12,000 words are split at sub-headings or paragraph boundaries into `Title (Part 1)`, `Title (Part 2)`, ….
- The strategy used is recorded in `document.json` (`structure_source`).

### 6.5 Text normalization for speech (`tts/normalize.py`)
Applied per segment before synthesis:
- Numbers, ordinals, years, decimals, percentages, currency (`₹`, `$`, `€`, `£`), simple dates.
- Common abbreviations: `Dr.`, `Mr.`, `Mrs.`, `St.`, `e.g.`, `i.e.`, `etc.`, `vs.`, `approx.`, `Fig.`, `No.`.
- Roman numerals in headings (`Chapter IV` → `Chapter Four`).
- URLs and emails → `link` / dropped; stray symbols (`*`, `#`, `|`, `•`) removed; `&` → `and`.

### 6.6 Chunking (`tts/chunk.py`)
Split each paragraph into sentences, then pack sentences into chunks of ≤ 1,500 characters without breaking a sentence (a single over-long sentence is split at `;`, `,` or spaces). Each chunk remembers which paragraph it belongs to so pauses can be inserted.

### 6.7 Synthesis (`tts/`)
- Interface: `TTSEngine.synthesize(text: str, voice: str) -> bytes` (MP3, 24 kHz, mono, 48 kbps).
- **EdgeTTSEngine** (primary): `edge-tts` library, narrator voice from config.
- **PiperEngine** (fallback): local Piper model (`en_US-lessac-medium`), WAV output re-encoded with ffmpeg to the same MP3 format.
- Per chunk: try edge-tts up to 3 times with exponential backoff (1 s, 2 s, 4 s); if it still fails, use Piper for that chunk. If edge-tts fails 5 times in a row within a job, switch the rest of the job to Piper.
- Concurrency: up to 4 chunks in flight at once (config), chapters processed **in order** so chapter 1 is ready first.

### 6.8 Assembly (`tts/assemble.py`)
- All audio uses the same MP3 parameters, so files can be joined with the ffmpeg concat demuxer and `-c copy` (no re-encoding).
- Pre-generated silence clips in the same format: 400 ms between paragraphs, 1,000 ms after the chapter title, 2,000 ms between chapters in the full book.
- Each chapter file starts with the narrator reading the chapter title.
- Chapter duration is measured with ffprobe and stored.
- `full.mp3` is built on the first full-book download request (after all chapters are ready) and cached in the job folder.

## 7. Data model

### 7.1 `document.json`
```json
{
  "source_filename": "alice.pdf",
  "source_type": "pdf",
  "page_count": 98,
  "word_count": 26450,
  "structure_source": "outline | styles | typography | patterns | fallback",
  "warnings": ["3 image-only pages were skipped"],
  "chapters": [
    {
      "index": 1,
      "title": "Down the Rabbit-Hole",
      "word_count": 2210,
      "paragraphs": [
        {
          "index": 1,
          "segments": [
            { "speaker": "narrator", "text": "Alice was beginning to get very tired..." }
          ]
        }
      ]
    }
  ]
}
```
`speaker` is always `"narrator"` in v1. The field exists so v2 can assign character voices without changing the data model.

### 7.2 Job (`job.json` and API responses)
```json
{
  "job_id": "uuid4",
  "status": "queued | extracting | synthesizing | done | partial | failed | expired",
  "created_at": "ISO-8601",
  "expires_at": "ISO-8601",
  "filename": "alice.pdf",
  "error": null,
  "warnings": [],
  "progress": {
    "chapters_total": 12,
    "chapters_done": 3,
    "chars_total": 150000,
    "chars_done": 41000,
    "eta_seconds": 420
  },
  "chapters": [
    {
      "index": 1,
      "title": "Down the Rabbit-Hole",
      "status": "pending | generating | ready | failed",
      "duration_seconds": 812.4
    }
  ],
  "engine_used": "edge | piper | mixed"
}
```
ETA = remaining characters ÷ observed characters-per-second over the job so far (null until the first chunk finishes).

## 8. Job lifecycle

```
queued → extracting → synthesizing → done
                │            └──────→ partial   (some chapters failed even after fallback)
                └──→ failed          (validation/extraction error, or every chapter failed)
done | partial | failed → expired (after 24 h; files deleted, job kept as a stub for 1 more hour so the UI can say "expired")
```
- Upload-time validation errors (§6.1 steps 1–7) are returned directly from `POST /api/upload` with HTTP 4xx and **no job is created**.
- Errors found after the job starts (e.g. `TOO_MANY_WORDS`, `EMPTY_DOCUMENT`) set `status = failed` with `error` filled.

## 9. API contract

Base path `/api`. All errors use one shape:
```json
{ "error": { "code": "SCANNED_PDF", "message": "This PDF looks like scanned images..." } }
```

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/api/health` | health + version (also used as keep-awake ping) | `200 {"status":"ok","version":"…"}` |
| GET | `/api/config` | public limits for the frontend (max size, max pages, retention hours) | `200 {...}` |
| POST | `/api/upload` | multipart `file` → validate → create job | `202 {"job_id": "...", "status": "queued"}` |
| GET | `/api/jobs/{job_id}` | full job object (§7.2) | `200` |
| GET | `/api/jobs/{job_id}/chapters/{n}/audio` | stream chapter MP3 for the player; must support HTTP `Range` so seeking works | `200/206 audio/mpeg` |
| GET | `/api/jobs/{job_id}/download/{n}` | chapter MP3 as attachment, filename `{book}_{nn}_{title}.mp3` | `200` |
| GET | `/api/jobs/{job_id}/download/full` | full-book MP3 as attachment (only when `done`/`partial`) | `200` |
| GET | `/sample/…` | static pre-generated sample audiobook (job-shaped JSON + MP3s) | `200` |

HTTP status mapping: validation errors `400`/`413`/`415`/`422`; `SERVER_BUSY` `503`; unknown job `404`; expired job `410`; chapter not ready `409`.

## 10. Storage and cleanup
- Root: `storage/jobs/` (path from config). Gitignored.
- A background task runs every 10 minutes and deletes job folders past `expires_at`.
- On startup, folders older than the retention period are deleted.
- On Render, the disk is wiped on every redeploy/restart anyway; this is acceptable because audio is temporary by design.

## 11. Frontend

Routes:
- `/` — upload card (drag-and-drop + picker, client-side type/size check, rejection messages), **Try a sample** button, short "how it works", limits shown from `/api/config`.
- `/book/:jobId` — progress bar + ETA, chapter list (title, duration, status, play, download), sticky player, **Download full book**, expiry notice.
- `/sample` — the same book view backed by the static sample.

Player behaviour:
- Single `<audio>` element; source = current chapter's audio URL.
- Speed selector 0.5×, 0.75×, 1×, 1.25×, 1.5×, 1.75×, 2× via `playbackRate` (pitch preserved by the browser).
- Previous/next chapter; auto-advance to the next **ready** chapter on end; if the next chapter is not ready yet, show "waiting for chapter N…" and continue when it is.
- Last chapter + position saved in `localStorage` per `job_id` (wrapped in try/catch).

Polling: `GET /api/jobs/{id}` every 3 s while status is `queued`/`extracting`/`synthesizing`; stop when finished. Polling also keeps the Render instance awake during generation.

Cold start: if `/api/health` takes > 3 s, show "Waking up the server (free hosting), this can take up to a minute…".

## 12. Error catalogue

| Code | User-facing message |
|---|---|
| `UNSUPPORTED_TYPE` | Only PDF and DOCX files are supported. |
| `LEGACY_DOC` | Old Word (.doc) files aren't supported. Open it in Word and "Save As" .docx, then try again. |
| `FILE_TOO_LARGE` | This file is larger than 50 MB. |
| `TOO_MANY_PAGES` | This PDF has more than 200 pages. Please upload a shorter document. |
| `TOO_MANY_WORDS` | This document is too long (over 100,000 words). Please upload a shorter document. |
| `CORRUPT_FILE` | This file couldn't be opened. It may be damaged. |
| `ENCRYPTED_FILE` | This PDF is password-protected. Remove the password and try again. |
| `SCANNED_PDF` | This PDF looks like scanned images with no selectable text. Scanned documents aren't supported. |
| `EMPTY_DOCUMENT` | No readable text was found in this document. |
| `SERVER_BUSY` | The server is busy converting other documents. Please try again in a few minutes. |
| `JOB_NOT_FOUND` | This audiobook doesn't exist. |
| `JOB_EXPIRED` | This audiobook has expired. Audio is kept for 24 hours — please upload the document again. |
| `CHAPTER_NOT_READY` | This chapter is still being generated. |
| `TTS_FAILED` | Narration failed for this chapter. |

## 13. Evaluation
- Test set in `testset/` with hand labels (`labels.json`): expected accept/reject, and the expected ordered chapter titles.
- `scripts/evaluate.py` runs the pipeline on every test document and reports:
  - rejection correctness (expected vs actual error code);
  - chapter detection precision, recall, and F1 (a detected title matches a labeled title if their normalized fuzzy similarity ≥ 0.8);
  - which structure strategy was used;
  - leaked-noise check: number of lines in the output that look like page numbers/headers.
- Results are written to `testset/results.md` and quoted in the README and viva.

## 14. Deployment
- **Frontend**: Vercel, env `VITE_API_URL`.
- **Backend**: Render free web service. Deployed with a Dockerfile (needed to install ffmpeg and bundle the Piper voice model). One worker. Env from `.env.example`.
- Free-tier constraints to keep in mind: 512 MB RAM (stream files to disk, never hold a whole book's audio in memory), instance sleeps after ~15 min without requests (frontend polling keeps it awake during a job), disk is ephemeral.
- Sample audiobook is pre-generated locally and served as static files so it plays instantly.

## 15. v2 outline (after v1 ships)
- Detect dialogue spans in paragraphs and split them into separate segments.
- Free LLM (Gemini or Groq) attributes each dialogue segment to a character using surrounding context; build a book-level character list with alias merging.
- Map characters to distinct edge-tts voices (by gender/tone); narrator voice for everything else.
- UI: v1/v2 narration toggle and a cast list.
- Evaluate speaker attribution accuracy on one hand-labeled chapter.

## 16. Repository structure
```
Project2/
├── CLAUDE.md
├── README.md
├── .gitignore
├── docs/
│   ├── SPEC.md
│   └── DECISIONS.md
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── models.py          (Phase 1+)
│   │   ├── api/               (Phase 3)
│   │   ├── pipeline/          (Phase 1)
│   │   ├── tts/               (Phase 2)
│   │   └── jobs/              (Phase 3)
│   ├── scripts/
│   │   ├── check_env.py
│   │   ├── extract.py         (Phase 1)
│   │   ├── narrate.py         (Phase 2)
│   │   └── evaluate.py        (Phase 1)
│   ├── tests/
│   └── storage/               (gitignored)
├── frontend/                  (Vite + React)
└── testset/
    ├── README.md
    ├── labels.json
    └── docs/                  (gitignored)
```
