# Phase 3 — Backend API, job queue, cleanup (+ first real Render deploy)

Read first: `CLAUDE.md`, `docs/DECISIONS.md` (note **D18–D20, D26, D27**), `docs/SPEC.md` **revision 5**: §3, §4, §5 (threading model), §6.1, §6.7–6.8, §7.2, §8, §9, §10, §12, §14.
No frontend in this phase.

---

## 0. Spike wrap-up (do first)
1. Write `docs/spike_results.md` from these Render results (2026-10-17, free tier, Singapore) plus your local numbers from Phase 2b:
   - `/spike/memory`: cgroup v2, limit 512 MB, idle usage 147.9 MB, this process peak 93.2 MB.
   - `/spike/edge`: ok, 3,348 chars in 14.61 s = 229.2 chars/s, 203.7 s of audio, peak 97.7 MB.
   - `/spike/extract`: ok, 200 pages, 74,235 words, 35 chapters, 7.78 s (validate 1.38, extract 4.00, clean 2.00, structure 0.39), peak 103.4 MB.
   - Piper (low-noarena, medium-noarena, medium-arena): every run ended with Render event "Ran out of memory (used over 512MB)" and an instance restart (4 times). The two medium runs were started 4 s apart and overlapped, which made that crash worse, but the low-noarena run crashed alone.
   - Conclusion and recommendations = D26 / D27 and SPEC rev 5 §6.7, §6.8, §14. Include the estimate: 100,000 words ≈ 537,000 chars ≈ 39 min at 229 chars/s with one stream (concurrency 4 should do better; Phase 3 measures it).
2. Delete `backend/spike/` and its mount in `main.py`, and remove `spike_enabled` from config / `.env.example`. Keep the Dockerfile.
3. Dockerfile: add `ARG INCLUDE_PIPER=false`; download the Piper voices only when it is `true`. Render will build without them (smaller image, faster deploys).

## 1. TTS changes required by D26 / D27
- Config: `TTS_FALLBACK` (`piper` | `none`, default `piper`), `TTS_MAX_RETRIES` (default 3; Render will set 5), backoff 1, 2, 4, 8 s (cap at 8).
- `build_engine(settings)`: with `none`, return an engine that retries edge only. Keep the consecutive-failure counter: at `TTS_FALLBACK_AFTER_CONSECUTIVE_FAILURES` (5) failed chunks it raises a `NarrationUnavailable` signal instead of switching engines.
- Narrator: on `NarrationUnavailable`, mark the current and all remaining chapters `failed` (`TTS_FAILED`) and stop. A chapter with **any** failed chunk is `failed` and its partial audio is deleted (D27). Update `test_narrator.py` (remove the "chunk skipped with a warning" behaviour, add tests for both rules).
- `narrate.py --engine` gains `edge` (edge with retries, no fallback) if it doesn't already behave like that.

## 2. Files to create
```
backend/app/jobs/__init__.py
backend/app/jobs/models.py      (Job, ChapterState, JobStatus; to_public() → SPEC §7.2 JSON)
backend/app/jobs/registry.py    (thread-safe in-memory registry + job.json persistence)
backend/app/jobs/worker.py      (one worker thread, FIFO queue, runs a job end to end)
backend/app/jobs/cleanup.py     (startup wipe + periodic expiry)
backend/app/api/__init__.py
backend/app/api/errors.py       (ApiError, handlers → SPEC §9 error shape + status table)
backend/app/api/routes.py       (all /api endpoints)
backend/app/api/files.py        (download filename sanitising + Content-Disposition)
backend/scripts/smoke_api.py    (end-to-end check against a running server)
backend/tests/test_api.py
backend/tests/test_jobs.py
backend/tests/test_files.py
```
`main.py`: wire routers, error handlers, startup (wipe storage, start worker + cleanup), shutdown (stop them cleanly).

## 3. Behaviour (follow the spec; key points)
**Upload (`POST /api/upload`)**
- Reject on `Content-Length` > max before reading; stream the body to `storage/jobs/<id>/input.<ext>` in 1 MB pieces and abort with `FILE_TOO_LARGE` if it passes the max (§6.1 step 1). Missing `file` field → 400.
- Check the queue limit **before** heavy validation: if 1 job is running and `MAX_QUEUED_JOBS` (3) are waiting → `SERVER_BUSY` 503 (§4: the running job is not counted in the 3).
- Run `validate_file` (steps 2–7) off the event loop (`run_in_threadpool`). On error: delete the job folder, return the 4xx from the §9 table, **no job created**.
- On success: create the job (`queued`), enqueue, return `202 {"job_id", "status"}`.

**Worker (`jobs/worker.py`)** — exactly the §5 threading model:
- One daemon thread; inside it `asyncio.run(run_job(job))` per job.
- `extracting`: `run_pipeline` via `asyncio.to_thread`; on `PipelineError` (`TOO_MANY_WORDS`, `EMPTY_DOCUMENT`, …) → `failed` with that error.
- Write `document.json`, fill `chapters` (all `pending`), set `chars_total`.
- `synthesizing`: `narrate_document(..., on_progress=...)`. The progress callback updates the job under the registry lock: chapter `generating` / `ready` (with `duration_seconds`) / `failed`, `chars_done`, `chapters_done`, `engine_used`, and `eta_seconds` = remaining chars ÷ observed chars/s (null until the first chunk).
- End state: `done` (all ready), `partial` (≥ 1 ready, ≥ 1 failed), `failed` (none ready). `expires_at = created_at + RETENTION_HOURS`.
- Any unexpected exception → `failed` with a generic `TTS_FAILED`/internal message, logged with traceback; the worker keeps running for the next job.

