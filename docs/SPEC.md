# AI-Powered Audiobook Generator — Specification (v1)

Course: UMC301 Applied Data Science and Artificial Intelligence — Project 2
Status: revision 11 (2026-10-19, Phase 6.2 results: D40 model/method, D41 voice casting, D42 output-token pacing; 6.2 interpretations recorded). Locked decisions live in `DECISIONS.md`; this file describes *how* the system works.

---

## 1. Overview

A web app that turns a PDF or DOCX document into an audiobook. The user uploads a file, the backend extracts and cleans the text, detects chapters/sections, narrates each chapter with neural text-to-speech, and the frontend lets the user play chapters (with speed control) and download individual chapters or the full book as MP3.

No accounts. Generated audio is temporary.

## 2. Goals and non-goals

### Goals (v1)
- Accept `.pdf` (up to 200 pages) and `.docx` (up to 100,000 words).
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
7. A notice tells the user the audio is temporary: download anything they want to keep. It is deleted after 24 h at most, and may disappear earlier if the free server restarts.

## 4. Limits

| Limit | Value | Where enforced |
|---|---|---|
| Accepted types | `.pdf`, `.docx` only (checked by extension **and** file signature) | client + server |
| Max file size | 50 MB | client + server |
| Max PDF pages | 200 | server |
| Max words (both formats) | 100,000 | server (this is the effective DOCX "page" cap) |
| Concurrent jobs being processed | 1 | server |
| Max waiting jobs (not counting the running one) | 3, so at most 4 jobs exist in the active pipeline; the next upload gets `SERVER_BUSY` | server |
| Uploads per client IP | 10 per rolling hour (accepted or rejected uploads both count); the next gets `RATE_LIMITED`. The client IP is taken from `X-Forwarded-For` only when the request comes from the Caddy container; otherwise the socket address | server |
| Audio retention | at most 24 h after job creation (earlier if the server restarts, see §10) | server cleanup task |

All values live in config (`.env`), not hard-coded.

## 5. Architecture

