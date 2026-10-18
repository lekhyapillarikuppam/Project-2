# Phase 4 — Frontend (React + Vite)

Read first: `CLAUDE.md`, `docs/DECISIONS.md` (D12, D13, D19, D22, D23, **D27, D29**), `docs/SPEC.md` revision 6: §3 user flow, §7.2 job object, §9 API + error shape, §11 frontend, §12 error messages.
Backend is live at `https://lekhya-audiobook.centralindia.cloudapp.azure.com` (CORS already allows `http://localhost:5173`). Do **not** change backend behaviour; if the UI needs something the API lacks, stop and tell me.

---

## 0. Setup
- `frontend/` already has a Vite React starter. Replace the starter content entirely (delete `hero.png`, `icons.svg`, starter CSS, etc.).
- Dependencies: `react-router-dom` (D23). Dev: `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `jsdom` (D29). Nothing else — no UI kit, no state library, no axios (use `fetch`). Pin exact versions in `package.json`.
- Env: `frontend/.env.development` → `VITE_API_URL=http://localhost:8000`; `frontend/.env.production` → `VITE_API_URL=https://lekhya-audiobook.centralindia.cloudapp.azure.com`. Add `npm run dev:remote` that runs the dev server against the Azure API (`vite --mode remote` with `.env.remote`).
- `frontend/vercel.json`: rewrite all paths to `/index.html` (D23).

## 1. Structure
```
frontend/src/
  main.jsx, App.jsx               (router: /, /book/:jobId, /sample, * → not found)
  api/client.js                   (fetch wrapper: base URL, JSON, error shape → ApiError{code,message,status}, retry/backoff for GETs)
  api/source.js                   (data sources for the book view: apiSource(jobId) and sampleSource())
  hooks/useJobPolling.js          (poll every 3 s while queued/extracting/synthesizing; retry 1/2/4/8 s up to 2 min on network errors; stop when finished; expose {job, error, connection})
  hooks/useAudioPlayer.js         (one <audio> element; current chapter, play/pause, seek, rate, prev/next, auto-advance, waiting state)
  lib/storage.js                  (localStorage get/set wrapped in try/catch; key per job id)
  lib/format.js                   (durations h:mm:ss, ETA "about 4 min", file sizes)
  lib/errors.js                   (code → message map from SPEC §12, with a generic fallback)
  pages/UploadPage.jsx
  pages/BookPage.jsx              (used by /book/:jobId and /sample with a different source)
  pages/NotFoundPage.jsx
  components/Dropzone.jsx, ProgressPanel.jsx, ChapterList.jsx, ChapterRow.jsx, PlayerBar.jsx, SpeedControl.jsx, Notice.jsx, ConnectionBanner.jsx
  styles/ (tokens.css, base.css, plus one CSS file per component, or CSS modules — your choice, be consistent)
```

## 2. Behaviour (SPEC §3 and §11 are the source of truth)
**Upload page `/`**
- Title, one-sentence explanation, drag-and-drop zone that is also a button (keyboard: Enter/Space opens the picker), "Try a sample" link to `/sample`.
- Limits shown from `GET /api/config` (fallback to 50 MB / 200 pages / PDF, DOCX if it fails).
- Client checks before upload: extension `.pdf`/`.docx` (`.doc` → the LEGACY_DOC message), size ≤ max.
- Upload with `XMLHttpRequest` so an upload progress bar is shown (fetch has no upload progress). On 202 → navigate to `/book/{job_id}`. On error → show the SPEC §12 message for the code inline, keep the user on the page.
- "How it works" in three short steps (upload → we find chapters → listen or download).

**Book page `/book/:jobId`**
- Header: book name (upload filename without extension), overall status.
- Progress panel while working: status text ("Reading your document…", "Finding chapters…", "Narrating chapter 3 of 12…"), progress bar from `chars_done/chars_total`, ETA from `eta_seconds`, an `aria-live="polite"` region for status changes (not for every percent).
- Chapter list: index, title, duration (when ready), status chip (waiting / narrating / ready / failed), Play button (enabled only when ready), Download button (only when ready). Failed chapters show "Narration failed for this chapter" (D27) and stay visible.
- Warnings from the job (e.g. "2 image-only pages were skipped") shown as a quiet notice.
- "Download full book" button when status is `done` or `partial` (with "(N chapters failed)" if partial). Downloads are plain links to the API download URLs (the server sends `Content-Disposition`).
- Temporary-audio notice (D19): "Audio is kept for a limited time — download anything you want to keep." Never promise an exact time.
- `JOB_NOT_FOUND` / `JOB_EXPIRED` → full-page message "This audiobook is no longer available (audio is temporary). Please upload the document again." + button to `/`.
- `failed` job → show the job's error message + upload-again button.
- Polling stops when finished; when the tab becomes visible again after being hidden, poll once immediately.

