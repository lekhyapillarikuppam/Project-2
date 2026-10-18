# Viva preparation — Readaloud (AI-Powered Audiobook Generator)

Live site: <VERCEL_URL> · sample: <VERCEL_URL>/sample ·
backend health: https://lekhya-audiobook.centralindia.cloudapp.azure.com/api/health

---

## 1. Two-minute summary

**What.** A web app that turns a PDF or Word document into a chaptered audiobook. You upload a file,
and a few seconds later you can play chapter 1 while the rest is narrated. Every chapter, or the
whole book, can be downloaded as MP3. No accounts, no paid services.

**Why.** Most documents are written for reading, not listening. Plain text-to-speech reads page
numbers, running headers, footnotes and figure captions, breaks words at line-end hyphens, and loses
the chapter structure. The interesting work is recovering clean, structured text from documents that
only store *positioned text on pages*.

**How.**
1. Validate the upload (type and file signature, size, pages, encryption, scanned pages).
2. Extract text with layout information (PyMuPDF for PDF, python-docx for Word), including a
   two-column reading order.
3. Clean it: remove running headers/footers and page numbers, rejoin hyphenated words, drop captions,
   footnotes, tables and equations.
4. Detect chapters with a cascade of strategies: PDF outline → Word heading styles → typography
   (font size / bold) → text patterns ("Chapter IV") → fixed-size fallback parts.
5. Normalize text for speech (numbers, abbreviations, symbols), split it into sentence chunks, and
   narrate with a neural voice (edge-tts), 4 chunks in parallel.
6. Join the chunks and silences into chapter MP3s with ffmpeg without re-encoding, and stream them
   to a React player that supports seeking, speed control and auto-advance.

**Results.**
- Chapter detection: **0.99 F1 on a held-out set on the first run** (78 of 79 chapters, 4/4 bad files
  rejected, no leaked page numbers or headers); 1.00 on the development set.
- On a free Azure student VM it narrates 600–720 characters per second: *Alice in Wonderland*
  (2 h 36 min of audio) is ready in about 3 minutes, using about 130 MB of memory.

---

## 2. Where is the AI / data science?

- **Neural text-to-speech.** The voices are neural TTS models. edge-tts uses Microsoft's neural voices
  (here `en-US-AriaNeural`); Piper, used locally, is an open neural model (VITS architecture, run with
  ONNX Runtime). I measured both: speed in characters per second, and memory. The measurements decided
  which one runs on the server.
- **Text normalization for speech.** Turning written text into speakable text is a classic TTS
  front-end task: numbers, years, ordinals, decimals, percentages, currency, abbreviations
  (`Dr.`, `e.g.`), Roman numerals in headings, symbols.
- **Document structure recovery as classification and segmentation.**
  - Every line is classified: body text, heading, running header/footer, page number, caption,
    footnote, equation, table cell.
  - The document is then segmented into chapters.
  - I used interpretable features: font size relative to the body size, bold, position on the page,
    repetition across pages, text patterns.
  - The rules use explicit, named thresholds instead of a trained model (why: question 1 below).
- **Evaluation methodology.**
  - A hand-labelled test set gives the expected accept/reject decision and the expected chapter
    titles in order.
  - Precision, recall and F1 are computed with fuzzy one-to-one matching of titles.
  - A separate held-out set was never looked at, to avoid overfitting the rules.
  - A noise metric counts leaked headers and page numbers.
- **Empirical system measurements driving design.**
  - Measured: throughput (chars/s) and peak memory on Render vs Azure, and Piper's memory with
    different settings.
  - Those numbers caused real decisions: moving to Azure (D28), edge-only narration on the server
    (D26), and a faster assembly step.

---

## 3. Likely examiner questions

1. **Why heuristics instead of an ML model for headings?**
   - There was no labelled training data at the scale a model needs; I had about 20 labelled
     documents, which is enough to *evaluate*, not to *train*.
   - The signals are strong and interpretable (font size, bold, position, repetition), and each rule
     can be explained and debugged.
   - It runs in milliseconds with no GPU.
   - With a larger labelled corpus, a line classifier (e.g. gradient boosting on the same features)
     would be the natural next step.

2. **Why is the development score 1.00 but you report 0.99?**
   - I looked at the development documents while writing the rules, so their score is optimistic.
   - The held-out set was made with different tools and never opened during development. Its
     **first** run gave 0.99 (one missed chapter title), and that is the honest number.
   - I later fixed that case and the held-out set now scores 1.00, but that later number is no longer
     "held out". Every run is logged in `testset_heldout/results_history.md`.