```
 Browser (React + Vite, on Vercel)
   │  upload / poll status / stream audio / download
   ▼
 FastAPI backend (Docker on an Azure B2ats v2 VM behind Caddy, single worker)
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

The backend runs **one uvicorn worker** because the job registry is in memory. `job.json` is written to disk after every state change so the state is inspectable while debugging; it is **not** used to restore jobs after a restart (see §10).

**Threading model.** All heavy work runs off the request loop so `/api/health` and job polling stay responsive:
- One dedicated **job worker thread** takes jobs from a FIFO queue, one at a time.
- Inside that thread the job runs in its own asyncio event loop (`asyncio.run(run_job(job))`), so the async edge-tts calls work there.
- CPU-bound or blocking steps (PyMuPDF/python-docx extraction, Piper synthesis, every ffmpeg/ffprobe call) are run with `await asyncio.to_thread(...)` from that loop.
- ffmpeg/ffprobe are always called with `subprocess.run([...], check=True)` inside a thread, **never** `asyncio.create_subprocess_exec` (it fails on Windows under `uvicorn --reload`).
- The job registry is a dict guarded by a `threading.Lock`; API handlers only read snapshots of it.

## 6. Processing pipeline

### 6.1 Validation (`pipeline/validate.py`)
In order, stop at first failure:
1. Size: if the request's `Content-Length` exceeds the max, reject with `FILE_TOO_LARGE` before reading the body. The body is then streamed to disk in 1 MB pieces and the upload is aborted with `FILE_TOO_LARGE` as soon as it passes the max (in case `Content-Length` was missing or wrong).
2. Extension is `.pdf` or `.docx` (`.doc` → `LEGACY_DOC`, anything else → `UNSUPPORTED_TYPE`).
3. File signature matches the extension (PDF starts with `%PDF`; DOCX is a ZIP containing `word/document.xml`). Mismatch → `UNSUPPORTED_TYPE`.
4. Opens without error → else `CORRUPT_FILE`.
5. PDF not encrypted / password-protected → else `ENCRYPTED_FILE`.
6. PDF page count ≤ 200 → else `TOO_MANY_PAGES`.
7. Scanned check (PDF): a page is **image-only** if it has fewer than 30 extractable characters **and** contains at least one image. A page with fewer than 30 characters and no image is **blank** (divider pages, intentionally blank pages) and is skipped silently. If more than 50% of the non-blank pages are image-only → `SCANNED_PDF`. If some (but ≤ 50%) are, skip them and add the warning "N image-only pages were skipped".
8. After extraction: total words ≤ 100,000 → else `TOO_MANY_WORDS`; total words ≥ 20 → else `EMPTY_DOCUMENT`.

### 6.2 Extraction
- **PDF** (`pipeline/extract_pdf.py`, PyMuPDF, imported as `pymupdf`): `page.get_text("dict", sort=False)` to get blocks → lines → spans with text, font size, bold flag, and bounding box. Also read the built-in outline via `doc.get_toc()`.
- **Reading order (multi-column pages)**: do **not** use `sort=True` (it interleaves columns). Per page:
  1. A block is *full-width* if its width is > 60% of the text area width; otherwise it is *left* if its centre is left of the page's centre line, else *right*.
  2. The page is two-column if at least 3 blocks are left, at least 3 are right, and left+right blocks hold ≥ 50% of the page's characters. Otherwise sort all blocks by `(y0, x0)`.
  3. For a two-column page, full-width blocks split the page into horizontal bands. Within each band, output all left blocks sorted by `y0`, then all right blocks sorted by `y0`. Bands are output top to bottom.
- **DOCX** (`pipeline/extract_docx.py`, python-docx): iterate paragraphs in body order, keeping style name (`Heading 1`, `Title`, …), run-level bold, and font size. Tables are skipped. Footnotes/endnotes are not in the body and are ignored.

Both extractors produce the same intermediate form: an ordered list of **lines/paragraphs** with `text`, `font_size`, `bold`, `page` (PDF only), `bbox` (PDF only), `style` (DOCX only).

### 6.3 Cleaning (`pipeline/clean.py`)
1. **Running headers/footers and page numbers**: a line in the top ~8% or bottom ~8% of a page is a running header/footer if its text (lowercased, digits and roman numerals stripped) appears in that same zone on **at least max(3, 30% of pages)** pages. Alternating left/right headers count separately, so each must meet the threshold on its own. Running headers/footers are removed. Lines in those zones that are only a number, a roman numeral, "Page N" or "N of M" are always removed, even in short documents. Documents with fewer than 3 pages therefore only lose page-number lines.
2. **Hyphenation**: a line ending in `-` followed by a line starting with a lowercase letter is joined without the hyphen.
3. **Line merging**: consecutive lines of the same block and same font size are merged into one paragraph; a paragraph break is kept when there is a vertical gap, indentation change, or the previous line ends with terminal punctuation and is noticeably shorter than the column width.
4. **Normalization of characters**: ligatures (ﬁ, ﬂ), smart quotes kept but normalized, non-breaking spaces, soft hyphens, repeated whitespace.
5. **Skipped content**:
   - Tables: PDF — `page.find_tables()` regions are excluded; DOCX — table elements skipped.
   - Captions: paragraphs starting with `Figure N`, `Fig. N`, `Table N` (case-insensitive) are dropped.
   - Footnotes: PDF lines in the bottom region with a noticeably smaller font than the body text, typically starting with a number or symbol, are dropped. Inline footnote markers (superscript digits) are removed.
   - Equations: lines with a high ratio of math symbols/Greek letters to letters, or using math fonts (e.g. names containing `CMMI`, `CMSY`, `Math`), are dropped.

Body font size = the most common font size by character count; it is used as the reference in cleaning and structure detection.

#### 6.3.6 Implementation rules added in Phase 1
These were found necessary on the development test set and are now part of the spec. Each is a named constant in the code.
- **Running headers by style**: a font + size combination in the top/bottom zone that differs from the body style, is not larger than body text, and appears on ≥ max(3, 30% of pages) pages is a running header, whatever its text. (Book headers that show the current chapter title change text every few pages, so the text rule alone misses them.)
- **Short documents**: for documents with 2 pages, a top/bottom-zone line whose normalized text appears on **every** page is a running header/footer. (Documents with ≥ 3 pages use the max(3, 30%) rule above.)
- **Captions**: `Figure N` / `Fig. N` / `Table N` must be followed by a separator (`:`, `—`, `–`, `-`, or `.` not followed by a digit), or set smaller than body text, so prose like "Figure 4.2 shows…" is kept.
- **Footnotes** are searched for in the bottom 25% of the page (not just the 8% footer zone). Superscript digits are dropped as footnote markers only when the previous span is not a math font (so exponents survive).
- **Equations**: the math-font share is measured over letters only; lines with no letters at all are dropped.
- **Tables without ruling lines**: a block where every visual line has ≥ 3 separately placed pieces is skipped. `find_tables()` is only called on pages whose drawings contain both horizontal and vertical edges (it is slow and finds nothing elsewhere).
- **Rotated text** (e.g. figure axis labels) is skipped.
- **Paragraphs across page/column breaks**: a body paragraph continues into the next block when it has no final punctuation and the next block starts with a lowercase letter.
- **Characters**: `¡`/`¿` → `!`/`?` (TeX ligature artifacts), U+FFFD removed.
- **Hyphenation and compounds**: when a line ends with a hyphen (`-`, soft hyphen, or U+2010) and the next line starts lowercase, the hyphen is **kept** only if the hyphenated form (`tea-time`) appears inside a line elsewhere in the document; otherwise the parts are joined without the hyphen. (Rev 4: the earlier "both halves are words" clause was removed; on `01_novel` it wrongly kept 6 of 95 splits such as `be-low`, `some-times`.)
- **DOCX table of contents**: paragraphs with `TOC N` styles are removed.
- **Column centre line** is the centre of the text area; a block that reaches more than 2% of the text width past the centre on both sides counts as full-width.
- **Tolerances used for "smaller"/"not larger" than body text**: running-header style ≤ body + 0.5 pt; captions ≤ 95% of body; footnotes ≤ 90% of body. Footnote markers are digits or `* † ‡ § ¶`.
- **Page-break continuation** additionally requires both blocks to be body-size text.
- **DOCX contents**: a `Contents` / `Table of Contents` paragraph directly before `TOC N` entries is removed with them.
- **Structure details**: a "frequent" heading level must occur on ≥ 2 different pages (PDF); a merged tiny chapter keeps its own title as the first paragraph of the chapter it joins (so the book title and author are read at the start of chapter 1); in the outline strategy, ties between equally good title matches go to the larger font; the patterns strategy uses `N.N` headings only when there are fewer than 2 top-level `N` headings.

### 6.4 Structure detection (`pipeline/structure.py`)
Produces an ordered list of chapters, each with a title and paragraphs. Strategies are tried in order; the first that yields a plausible result wins:

1. **Built-in outline** (PDF `get_toc()`): use level-1 entries (or level-2 if there is only one level-1 entry). Match each entry to its page and find the heading line on that page. Plausible = ≥ 2 entries.
2. **Heading styles** (DOCX): `Title`/`Heading 1` as chapter boundaries (`Heading 2` if there are fewer than 2 `Heading 1`s).
3. **Typography**: lines that are short (≤ 12 words), not ending in a period, and either ≥ 1.2× body font size or bold-on-their-own-line with whitespace above. Cluster heading sizes; the largest frequent level becomes the chapter level.
4. **Text patterns**: lines matching `^(chapter|part|book|section|unit|lecture)\s+([0-9]+|[ivxlcdm]+|one|two|…)\b` or numbered headings like `^\d+(\.\d+)?\s+[A-Z]`.
5. **Fallback**: no structure found → split into `Part 1`, `Part 2`, … of roughly 3,000 words each, at paragraph boundaries.

**Heading text.** When a heading line is only a chapter label (matches the pattern in strategy 4 with nothing after the number, e.g. `Chapter I`, `CHAPTER 3`, `Part Two`) and the next line is also heading-like (larger or bold, short), the two lines are combined into one title: `Chapter I: Down the Rabbit-Hole`. A chapter label with no title line after it stays as is (`Chapter XII`). Numbered headings keep their number (`2 Eigenvalues and Eigenvectors`).

Post-processing, applied **in this order**:
1. **Remove table-of-contents pages**: a page where ≥ 50% of the lines end with a page number or dot leaders, or that is headed `Contents` / `Table of Contents`, is dropped.
2. **Drop back matter**: a chapter titled (case-insensitive, ignoring numbering) `References`, `Bibliography`, `Works Cited`, `Index`, or `Acknowledgements`/`Acknowledgments` is dropped entirely.
3. **Front matter**: text before the first chapter (title page, author line, abstract, preface without a heading) becomes a chapter titled `Introduction` if it has ≥ 150 words; otherwise it is dropped. If the first real chapter is itself titled `Introduction`, the front matter chapter is titled `Front Matter` instead.
4. **Merge tiny chapters**: a chapter with fewer than **40** words (e.g. a part-title page `Part One` with nothing under it) is merged into the **next** chapter, and the next chapter's title is kept. If the tiny chapter is the last one, it is merged into the previous chapter, keeping the previous title. Real sections with a heading and a short body are **not** merged; short chapters are fine for listening.
5. **Split huge chapters**: chapters with > 12,000 words are split at sub-headings if any exist, otherwise at paragraph boundaries, into parts of roughly equal length titled `Title (Part 1)`, `Title (Part 2)`, ….
6. Number the chapters 1..N and record the strategy used in `document.json` (`structure_source`).

### 6.5 Text normalization for speech (`tts/normalize.py`)
Applied per segment before synthesis:
- Numbers, ordinals, years, decimals, percentages, currency (`₹`, `$`, `€`, `£`), simple dates.
- Common abbreviations: `Dr.`, `Mr.`, `Mrs.`, `St.`, `e.g.`, `i.e.`, `etc.`, `vs.`, `approx.`, `Fig.`, `No.`.
- Roman numerals in headings (`Chapter IV` → `Chapter Four`).
- URLs and emails → `link` / dropped; stray symbols (`*`, `#`, `|`, `•`) removed; `&` → `and`.

