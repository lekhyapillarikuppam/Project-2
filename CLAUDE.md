# CLAUDE.md — instructions for Claude Code in this repo

## Project
AI-Powered Audiobook Generator (UMC301 Project 2). Converts PDF/DOCX into chaptered MP3 audiobooks.
- Full spec: `docs/SPEC.md` — read the relevant section before implementing anything.
- Locked decisions: `docs/DECISIONS.md` — never contradict these. If a task seems to require it, stop and ask.

## How we work
- Work happens in phases (0–6). Only implement the phase you were asked for. Do not start the next phase or "get ahead".
- Do not add features, dependencies, or files that are not in the spec or the phase prompt. If you think something is missing, say so at the end instead of adding it.
- After finishing a phase task, report: files created/changed, commands to run, and anything that did not work.

## Environment
- Windows 11, VS Code, **PowerShell** (not WSL, not bash). Give commands in PowerShell syntax.
- Python 3.11 or 3.12 in `backend/.venv`. Activate with `backend\.venv\Scripts\Activate.ps1`.
- Node 20+ for the frontend.
- ffmpeg and ffprobe must be on PATH.

## Commands
- Backend dev server: `cd backend; uvicorn app.main:app --reload --port 8000`
- Environment check: `cd backend; python scripts/check_env.py`
- Tests: `cd backend; pytest -q`
- Frontend dev server: `cd frontend; npm run dev`
- Frontend against the Azure API: `cd frontend; npm run dev:remote`
- Frontend tests / build: `cd frontend; npm test`, `npm run build`
- Chapter-detection evaluation: `cd backend; python scripts\evaluate.py` (held-out: `--testset ..\testset_heldout --history`)

## Code conventions
- Python: type hints everywhere, small pure functions in `pipeline/` and `tts/` (easy to unit test), no global mutable state outside `jobs/`.
- All limits and paths come from `app/config.py` (env-driven). Never hard-code them.
- Never hold a whole book's audio in memory; stream to files in the job folder.
- Use `pathlib.Path`, never string-joined paths (Windows + Linux deploy).
- Error responses use the shape and codes in SPEC §9 and §12.
- Frontend: React function components + hooks, plain CSS modules or a single CSS file; no UI framework unless the phase prompt says so.

## Don'ts
- Don't commit anything in `backend/storage/`, `testset/docs/`, `.env`, or voice model files.
- Don't call paid APIs. Don't add OCR.
- Don't use `print` for logging in app code; use the `logging` module.
