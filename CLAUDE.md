# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
AI-Powered Audiobook Generator ("Readaloud", UMC301 Project 2). Converts PDF/DOCX into chaptered MP3 audiobooks; v2 adds optional per-character voices.
- Full spec: `docs/SPEC.md` — read the relevant section before implementing anything (§15 covers v2 character voices).
- Locked decisions: `docs/DECISIONS.md` (D1–D42) — never contradict these. If a task seems to require it, stop and ask.
- Phase prompts live in `docs/prompts/phaseN.md`; measured results in `docs/spike_results.md` and the `testset*/results*.md` files.

## How we work
- Work happens in phases (0–6). Only implement the phase you were asked for. Do not start the next phase or "get ahead".
- Do not add features, dependencies, or files that are not in the spec or the phase prompt. If you think something is missing, say so at the end instead of adding it.
- After finishing a phase task, report: files created/changed, commands to run, and anything that did not work.

## Environment
- Windows 11, VS Code, **PowerShell** (not WSL, not bash). Give commands in PowerShell syntax.
- Python 3.11 or 3.12 in `backend/.venv`. Activate with `backend\.venv\Scripts\Activate.ps1`.
- Node 20+ for the frontend.
- ffmpeg and ffprobe must be on PATH (tests that need them skip without them).
- Character voices need `GROQ_API_KEY` in `backend\.env` (copy from `.env.example`).

## Commands
- Backend dev server: `cd backend; uvicorn app.main:app --reload --port 8000`
- Environment check: `cd backend; python scripts/check_env.py`
- Tests: `cd backend; pytest -q`
- Single test: `cd backend; pytest -q tests/test_structure.py::test_name` (or `-k pattern`)
- Frontend dev server: `cd frontend; npm run dev`
- Frontend against the Azure API: `cd frontend; npm run dev:remote`
- Frontend tests / build / lint: `cd frontend; npm test`, `npm run build`, `npm run lint` (oxlint)
- Single frontend test file: `cd frontend; npx vitest run src/test/BookPage.test.jsx`
- Chapter-detection evaluation: `cd backend; python scripts\evaluate.py` (held-out: `--testset ..\testset_heldout --history`)
- Dialogue/speaker evaluation: `cd backend; python scripts\evaluate_dialogue.py --methods all --models qwen/qwen3.8-27b,openai/gpt-oss-120b` (Groq answers cached in `storage/llm_cache`, so re-scoring is free)
- Debug one stage by hand: `scripts\extract.py` (pipeline only → document JSON), `scripts\narrate.py` (TTS only), `scripts\smoke_api.py` (end-to-end against a running server)
- Deploy: backend via `deploy/README.md` (`bash deploy.sh` on the Azure VM); frontend on Vercel (root `frontend`, `VITE_API_URL`).

## Architecture
**Request flow.** `app/main.py:create_app()` builds the FastAPI app and stores everything shared on `app.state` (settings, `JobRegistry`, engine factory, LLM factory, upload rate limiter). The lifespan hook wipes `storage/jobs/` (D18), then starts one `JobWorker` thread and one `CleanupThread`. `POST /api/jobs` (in `app/api/routes.py`) saves the upload, registers a job, and hands the id to the worker; the frontend polls the job and streams chapter MP3s with HTTP Range (`app/api/files.py`).

**Threading model (D20).** The worker is one daemon thread with a FIFO queue; each job runs end to end inside its own `asyncio.run`. Blocking steps go through `asyncio.to_thread`; ffmpeg is called only via `subprocess.run` (Windows asyncio subprocess issues). Job state lives only in `app/jobs/registry.py` and is changed via `registry.update(job_id, change_fn)` under its lock, which also writes `job.json`. This is the only place mutable global state is allowed.

**Pipeline (`app/pipeline/`, entry `run_pipeline`).** validate → extract (PyMuPDF for PDF, python-docx for DOCX) → clean → structure detection (outline → Word styles → typography → text patterns → fallback) → `DocumentModel` (`app/models.py`, SPEC §7.1). Failures raise `PipelineError` with a SPEC §12 code; `app/api/errors.py` turns those into the §9 error shape.

**TTS (`app/tts/`).** `normalize` (text for speech) → `chunk` (sentences, ≤ `tts_chunk_max_chars`) → `engines` (`TTSEngine` interface: edge-tts primary, Piper local-only fallback; deployed with `TTS_FALLBACK=none`, D26) → `narrator` (chunks in parallel, `tts_concurrency`), then `audio.py` concatenates with ffmpeg without re-encoding. Chapters are narrated strictly in order, and each is playable as soon as it is assembled (D13). A chunk that fails on every engine fails its whole chapter (D27). The job status is then `done` / `partial` / `failed`.

**Character voices (`app/dialogue/`, only when `narration_mode=characters`).** `segment` finds quotes/utterances → `attribute` assigns speakers (Groq LLM via `llm.py` over `httpx`, in `windows.py` word windows, with a rule-based fallback; the LLM never fails a job, D34) → `cast` merges aliases → `voices` casts lazily per chapter from `voice_pool.json` (D41). `CharactersMode` in `app/jobs/worker.py` attributes chapter N+1 while chapter N is narrated (D37), and paces itself on input and output token budgets (D42). Every segment carries a `speaker` field (`"narrator"` in v1 mode, D11).

**Testing seams.** `create_app(settings, engine_factory, llm_factory)` is how tests inject fakes. `tests/conftest.py` provides `GateEngine` (a fake TTS that copies a real MP3 clip, with optional gate/failure/delay) and an ffmpeg-generated silent clip. Tests never call edge-tts or Groq. `tests/test_testset.py` skips when `testset/docs/` is absent (the documents are not committed).

**Frontend (`frontend/src/`).** React 19 + react-router-dom (`/`, `/book/:jobId`, `/sample`, `/sample/characters`). `api/source.js` gives the live API job and the static sample in `public/sample/` (D22) the same `BookSource` shape, so `BookPage` doesn't care which it gets. Polling is in `hooks/useJobPolling.js` and playback in `hooks/useAudioPlayer.js` (speed is client-side `playbackRate`, D12). The API base URL comes from `VITE_API_URL` (`.env.development` / `.env.remote` / `.env.production`).

**Evaluation honesty (D25, D35, D39).** Never open or tune against `testset_heldout/` or the held-out dialogue chapter; their first-run numbers are the reported results, and later runs are appended to `results_history.md`.

## Code conventions
- Python: type hints everywhere, small pure functions in `pipeline/` and `tts/` (easy to unit test), no global mutable state outside `jobs/`.
- All limits and paths come from `app/config.py` (env-driven, pydantic-settings reading `backend/.env`). Never hard-code them.
- Python dependencies are pinned with `==` (D24).
- Never hold a whole book's audio in memory; stream to files in the job folder.
- Use `pathlib.Path`, never string-joined paths (Windows + Linux deploy).
- Error responses use the shape and codes in SPEC §9 and §12.
- Frontend: React function components + hooks, plain CSS files in `src/styles/` (tokens in `tokens.css`); no UI framework unless the phase prompt says so.

## Don'ts
- Don't commit anything in `backend/storage/`, `testset/docs/`, `.env`, or voice model files.
- Don't call paid APIs. Don't add OCR.
- Don't use `print` for logging in app code; use the `logging` module.