### 6.6 Chunking (`tts/chunk.py`)
Split each paragraph into sentences, then pack sentences into chunks of ≤ 1,500 characters without breaking a sentence (a single over-long sentence is split at `;`, `,` or spaces). Each chunk remembers which paragraph it belongs to so pauses can be inserted.

### 6.7 Synthesis (`tts/`)
- Interface (async, file-based so audio never sits in memory):
  ```python
  class TTSEngine(Protocol):
      name: str                      # "edge" | "piper"
      async def synthesize(self, text: str, out_path: Path, voice: str | None = None) -> None: ...
  ```
  Output is always MP3, 24 kHz, mono, 48 kbps. `voice` is an engine-specific voice id; `None` means the engine's configured narrator voice (`EDGE_VOICE` for edge, the configured model for Piper). Piper ignores voices it does not have and uses its narrator voice. v2 passes per-character voice ids.
- Piper's synthesis is blocking, so `PiperEngine.synthesize` runs Piper and the ffmpeg re-encode via `asyncio.to_thread`.
- **EdgeTTSEngine** (primary): `edge-tts` library, narrator voice from config.
- **PiperEngine** (fallback): local Piper model (`en_US-lessac-medium`), WAV output re-encoded with ffmpeg to the same MP3 format.
- Per chunk: try edge-tts up to 3 times with exponential backoff (1 s, 2 s, 4 s); if it still fails, use Piper for that chunk.
- Switching the whole job to Piper: a counter of **consecutive chunk failures** is kept per job (a chunk counts as failed when all 3 edge-tts attempts failed). It is updated in the order chunks **finish**, under a lock; any successful edge-tts chunk resets it to 0. When it reaches 5, every remaining chunk in the job uses Piper directly.
- Concurrency: chapters are processed strictly **one at a time, in order**. Within the current chapter up to 4 chunks are synthesized at once (config); chunks from different chapters never overlap. A chapter is marked `ready` only after its MP3 is assembled.
- **Fallback is configurable** (`TTS_FALLBACK=piper|none`). Locally the default is `piper`. **On the deployed server it is `none`** (D26) unless the Piper re-test on the Azure VM (1 GB RAM) passes; on Render's 512 MB free tier every Piper variant was killed (see `docs/spike_results.md`).
- With `TTS_FALLBACK=none`, edge-tts is retried more patiently: up to `TTS_MAX_RETRIES` attempts (deployed: 5) with backoff 1, 2, 4, 8 s. A chunk that still fails is a **failed chunk**. The consecutive-failure counter still applies: at 5 consecutive failed chunks the narration service is considered unavailable, the current and all remaining chapters are marked `failed` with `TTS_FAILED`, and the job ends as `partial` (if any chapter is ready) or `failed`.
- Piper settings (used only when `TTS_FALLBACK=piper`): `PIPER_DISABLE_ARENA=true`, `PIPER_MAX_CHARS=300`, model `en_US-lessac-low` or `-medium`.