3. **What happens if edge-tts goes down?**
   - Each chunk is retried up to 5 times with backoff (1, 2, 4, 8 s).
   - If a chunk still fails, its whole chapter is marked failed, and the UI says "Narration failed
     for this chapter". I never produce audio with silently missing text (D27).
   - After 5 consecutive failed chunks the service counts as unavailable: the remaining chapters
     fail, and the job ends as partial or failed.
   - Locally, Piper can be the fallback. On the server it doesn't fit in memory.

4. **Why not OCR?**
   - OCR is slow, error-prone on old scans, and would multiply processing time and memory on a free
     VM.
   - Scanned PDFs are detected (most pages have an image and almost no text) and rejected with a clear
     message.
   - PDFs with only a few scanned pages are accepted: those pages are skipped with a warning.

5. **How are two-column PDFs handled?**
   - Text blocks are classified as full-width, left or right relative to the text area's centre line.
   - A page is two-column if both sides have at least 3 blocks and together hold most of the
     characters.
   - Full-width blocks split the page into bands; inside each band I read the left column top to
     bottom, then the right.

6. **How do you avoid reading page numbers and headers?**
   - Lines in the top or bottom ~8% of the page that repeat on at least max(3, 30%) of the pages are
     running headers/footers, comparing text with digits and Roman numerals stripped.
   - A second rule catches headers set in a distinctive font even when their text changes, such as
     the current chapter title.
   - Lines that are only a number, "Page N" or "N of M" are always removed.
   - The noise metric checks for leaks; it is 0 on both test sets.

7. **Why Azure instead of Render?**
   - Render's free tier gives 0.15 CPU and sleeps after 15 minutes. Narration ran at 172 chars/s with
     long stalls, and a sleeping server loses its jobs.
   - The Azure for Students VM (2 vCPU, 1 GiB, always on) narrates at 600–720 chars/s, and audio
     really stays available.

8. **How are chunks joined without re-encoding?**
   - Every piece (speech chunks and silence clips) has exactly the same MP3 format: 24 kHz, mono,
     48 kbps.
   - So ffmpeg's concat demuxer can join them with `-c copy`, which is fast and lossless.
   - Durations are computed by counting MP3 frames (each frame is 24 ms), and checked once with
     ffprobe on the joined file.

9. **How does seeking work?** The browser's `<audio>` element sends HTTP `Range` requests and the
   server answers `206 Partial Content` with just the requested bytes. So you can jump to minute 10
   without downloading the first 10 minutes.

10. **What limits exist, and why?**

    | Limit | Value | Why |
    |---|---|---|
    | File types | PDF and DOCX only | Reliable extraction |
    | File size | 50 MB | Bounded upload and disk |
    | PDF pages | 200 | Bounded processing time |
    | Words | 100,000 | ≈ 15 minutes of narration |
    | Jobs | one at a time, up to 3 waiting | Memory and CPU on one small VM |
    | Uploads per IP | 10 per hour | Public site on student credit |
    | Retention | at most 24 h | No paid storage |

11. **What is the job lifecycle?**
    - `queued → extracting → synthesizing → done`, or `partial` if some chapters failed, or `failed`.
    - After 24 hours the job becomes `expired` and its files are deleted.
    - Upload-time problems are rejected before any job exists.
    - The frontend polls the job every 3 s and enables each chapter's play button as soon as it is
      ready.

12. **How would you scale to many users?**
    - Today there is one process with an in-memory job registry (simple, and enough for a class demo).
    - To scale: a persistent queue (e.g. Redis) and job database, several worker containers, object
      storage for audio, and a CDN for downloads.
    - Possibly a paid TTS API with a service-level agreement instead of the unofficial edge endpoint.

13. **Privacy?**
    - No accounts. Files and audio live only in a temporary job folder and are deleted within 24 hours;
      a restart deletes them sooner.
    - The text is sent to Microsoft's edge-tts service to be spoken, so users shouldn't upload
      confidential documents.
    - The server keeps no history apart from short-lived logs.

14. **What does v2 add?**
    - One voice per character: detect dialogue, use a free LLM to attribute each line to a speaker,
      merge aliases, and give each character a distinct voice.
    - The data model already has a `speaker` field on every segment for this (D11).
    - Attribution accuracy would be measured on a labelled chapter.

