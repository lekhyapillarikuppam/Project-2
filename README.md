# AI-Powered Audiobook Generator

Upload a PDF or DOCX and get a chaptered audiobook you can play in the browser (with speed control) or download chapter-by-chapter or as one MP3.

UMC301 Applied Data Science and Artificial Intelligence — Project 2.

> Status: **Phase 0 — spec and setup.**

## Live demo
_Coming in Phase 5._

## Features (v1)
- PDF and DOCX up to 200 pages
- Automatic cleanup: removes page numbers, running headers/footers, broken hyphenation; skips tables, captions, footnotes, equations
- Chapter/section detection (built-in outline → heading styles → typography → text patterns → fallback)
- Neural narration (edge-tts, with local Piper fallback)
- Chapter-by-chapter playback while the rest is still generating
- 0.5×–2× speed, chapter navigation, auto-advance
- Download any chapter or the full book
- Audio auto-deletes after 24 hours

## Tech stack
| Part | Tech |
|---|---|
| Backend | Python, FastAPI, PyMuPDF, python-docx, edge-tts, Piper, ffmpeg |
| Frontend | React + Vite |
| Hosting | Render (backend), Vercel (frontend) |

## Local setup (Windows, PowerShell)
```powershell
# backend
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts\check_env.py
uvicorn app.main:app --reload --port 8000

# frontend (new terminal)
cd frontend
npm install
npm run dev
```

## Documentation
- [Specification](docs/SPEC.md)
- [Decisions](docs/DECISIONS.md)

## Evaluation
_Chapter-detection results will be added in Phase 1._