### 6.8 Assembly (`tts/audio.py`, `tts/narrator.py`)
- All audio uses the same MP3 parameters, so files can be joined with the ffmpeg concat demuxer and `-c copy` (no re-encoding).
- The concat list file uses absolute paths written with forward slashes, each single-quoted with any `'` escaped as `'\''`, and ffmpeg is run with `-f concat -safe 0`. This works with Windows paths that contain backslashes or spaces.
- Pre-generated silence clips in the same format: 400 ms between paragraphs, 1,000 ms after the chapter title, 2,000 ms between chapters in the full book.
- Each chapter file starts with the narrator reading the chapter title.
- **No silent gaps**: if any chunk of a chapter fails on every available engine, the whole chapter is `failed` (its partial audio is discarded). A listener must never get a chapter with missing text.
- Chapter duration = the sum of the durations of its parts (each chunk and silence clip measured before joining from its MP3 frame headers, with ffprobe only when a file cannot be parsed). After joining, ffprobe also reads the joined file; if the two differ by more than 1 s, a warning is logged and the summed value is used. This avoids wrong durations from MP3 header information copied from the first part.
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
done | partial | failed → expired (after 24 h; files deleted, job kept as a stub for 1 more hour so the API can return JOB_EXPIRED)
```
- **Restarts**: jobs are not restored after a server restart. On startup the whole `storage/jobs/` folder is emptied, and any unknown `job_id` returns `JOB_NOT_FOUND`. The frontend treats `JOB_NOT_FOUND` and `JOB_EXPIRED` the same way: "This audiobook is no longer available (audio is temporary). Please upload the document again." 
- Upload-time validation errors (§6.1 steps 1–7) are returned directly from `POST /api/upload` with HTTP 4xx and **no job is created**.
- Errors found after the job starts (e.g. `TOO_MANY_WORDS`, `EMPTY_DOCUMENT`) set `status = failed` with `error` filled.

## 9. API contract

Base path `/api`. All errors use one shape:
```json
{ "error": { "code": "SCANNED_PDF", "message": "This PDF looks like scanned images..." } }
```

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/api/health` | health + version (used by deploy.sh and the frontend connection check) | `200 {"status":"ok","version":"…"}` |
| GET | `/api/config` | public limits for the frontend (max size, max pages, retention hours) | `200 {...}` |
| POST | `/api/upload` | multipart `file` → validate → create job | `202 {"job_id": "...", "status": "queued"}` |
| GET | `/api/jobs/{job_id}` | full job object (§7.2) | `200` |
| GET | `/api/jobs/{job_id}/chapters/{n}/audio` | stream chapter MP3 for the player; must support HTTP `Range` so seeking works | `200/206 audio/mpeg` |
| GET | `/api/jobs/{job_id}/download/{n}` | chapter MP3 as attachment, filename `{book}_{nn}_{title}.mp3` | `200` |
| GET | `/api/jobs/{job_id}/download/full` | full-book MP3 as attachment (only when `done`/`partial`) | `200` |

The pre-generated sample audiobook is **not** served by the backend; it is static files in the frontend (see §11).

Download filenames: `{book}_{nn}_{title}.mp3` (chapter) and `{book}.mp3` (full book), where `book` is the upload's filename without its extension. Both parts are sanitised: characters invalid on Windows (`< > : " / \ | ? *` and control characters) are removed, whitespace runs become `_`, and each part is cut to 60 characters. The `Content-Disposition` header carries an ASCII-only `filename="…"` (non-ASCII replaced by `_`) plus `filename*=UTF-8''…` with the full percent-encoded name.

HTTP status for each error code:

| Code | HTTP |
|---|---|
| `UNSUPPORTED_TYPE`, `LEGACY_DOC` | 415 |
| `FILE_TOO_LARGE` | 413 |
| `TOO_MANY_PAGES`, `CORRUPT_FILE`, `ENCRYPTED_FILE`, `SCANNED_PDF` | 422 |
| missing `file` field in the upload | 400 |
| `SERVER_BUSY` | 503 |
| `RATE_LIMITED` | 429 (with `Retry-After` in seconds) |
| `JOB_NOT_FOUND` | 404 |
| `JOB_EXPIRED` | 410 |
| `CHAPTER_NOT_READY` | 409 |
| `TOO_MANY_WORDS`, `EMPTY_DOCUMENT`, `TTS_FAILED` | not HTTP errors: they appear in the job object (`status: failed`, `error: {...}`) or on a chapter (`status: failed`) |

## 10. Storage and cleanup
- Root: `storage/jobs/` (path from config). Gitignored.
- A background task runs every 10 minutes and deletes job folders past `expires_at`. If a file is locked (Windows refuses to delete a file that is still being streamed), the `PermissionError` is caught and logged, and the folder is retried on the next run.
- On startup, the whole `storage/jobs/` folder is emptied (jobs are not restored).
- **Azure VM reality**: the server is always on, so the 24 h retention holds in normal operation. A redeploy or container restart still empties `storage/jobs/` (jobs are not restored, D18). The UI therefore still says "temporary, download what you want to keep" and does not promise an exact time.

## 11. Frontend

Routing uses `react-router-dom` (approved dependency). `frontend/vercel.json` rewrites every path to `/index.html` so deep links like `/book/:jobId` work on Vercel.

Routes:
- `/` — upload card (drag-and-drop + picker, client-side type/size check, rejection messages), **Try a sample** button, short "how it works", limits shown from `/api/config`.
- `/book/:jobId` — progress bar + ETA, chapter list (title, duration, status, play, download), sticky player, **Download full book**, "audio is temporary" notice. If the API returns `JOB_NOT_FOUND` or `JOB_EXPIRED`, show the "no longer available" message from §8.
- `/sample` — the same book view backed by the static sample: `frontend/public/sample/job.json` (a job object in the §7.2 shape, status `done`) and `frontend/public/sample/ch_001.mp3` …, served by Vercel. The book view takes a data source (API or static sample) so the same components render both.

