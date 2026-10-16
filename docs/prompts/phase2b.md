# Phase 2b — Render deployment check (spike)

Read `CLAUDE.md`, `docs/DECISIONS.md`, `docs/SPEC.md` §5, §6.7, §14 (the "Early deployment check" bullet).
Goal: find out, **on Render's free tier**, (a) whether edge-tts works from Render's network, (b) whether Piper can run inside 512 MB and how fast, (c) peak memory of the Phase 1 pipeline on a 200-page PDF. These numbers decide the fallback design before Phase 3.

This is a spike. Keep it small, keep it separate from app code, and make everything removable. The Dockerfile is the exception: it should be good enough to reuse in Phase 5.

---

## 1. Piper memory controls (app code, tested locally first)
Phase 2 measured Piper at ~1 GB peak. Add three settings to `config.py` / `.env.example` and support them in `PiperEngine`:
- `PIPER_DISABLE_ARENA` (bool, default `true`): build the onnxruntime session with the CPU memory arena disabled (and memory pattern off). Find how piper-tts 1.8.0 lets you pass session options; if it doesn't, load the model with your own `onnxruntime.InferenceSession` the way piper does internally — report what you did.
- `PIPER_MAX_CHARS` (int, default `300`): before synthesizing with Piper, split the chunk text at sentence / clause boundaries into pieces of at most this many characters, synthesize each, and join the WAVs before the MP3 encode. Peak memory grows with input length, so this bounds it.
- `PIPER_MODEL_PATH` already exists. Also download `en_US-lessac-low` into `backend\models\` so both can be compared.

Locally, measure peak memory and chars/second for the 4 combinations {medium, low} × {arena on, arena off} with `PIPER_MAX_CHARS=300`, using `narrate.py --engine piper --chapters 1` on `01_novel.pdf`. Run each combination in a fresh process. Report a table. Add unit tests for the text splitting.

## 2. Spike code (removable)
Create `backend/spike/` (package) containing:
- `run.py` — a CLI with subcommands, each designed to run in its **own process** so its peak memory is isolated:
  - `edge` — synthesize the first ~3,000 characters of `spike/assets/alice_ch1.txt` with edge-tts using the real `EdgeTTSEngine` and chunker; print JSON `{ok, error, chars, seconds, chars_per_sec, peak_mb}`.
  - `piper --model <path> [--arena on|off] [--max-chars N]` — same text with `PiperEngine`; same JSON.
  - `extract --pages 200` — build a ~200-page PDF on the fly with pymupdf from the Alice text repeated (A5, 11 pt, so it is realistic), then run `run_pipeline` on it; print JSON `{ok, pages, words, chapters, seconds, peak_mb}`.
  - `memory` — print the container memory limit if readable (`/sys/fs/cgroup/memory.max` or `memory/memory.limit_in_bytes`) and current usage.
  Peak memory on Linux: `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` (KB).
- `spike/assets/alice_ch1.txt` — chapter 1 of Alice as plain text (public domain; take it from your `testset/output/01_novel.txt` dump).
- `spike/router.py` — a FastAPI router mounted **only when** env `SPIKE_ENABLED=true`, with `GET /spike/{name}` that runs `python -m spike.run <name> ...` as a subprocess (timeout 600 s), and returns its JSON plus exit code and the last 2,000 characters of stderr. Allowed names and their arguments are a fixed whitelist (no arbitrary arguments from the query string except picking one of the predefined Piper variants: `piper-medium-arena`, `piper-medium-noarena`, `piper-low-noarena`). If the subprocess is killed (e.g. exit code -9 / 137), report `"killed": true` — that's the out-of-memory signal.
- Mount the router in `main.py` behind the env check. Add `SPIKE_ENABLED=false` to `.env.example`.

## 3. Dockerfile (reused in Phase 5)
`backend/Dockerfile`:
- `python:3.11-slim` (match your local 3.11), install `ffmpeg` with apt, `pip install -r requirements.txt` (no cache).
- Download the two Piper voices **at build time** into `/app/models/` with the official `python -m piper.download_voices` command.
- Copy the app, run as a non-root user, `CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}` with **one** worker.
- Add `backend/.dockerignore` (`.venv`, `storage`, `models`, `tests`, `__pycache__`, `*.mp3`).
- If Docker Desktop is installed locally, build and run it once and hit `/api/health` and `/spike/memory`. If it isn't installed, say so and skip — Render will build it.

Run `pytest -q`, commit `Phase 2b: Piper memory controls, Render spike, Dockerfile`, push.

## 4. Stop and hand over to the user
Print these exact steps for the user (they do them in the Render dashboard; you cannot):
1. Render → **New → Web Service** → connect the GitHub repo.
2. Name `audiobook-spike`, **Language: Docker**, **Root directory: `backend`**, Dockerfile path `./Dockerfile`, **Instance type: Free**, region Singapore.
3. Environment variables: `SPIKE_ENABLED=true`, `PIPER_MODEL_PATH=/app/models/en_US-lessac-medium.onnx` (plus anything else your code needs).
4. Deploy, wait for "Live", then open in a browser, **one at a time**, waiting for each to finish:
   - `https://<service>.onrender.com/api/health`
   - `/spike/memory`
   - `/spike/edge`
   - `/spike/piper-low-noarena`
   - `/spike/piper-medium-noarena`
   - `/spike/piper-medium-arena`
   - `/spike/extract`
5. Copy each JSON response into a text file and give it back to you. If a page shows a Render error or the service restarts, note which URL caused it.

Then wait for the user to paste the results.

## 5. After the user pastes the results
Write `docs/spike_results.md`: a table of every measurement (Render vs local), and a recommendation for each:
- Is edge-tts usable from Render? (If it fails: what error.)
- Which Piper variant, if any, fits under ~450 MB total (leave headroom for the app), and its chars/second on Render → the estimated Piper-only time for a 100,000-word book.
- Peak memory of extracting a 200-page PDF.
- Recommended values for `PIPER_MAX_CHARS`, arena, model, `TTS_CONCURRENCY`, and whether the 200-page / 100,000-word limits in SPEC §4 need lowering.
Do **not** change the spec or the limits yourself. Commit `docs/spike_results.md`, then tell the user to **delete the `audiobook-spike` service on Render** (free accounts are limited) and report.