**Registry**: dict guarded by `threading.Lock`; handlers only get deep-copied snapshots; `job.json` rewritten after every state change (debug only, never read back — D18).

**Cleanup**: on startup empty `storage/jobs/`. Every `CLEANUP_INTERVAL_MINUTES`: jobs past `expires_at` → delete folder (catch `PermissionError`, retry next run), status `expired`, keep the stub for 1 h, then remove it. Unknown id → `JOB_NOT_FOUND` 404; expired stub → `JOB_EXPIRED` 410.

**Read endpoints**
- `GET /api/jobs/{id}` → §7.2 JSON.
- `GET /api/jobs/{id}/chapters/{n}/audio` → `audio/mpeg`, **must support `Range`** (206 + `Content-Range`, `Accept-Ranges: bytes`). Check whether Starlette's `FileResponse` in the pinned version already handles Range; if yes use it, if not implement it. Test both a full request and `Range: bytes=1000-1999`. Not ready → `CHAPTER_NOT_READY` 409; `n` out of range → 404.
- `GET /api/jobs/{id}/download/{n}` → same file as attachment with the §9 filename rules.
- `GET /api/jobs/{id}/download/full` → only when `done`/`partial`, else 409. Build `full.mp3` lazily with `build_full_book` in a thread, guarded by a per-job lock so two requests never build it at once; cache it.
- `GET /api/health`, `GET /api/config` (add `retention_hours`, `max_words`, `accepted_types`, and `tts_fallback` so the UI can show it).

**CORS**: from `CORS_ORIGINS`; expose `Content-Disposition`, `Content-Range`, `Accept-Ranges`, `Content-Length`.

## 4. Tests (no network)
Inject a fake engine (e.g. `app.state.engine_factory`) that writes a valid MP3 from `make_silence`, with switches to fail chunks and to add delay.
- Upload happy path → job goes `queued → extracting → synthesizing → done`; chapters become `ready` in order; `chars_done` increases; ETA appears.
- Every upload rejection code from the §9 table with the right HTTP status, and no job folder left behind.
- `Content-Length` too large → 413 without reading the body.
- `SERVER_BUSY` when 1 running + 3 queued (use a slow fake engine).
- Audio: full GET, Range GET (206, correct bytes), not-ready 409.
- Download filenames: Windows-invalid characters, long titles, a non-ASCII title (`filename*`).
- Full book: 409 before done; built once under two concurrent requests.
- D26/D27: chunk failure fails the chapter; 5 consecutive failures → remaining chapters failed, job `partial`/`failed`.
- Expiry: with a tiny retention, the folder is deleted, status `expired`, endpoint returns 410, stub later 404.
- Worker survives an exception in one job and runs the next.

## 5. Acceptance (local)
1. `pytest -q` — all pass, no warnings.
2. Start the server (`uvicorn app.main:app --port 8000`, **no** `--reload`, `TTS_FALLBACK=none`).
3. `python scripts\smoke_api.py --base http://localhost:8000 --file ..\testset\docs\06_no_styles.docx` must: upload, poll every 3 s printing status/progress/ETA, start downloading chapter 1 audio **as soon as it is ready while later chapters are still generating** (prove early playback), wait for `done`, download full book, check `ffprobe` durations, and print a summary (total time, chars/s with concurrency 4).
4. `smoke_api.py --expect-reject` for `08_scanned.pdf`, `09_legacy.doc`, `10_fake.pdf` (and `testset_heldout/docs/H06_encrypted.pdf` + `H07_corrupt.pdf` are allowed here — rejection only, no inspection) prints the error code + status for each.
5. While a job is synthesizing, call `/api/health` 20 times and report the max latency (must stay < 200 ms — proves the worker doesn't block the API).

Commit `Phase 3: API, job queue, cleanup, edge-only mode (D26/D27)` and push.

## 6. First real deploy to Render (stop and hand over)
Print exact steps for the user:
1. Render → New → Web Service → repo `Project-2`, name **`audiobook-api`**, Docker, root `backend`, Free, Singapore.
2. Env vars: `TTS_FALLBACK=none`, `TTS_MAX_RETRIES=5`, `TTS_CONCURRENCY=4`, `CORS_ORIGINS=http://localhost:5173` (Vercel URL added in Phase 5), `LOG_LEVEL=INFO`.
3. Wait for Live, open `/api/health`.
4. Then the user runs on their laptop:
   `python scripts\smoke_api.py --base https://audiobook-api.onrender.com --file ..\testset\docs\06_no_styles.docx`
   and then the same with `..\testset\docs\01_novel.pdf`, and pastes both outputs back to you.
5. While the Alice job runs, the user watches Render → Metrics → Memory and notes the peak.

When the user pastes the results: append a "Phase 3 Render run" section to `docs/spike_results.md` (time, chars/s with concurrency 4, peak memory, any edge errors/retries) with a projected time for a 100,000-word book, commit, push, report.

Do **not** delete `audiobook-api` — it is the production backend from now on. Do not start Phase 4.