Player behaviour:
- Single `<audio>` element; source = current chapter's audio URL.
- Speed selector 0.5×, 0.75×, 1×, 1.25×, 1.5×, 1.75×, 2× via `playbackRate` (pitch preserved by the browser).
- Previous/next chapter; auto-advance to the next **ready** chapter on end; if the next chapter is not ready yet, show "waiting for chapter N…" and continue when it is.
- Last chapter + position saved in `localStorage` per `job_id` (wrapped in try/catch).

Polling: `GET /api/jobs/{id}` every 3 s while status is `queued`/`extracting`/`synthesizing`; stop when finished. Polling must survive dropped connections: retry with backoff (1, 2, 4, 8 s) for up to 2 minutes before showing a connection error.

Connection check: if `/api/health` fails or takes > 5 s, show "Can't reach the server right now. Retrying…" and keep retrying in the background.

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
| `RATE_LIMITED` | You've uploaded a lot of documents in the last hour. Please try again later. |
| `JOB_NOT_FOUND` | This audiobook is no longer available (audio is temporary). Please upload the document again. |
| `JOB_EXPIRED` | This audiobook is no longer available (audio is temporary). Please upload the document again. |
| `CHAPTER_NOT_READY` | This chapter is still being generated. |
| `TTS_FAILED` | Narration failed for this chapter. |

## 13. Evaluation
- Test set in `testset/` with hand labels (`labels.json`): expected accept/reject, the expected ordered chapter titles (`chapters`), and titles that may or may not appear without counting for or against the detector (`optional`).
- **Title normalization** (applied to both detected and labeled titles): lowercase; strip a leading chapter label (`chapter|part|book|section|unit|lecture` + arabic/roman/word number) and the `:`/`.`/`-` after it; strip leading section numbers like `4.1` or `2`; strip a trailing `(part N)`; remove punctuation; collapse whitespace.
- Normalization also strips a leading bare arabic or roman number followed by a period and a space (`XVII. The Idiot` → `the idiot`, `3. Convexity` → `convexity`). (Rev 4.)
- If normalization leaves an **empty** string (e.g. a heading that is only `Chapter 12`), the title is instead normalized as lowercase + punctuation removed + whitespace collapsed, keeping the label (`chapter 12`). Two titles that used this fallback match only if identical (`fuzz.ratio` would score `chapter 12` vs `chapter 13` at 90). Empty titles never match anything.
- Expected-accept files that are rejected, crash, or are missing count all their labeled chapters as missed (recall), and a crash is recorded as `CRASH` instead of stopping the run.
- **Matching**: score = `rapidfuzz.fuzz.ratio` of the normalized titles (0–100). Pairs with score ≥ 80 are candidates. Matching is **one-to-one**: repeatedly take the highest-scoring remaining candidate pair and remove both titles. Order is not required for a match.
- Detected titles that match an `optional` label (same rule) are removed before scoring, and unmatched `optional` labels are ignored.
- **Metrics** per document: TP = matches, precision = TP / detected, recall = TP / labeled, F1; plus `order_ok` = whether the matched pairs appear in the same order in both lists. Overall precision/recall/F1 are micro-averaged (sum TP, detected and labeled over all documents).
- `scripts/evaluate.py` runs the pipeline on every test document and reports:
  - rejection correctness (expected vs actual error code);
  - chapter detection precision, recall, F1 and `order_ok` (rules above);
  - which structure strategy was used;
  - leaked-noise check: number of lines in the output that look like page numbers/headers.
- Results are written to `testset/results.md` and quoted in the README and viva.

### 13.1 Held-out set
The development set (`testset/`) was inspected while the pipeline was built, so its scores are optimistic. A second set, `testset_heldout/` (9 files made with different tools: LibreOffice, reportlab, Word-style headings; plus encrypted, corrupt, partially scanned, too many words and too many pages), is **never opened or inspected during development**.
- It is evaluated with `python scripts\evaluate.py --testset ..\testset_heldout`, which writes `testset_heldout/results.md`.
- The **first** run after a change to the pipeline is the reported held-out score. If held-out failures are later fixed, the file is re-run and both the first and the latest scores are kept in `testset_heldout/results_history.md`, so it stays clear which number is truly held-out.

## 14. Deployment
- **Frontend**: Vercel, env `VITE_API_URL`.
- **Backend**: Azure for Students, one **B2ats v2** Linux VM (2 vCPU AMD x64, 1 GiB RAM; free 750 h/month for 12 months), Ubuntu 24.04 LTS, region Central India, 64 GB Premium SSD OS disk, Standard static public IP (paid from the student credit) with an Azure DNS name `<label>.centralindia.cloudapp.azure.com`.
  - The app runs as the existing Docker image (`backend/Dockerfile`), one container, one uvicorn worker, `restart: unless-stopped`, job storage on a Docker volume.
  - **Caddy** (second container) terminates HTTPS with an automatic Let's Encrypt certificate for the Azure DNS name and reverse-proxies to the app; request body limit 55 MB.
  - Managed with `deploy/docker-compose.yml`; `deploy/bootstrap.sh` (one-time VM setup: Docker, 2 GB swap file, firewall, unattended security updates) and `deploy/deploy.sh` (git pull → build → restart → health check).
  - Network security group: inbound 80 and 443 from anywhere; 22 (SSH) only from the developer's IP; SSH key login only.
  - A $5 budget alert in Azure Cost Management guards the credit.