**Player (sticky bottom bar)**
- Shows current chapter title, play/pause, −15 s / +15 s, seek bar with current/total time, previous/next chapter, speed 0.5×, 0.75×, 1×, 1.25×, 1.5×, 1.75×, 2× (`playbackRate`, pitch preserved — set `preservesPitch`), keep the chosen speed across chapters.
- Audio source = `GET /api/jobs/{id}/chapters/{n}/audio` (Range supported, so seeking works).
- Auto-advance to the next **ready** chapter; if the next chapter isn't ready yet, show "Waiting for chapter N…" and start it automatically when it becomes ready; skip failed chapters.
- Remember last chapter + position + speed per job in localStorage (lib/storage.js; save at most every 5 s and on pause). On return, offer "Resume chapter N at m:ss".
- Keyboard: Space = play/pause (when focus isn't in an input/button), ←/→ = −15/+15 s, Shift+←/→ = previous/next chapter. Show these in a small "?" tooltip.
- Media Session API (title/chapter on lock screen, next/previous handlers) if available.

**Sample `/sample`**
- Same BookPage with `sampleSource()`: reads `public/sample/job.json` (a §7.2 job object with status `done`) and plays `public/sample/ch_00N.mp3`; downloads link to the same static files. Never calls the API, so it works even if the backend is down.
- Generate the sample yourself: run the backend pipeline + `narrate.py` (edge) on the **first 3 stories of `testset/docs/06_no_styles.docx`** (public domain), copy the 3 chapter MP3s and a full-book MP3 into `frontend/public/sample/`, write `job.json` to match. Keep the total under 10 MB. Commit these files (the `.gitignore` exception already exists). Add `scripts/make_sample.py` in backend so it can be regenerated.

**Connection banner**: if `/api/health` fails or takes > 5 s on load, or polling enters retry mode, show "Can't reach the server right now. Retrying…" and hide it when a request succeeds.

## 3. Design direction
A calm "reading room" feel, not a generic SaaS dashboard:
- Warm off-white paper background, deep ink text, one accent colour (e.g. a muted teal or deep amber) used only for primary actions and the progress/seek bars. Full dark mode via `prefers-color-scheme` (all colours as CSS custom properties in `tokens.css`).
- Typography: a serif for headings and book titles (Google Fonts, e.g. Literata or Fraunces, loaded in `index.html` with `display=swap`), a clean sans for UI text. Generous line height, max content width ~760 px, centred.
- Chapter list reads like a table of contents (title left, duration right, dotted leader optional).
- Clear focus rings, ≥ 44 px touch targets, WCAG AA contrast in both themes, `prefers-reduced-motion` respected.
- Works from 360 px wide: the player bar collapses to two rows on mobile; nothing scrolls horizontally.
- App name in the header: **"Readaloud"** (single constant, easy to change), with tagline "Turn documents into audiobooks".

## 4. Tests (Vitest + Testing Library, D29)
- `client.js`: error shape parsing; GET retries on network failure then succeeds; gives up after the limit.
- `useJobPolling`: polls while running, stops when done, enters/leaves retry mode (fake timers, mocked fetch).
- `UploadPage`: rejects `.doc`, `.txt`, too-large files with the right messages; shows the server error message for a 422 `SCANNED_PDF`.
- `BookPage` with a fake source: renders chapters with correct statuses; play disabled for pending; full-book button only when done/partial; not-available screen on 404/410; failed chapter text.
- `useAudioPlayer`: auto-advance skips failed chapters and waits for pending ones; speed persists across chapters.
- `npm test` must pass with no warnings; `npm run build` must succeed with no warnings.

## 5. Acceptance
1. `npm test`, `npm run build`.
2. Run the backend locally (`uvicorn app.main:app --port 8000`, `TTS_FALLBACK=none`) and `npm run dev`; upload `testset/docs/06_no_styles.docx` and walk through: progress, early play of chapter 1, auto-advance, speed change, seek, download one chapter, full-book download, reload the page mid-job (polling resumes), resume prompt after reload.
3. `npm run dev:remote` against Azure: upload `01_novel.pdf`, confirm the same.
4. Error paths: upload `08_scanned.pdf`, `09_legacy.doc`, `10_fake.pdf` → correct messages; open `/book/does-not-exist` → not-available screen; stop the local backend mid-job → connection banner appears, restart → it recovers (the job will be gone after a restart, so the not-available screen is correct).
5. `/sample` works with the backend stopped.
6. Check 360 px and 1280 px widths, light and dark mode, keyboard-only use (Tab through upload and player). If you can take screenshots, save them to `docs/screenshots/` (desktop light, desktop dark, mobile) and commit them.
7. Lighthouse (if available) accessibility score ≥ 95 on `/` and `/sample`.

## 6. Report
Files created; test + build output; what you verified in steps 2–7 (and anything you could not verify); any API gaps found. Commit `Phase 4: frontend (upload, progress, player, sample)` and push. Do not deploy to Vercel (that is Phase 5).