15. **How do you know where a chapter starts in a PDF without an outline?**
    - Body size = the most common font size by character count.
    - Heading candidates are short lines (≤ 12 words), not ending in a period, that are ≥ 1.2× body
      size or bold on their own line.
    - Their sizes are clustered, and the largest frequent level becomes the chapter level.
    - If that fails, text patterns ("Chapter 3", "Part II", numbered headings) are tried, then
      fixed ~3,000-word parts.

16. **How does the title matching in the evaluation work, and why fuzzy?**
    - Titles are normalized (lower-case, labels like "Chapter IV" and section numbers stripped,
      punctuation removed) and compared with `rapidfuzz` ratio.
    - Pairs ≥ 80 are matched one-to-one, best first.
    - Fuzzy matching tolerates tiny extraction differences (a ligature, a dash) without rewarding
      wrong titles.

17. **Why can I play chapter 1 before the book is finished?** Chapters are narrated strictly in
    order, and each is assembled into its own MP3 as soon as its chunks are done (D13). Within a
    chapter, 4 chunks are narrated in parallel.

18. **How did you test it?**
    - 238 backend tests (pytest) and 33 frontend tests (Vitest + Testing Library).
    - Two evaluation sets.
    - A smoke script that runs a whole book through the live API.
    - Manual checks on desktop and phone, and Lighthouse accessibility (100).

19. **Why is memory such a concern?**
    - The VM has 1 GiB, about 837 MB usable.
    - Audio is never held in memory; everything streams to files.
    - The app peaks at about 130 MB on a full novel.
    - Piper would need about 700 MB together with the app, which is why it isn't used on the server.

20. **What was the hardest bug?** Pick one you can tell well:
    - Running headers that change text every few pages, solved with the style-based header rule.
    - Hyphenated compounds (`tea-time` vs `be-low`), solved by keeping the hyphen only when that
      hyphenated form appears elsewhere in the document.
    - Render's proxy dropping keep-alive connections, which led to retrying GETs in the client.

---

## 4. Known limitations (say these plainly)
- A 2-page document can keep a header that appears on only one of its pages; the header rule needs
  repetition to be sure.
- Equations are skipped, so a sentence that contained an equation can have a gap.
- A compound word split across a line break may lose its hyphen if the hyphenated form doesn't appear
  anywhere else in the document.
- edge-tts is an unofficial endpoint and could change or stop; retries and D27 make failures visible,
  but they can't prevent them.
- English only (normalization rules and voice).
- Scanned PDFs are rejected (no OCR).
- Jobs are kept in memory: a server restart or redeploy loses running jobs and their audio.

---

## 5. Three-minute live demo script

| Time | Do | Say |
|---|---|---|
| 0:00 | Open <VERCEL_URL> | "Readaloud turns PDFs and Word files into chaptered audiobooks." |
| 0:15 | Click **Try a sample**, press play | "This is a pre-made sample, served as static files, so it works even without the backend." |
| 0:35 | Back to the start page; upload `01_novel.pdf` (Alice) | "The server checks the file, extracts the text with its layout, cleans it and finds the chapters." |
| 0:55 | Show the progress panel and the chapter list | "Chapters are narrated in order. Chapter 1 is ready after about 10 seconds." |
| 1:10 | Play chapter 1 while the others are still narrating | "I can listen now; the rest keeps going in the background." |
| 1:30 | Change speed to 1.5×, seek forward, press → | "Speed is changed in the browser with pitch preserved; seeking uses HTTP Range requests." |
| 1:50 | Download one chapter | "Every chapter, and later the whole book, downloads as an MP3 with a readable name." |
| 2:05 | Upload `08_scanned.pdf` | "Scanned documents are detected and rejected with a clear message: no OCR in this version." |
| 2:25 | Open the README's Evaluation table | "Chapter detection is evaluated on labelled documents: 0.99 F1 on a held-out set I never looked at during development." |
| 2:50 | Back to the Alice tab | "By now the whole book is nearly done: two and a half hours of audio in about three minutes." |

**If the network is bad:**
- The sample (`/sample`) plays without the backend, and so does a locally running copy
  (`npm run dev` plus a local backend).
- Keep a pre-generated full book (e.g. `01_novel.mp3`) and two chapter files on the laptop to play
  directly.
- Keep screenshots (`docs/screenshots/`) and the README open in a tab.
- If the backend is down, show the connection banner. It shows the app handling failure honestly.