- Still keep memory in mind (1 GiB): stream files to disk, never hold a whole book's audio in memory.
- Sample audiobook is pre-generated locally and served as static files so it plays instantly.
- **History — measured on Render free (2026-10-17 spike, why we moved)**: idle server 148 MB; edge-tts works at 229 chars/s (98 MB); extracting a 200-page PDF takes 7.8 s at 103 MB; every Piper variant was killed for exceeding 512 MB. Consequence then: `TTS_FALLBACK=none` (D26), and the Docker image is built **without** Piper voices by default (`--build-arg INCLUDE_PIPER=false`).
- Render also ran the first real jobs (06: 172 chars/s at 0.15 CPU, no crash); the free tier's 0.15 CPU and 15-minute sleep were the reasons to move (D28).
- **Azure acceptance check** (Phase 3b): edge-tts works from Azure's network; smoke tests for `06_no_styles.docx` and `01_novel.pdf` pass over HTTPS; chars/s and peak memory recorded in `docs/spike_results.md`; optional Piper re-test (`piper-low`, arena off, 300-char pieces) under the 1 GiB limit decides whether `TTS_FALLBACK=piper` is enabled.

## 15. v2 — Character voices

v1 behaviour is unchanged and stays the default. v2 adds an opt-in **character voices** mode: narration in the narrator voice, each identified character's dialogue in that character's voice (D10, D31).

### 15.1 Mode and API changes
- `POST /api/upload` accepts an optional form field `narration_mode`: `single` (default) or `characters`. Any other value → 400 `BAD_REQUEST`.
- Job object (§7.2) gains:
  - `narration_mode`: `single` | `characters`.
  - `status` gains `attributing` between `extracting` and `synthesizing` (characters mode only).
  - `progress.windows_total`, `progress.windows_done` (attribution windows; 0 in single mode).
  - `cast`: list of `{ "name", "aliases": [], "gender": "female|male|unknown", "lines": int, "voice": "<edge voice id>", "voice_label": "e.g. Voice 3 (female, British)" }`, ordered by `lines` descending; empty in single mode. The narrator is not in the list.
  - per chapter `attribution`: `llm` | `baseline` | `mixed` | `none` (single mode) — which method produced its speakers.
- `GET /api/config` adds `character_voices_available: bool` (false when no LLM key is configured; the upload page then hides the option).
- Warnings (not errors): `"Speaker detection used the simpler rule-based method for N chapters (the AI service was unavailable or busy)."`

### 15.2 Dialogue segmentation (`app/dialogue/segment.py`)
Turns each paragraph's single narrator segment into alternating `narration` and `quote` segments. Rules:
1. **Quote style per document**: for each family — double (`“ ” "`) and single (`‘ ’ '`) — count the marks that are **not** apostrophes (rule 2) per 1,000 words. The family with the higher rate is the document's dialogue family, provided it has at least 2 marks per 1,000 words (otherwise the document has no dialogue and every segment stays narration). Curly and straight marks of the family are treated alike. The other family is ordinary text inside quotes (so `'` inside a double-quote book is never a boundary).
   - **Open vs close is decided by position, never by glyph** (PDFs swap curly marks, e.g. `table,‘ said Alice; ’it‘s`): a mark is an *opener* if it is at paragraph start or follows whitespace, `(`, `[`, `—`, `–`, and is followed by a non-space; it is a *closer* if it follows a non-space and is followed by whitespace, punctuation or paragraph end. A mark that is both or neither is resolved by the current state (inside a quote → closer, outside → opener).
2. **Apostrophes (single family only)**: any of `' ‘ ’` is an apostrophe, never a boundary, when it is (a) between two letters (`don't`, `Alice's`, `doesn‘t`); (b) the start of a word in the leading-elision list (`'tis 'twas 'em 'cause 'til 'twere 'ere 'ow 'e`); (c) after `s` at the end of a word **while no quote is open** (plural possessive `the Queens' `); (d) at the end of a word in the trailing-elision list (`o' an' talkin' nothin' somethin' goin' ol'`, plus any word ending `in'` of 4+ letters that is followed by a space and a lowercase word that is not a speech verb, e.g. `goin' home`; taken literally, the bare `in'` rule would swallow closers after `again`, `begin`, `within`). The two lists are named constants; additions must be justified by the dev set and reported.
3. **Multi-paragraph quotes**: a paragraph that ends inside an open quote, followed by a paragraph that starts with an opening mark, continues the same utterance (typographic convention) — **unless** the next paragraph contains an explicit speech tag naming a speaker (`said X`, `X said`), in which case the open quote is closed at the end of the first paragraph (damaged-PDF case: missing closing mark).
   - **3b. In-paragraph damaged closer** (added in Phase 6.1): while a quote is open, one of `! ? , . ;` followed by whitespace and a speech tag (`said Alice`, `the Hatter said`, `Alice said`, a lowercase pronoun + verb such as `she thought`, optionally with an `-ly` adverb or a leading `and`) closes the quote right after the punctuation. Case-sensitive: a new sentence inside a quote would be capitalised. Known risk: reported speech inside a correctly closed quote (`‘…, said I, …’`).
   - An opener-only mark while a quote is already open closes the open quote just before it and starts a new one (counted as `reopened`).
4. **Broken quotes**: `“Well,” said Alice, “I suppose so.”` → two quote segments around a narration segment; both belong to one **utterance** id (they share a speaker). Two quotes in one paragraph are one utterance unless the second has its own speech tag right after it, or the narration between them ends a sentence and introduces it (`The Hatter said, ‘…’`), or the narration starts with `and` / `but` / `while` followed by a newly named speaker (`… and the Dormouse sulkily remarked, ‘…’`; pronouns don't trigger this).
5. Quote spans **include** their marks (start at the opener, end after the closer). Marks are stripped before TTS. Nested quotes (the other family) inside a quote stay part of the outer quote.
5a. **Labelling conventions** (also used by the evaluation): quoted thoughts (`‘…,’ thought Alice`) are utterances of the thinker; speech by several characters at once (`‘No room!’ they cried`) is `UNKNOWN`; quoted writing that nobody says aloud (a label `‘DRINK ME’`, a quoted word inside narration) is **not** an utterance.
6. Unbalanced quote at paragraph end without a continuation → close it at paragraph end and log a warning count.
Interface: `find_quote_spans(paragraphs, family)` is the shared core (also used by the evaluation); `segment_chapter(chapter, family) -> (Chapter, SegmentStats)`.
Output: segments carry `kind` (`narration` | `quote`), `utterance_id` (quotes only), and `speaker` (`"narrator"` for narration; filled by attribution for quotes; `"narrator"` if unknown — D33).

### 15.3 Speaker attribution (`app/dialogue/attribute.py`)
Two methods behind one interface; both return, per utterance, a speaker name or `UNKNOWN`, plus character info.

**Baseline (rule-based)**:
- Speech-verb list (said, asked, cried, replied, shouted, whispered, answered, exclaimed, added, went on, continued, remarked, muttered, began, called, thought, …).
- Explicit tags adjacent to the utterance: `<quote> said NAME`, `<quote> NAME said`, `NAME said, <quote>`, `said the NOUN PHRASE` (`said the Hatter`, `said the March Hare`). NAME = capitalised name or `the` + capitalised/lowercase noun phrase up to 4 words.
- Pronoun tags (`she said`) → the most recent explicitly named character of matching gender in the last 3 paragraphs if exactly one candidate, else UNKNOWN.
- Turn-taking: inside a run of consecutive dialogue-only paragraphs, if the last two explicitly tagged speakers are A then B, untagged utterances alternate A, B, A… until the run ends.
- `they` tags (`they cried`) are group speech: UNKNOWN, and they end a turn-taking run.
- Sentence-initial pronouns and conjunctions (`She`, `But`, `Then`…) are never names; lowercase noun phrases (`said the girl shortly`) end before an `-ly` adverb or a preposition.
- Turn-taking runs over paragraphs that contain a quote and at most 20 words of narration. Pronoun tags look at names from explicit tags in the current and previous 2 paragraphs.
- Everything else → UNKNOWN.

**LLM (Groq, D32)**:
- **Per chapter, ahead of narration** (D37): chapter N+1 is attributed while chapter N is narrated; chapter 1 is attributed before narration starts. Within a chapter, windows of consecutive paragraphs up to `LLM_WINDOW_WORDS` (default 1,200) that never split an utterance (a multi-paragraph quote or a broken quote stays in one window), plus the previous 2 paragraphs as read-only context **without** ids. Utterances are numbered `Q1…Qn` inline as `[Q3]‘…’`.
- Output format is terse: `{"q":{"Q1":"Alice","Q2":"UNKNOWN"},"new":[{"name":"Dormouse","gender":"male","aliases":[]}]}` — speakers for every id, plus only characters not already in the supplied list. The supplied running list is capped at the 30 most frequent characters (name + gender only), so prompts don't grow without bound. Model "thinking"/reasoning output is disabled where the model allows it.
- One request per window (system + user prompt), `temperature 0`, JSON response format. `speaker` is a name from the supplied list, a name in `new`, or `"UNKNOWN"`. The prompt states the §15.2 rule 5a conventions (thoughts → thinker, group speech → UNKNOWN).
- Validation: every id answered exactly once, speakers non-empty strings; otherwise one retry with the validation error appended; then the **baseline** result is used for that window (D34).
- Rate limiting is **token-based**: estimate each request's tokens (prompt chars ÷ 4 + expected output) and keep a rolling 60 s budget of `LLM_TOKENS_PER_MINUTE` (default 7,000, below Groq's 8,000); also honour `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens`, `retry-after` and 429. `LLM_MAX_REQUESTS_PER_DAY` (default 800) caps daily use. If the daily budget is exhausted, or a window waits more than 120 s for budget, that window uses the baseline (D34).
- **Output-token pacing (D42)**: the limiter also keeps a rolling 60 s budget of completion tokens, `LLM_OUTPUT_TOKENS_PER_MINUTE` (default 1,000, Groq's limit for the chosen model on this key), estimated before a request (≈ 12 tokens per quote id + 40) and corrected from `usage` afterwards, so requests are paced instead of bouncing on 429.
- Requests use `httpx` against Groq's OpenAI-compatible endpoint (no new SDK dependency). Model from `LLM_MODEL` (D40: `qwen/qwen3.8-27b`, `reasoning_effort: "none"`, which Groq accepts but which changes nothing: the model doesn't think there). Method from `ATTRIBUTION_METHOD` (D40: `llm`).
- Results cached per job (window hash → response) so a retry never pays twice. The evaluation additionally caches responses on disk (`backend/storage/llm_cache/`, gitignored, keyed by model + prompt hash) so re-scoring never calls Groq again.
- Every request records prompt tokens, completion tokens (from the response `usage`) and latency, for the evaluation.

**Model choice and combining method — selection rule fixed before any results (D39)**:
- Candidates: `baseline`; and for each model (initially `qwen/qwen3.8-27b` with thinking disabled, `openai/gpt-oss-120b` at its lowest reasoning effort) the methods `llm` and `llm_with_baseline_override` (an explicit `said NAME` / `NAME said` / `said the NOUN` tag found by the baseline wins over the LLM).
- Score: **micro voice accuracy** on the dev chapters (§15.9).
- Pick the highest. Every candidate within **2 percentage points** of the best is a tie; among ties prefer, in order: fewer total tokens on the dev set, then lower median window latency, then `llm_with_baseline_override` over `llm`.
- The result is recorded as D40 (model, method, the scores table) and becomes the default `LLM_MODEL` / `ATTRIBUTION_METHOD`. Prompt and rules are frozen after this; the held-out chapter is then run once (Phase 6.3).

### 15.4 Cast building (`app/dialogue/cast.py`)
- Canonical name normalisation: strip leading `the`, honorifics (Mr, Mrs, Miss, Dr, Professor, Sir, Lady, Father), punctuation; casefold.
- Merge: LLM aliases; plus rapidfuzz `token_set_ratio ≥ 90` or one name's tokens a subset of the other's (`Hatter` ⊂ `the Mad Hatter`), only when genders don't conflict.
- Gender: majority of LLM reports, else pronoun counts near the character's tags, else `unknown`.
- Display name: the most frequent surface form.

### 15.5 Voice casting (`app/dialogue/voices.py`)
- **Casting (D41, replaces the up-front assignment)**: voices are assigned **lazily**, just before each chapter is narrated, using that chapter's attribution (LLM names and genders), and never change afterwards.
  - The whole-book baseline pass (local, no LLM) is used only to **reserve** distinct voices: up to `MAX_CAST − 2` capitalised names (never lowercase epithets like `the girl`) with at least 3 explicitly tagged lines in the book. A reserved character gets its voice on first narration, with the gender the LLM reports then.
  - Unreserved characters get a free distinct voice on first narration if they have at least 2 lines in that chapter and slots remain; otherwise the minor voice of their gender.
  - So a major character is never pushed to a minor voice by junk names, and the gender used is the LLM's, not the baseline's guess.
- Narrator: `EDGE_VOICE` (unchanged).
- Pools: English edge-tts neural voices, split female / male (verified list in config, excluding the narrator voice), ordered so adjacent picks differ in accent.
- The `MAX_CAST` (default 8) characters ranked highest get distinct voices: females from the female pool, males from the male pool, `unknown` from whichever pool has more unused voices.
- All other characters share one "minor female" / "minor male" voice; `unknown`-gender minors use the minor voice of the larger pool.
- `UNKNOWN` speaker → narrator voice (D33).
- Deterministic: same cast → same voices.

### 15.6 Narration changes
- Chunking (§6.6) never puts two segments with different speakers into one chunk; each chunk carries its voice.
- Pause between segments of different speakers inside a paragraph: 150 ms. Quote marks removed before synthesis.
- Chapter titles are always read by the narrator.
- Everything else (order, concurrency, D27 no-gap rule, assembly) is unchanged.

### 15.7 Limits
- Same document limits as v1 (§4).
- Characters mode is offered only when `GROQ_API_KEY` is set.
- Expected LLM use: ~1 request per 1,200 words (≈ 85 for a 100,000-word book, ≈ 200k tokens). The binding limit is the model's **1,000 completion tokens/minute**: a dialogue-dense window (56 quotes) needs ~650, a typical one ~300, so 1.5–3 windows per minute and **≈ 30–60 min of attribution for the largest book**, longer than its narration (~15 min). Attribution runs one chapter ahead (D37), so listening can start after chapter 1 is attributed and narrated; later chapters may wait for attribution. Daily request/token caps → remaining windows use the baseline (D34).

### 15.8 Frontend
- Upload page: a two-option choice "Single narrator" (default) / "Character voices (beta)", with one line: "Uses an AI service (Groq) to work out who is speaking; the document's text is sent to it." Hidden when `character_voices_available` is false.
- Progress: "Finding the characters… (window 3 of 12)" during `attributing`.
- Book page: a **Cast** section (name, voice label, number of lines; "Narrator" first) and per-chapter note when `attribution` is `baseline`.
- Sample: add a second static sample in characters mode (Alice ch. 7) at `/sample/characters`.

### 15.9 Evaluation (`scripts/evaluate_dialogue.py`)
- Labels: `testset_dialogue/labels/<chapter>.jsonl`, one line per gold quote span: `{"id", "paragraph" (0-based index into the chapter text file), "start", "end" (character offsets, end exclusive, marks included), "text", "speaker", "explicit": bool, "utterance"}` plus `<chapter>.cast.json` (canonical name → `{"aliases": [], "gender"}`). Created by the user with `scripts/label_dialogue.py`, following the §15.2 rule 5a conventions.
- Metrics per chapter and micro-averaged:
  - **Quote detection**: precision / recall / F1, a detected span matching a gold span when their character overlap ≥ 80% of the longer one.
  - **Attribution accuracy** on matched utterances (speaker equal after alias mapping), split into **explicit** (gold `explicit: true`) and **implicit**; also % predicted UNKNOWN.
  - **Voice accuracy** (what the listener hears): using the system's own voice map for the chapter, the *expected* voice of a gold utterance is the voice the system assigned to the gold speaker's character (matched by alias), else the minor voice for the gold speaker's gender, else the narrator voice if gold is `UNKNOWN`; a matched utterance is voice-correct if its synthesized voice equals the expected voice.
  - For each method: baseline, llm, llm_with_baseline_override; plus LLM requests and seconds per chapter.
  - A gold utterance is scored once, using the predicted utterance of its first matched span; gold utterances missed by detection are counted, not scored.
- **Label lint** (`label_dialogue.py --lint`): label-file-only checks (no segmenter): overlapping gold spans (error), spans that don't start/end at a quote mark, spans that may contain narration (punctuation + speech tag inside the span), `Not a quote` decisions followed by `thought`, and adjacent spans separated by a speech tag that don't share an utterance (warnings).
- Sets: dev = `alice_ch07`, `brown_glass`; **held-out = `ball_ch03`** (never inspected during development; first run reported — D25 applies).

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
│   ├── models/                (Piper voice files, gitignored; Phase 2)
│   ├── Dockerfile             (Phase 5)
│   └── storage/               (gitignored)
├── frontend/                  (Vite + React, react-router-dom, vercel.json; sample in public/sample/)
└── testset/
    ├── README.md
    ├── labels.json
    └── docs/                  (gitignored)
```
