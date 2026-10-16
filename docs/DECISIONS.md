# Decisions Log — Audiobook Generator (Project 2)

Locked decisions. Changing any of these needs an explicit new entry below, not a silent edit.

| # | Decision | Reason |
|---|---|---|
| D1 | No user accounts, no login, no saved library | Scope: focus on conversion quality |
| D2 | Free tools and free hosting only; no paid APIs | Course constraint |
| D3 | English only | Scope for v1 and v2 |
| D4 | Accepted inputs: `.pdf` and `.docx` only. Legacy `.doc` and every other format rejected with a clear message | Reliable extraction |
| D5 | Scanned / image-only PDFs rejected (no OCR) | OCR is slow, error-prone, and out of scope |
| D6 | Max 200 PDF pages; max 100,000 words for any document; max 50 MB file | Keeps generation time and disk use bounded on free hosting |
| D7 | Audio is temporary: deleted 24 h after creation | No paid storage; Render disk is ephemeral anyway |
| D8 | Tables, figure/table captions, footnotes and equations are skipped, not read aloud | They don't make sense spoken |
| D9 | TTS: `edge-tts` primary, Piper (local) fallback, behind one `TTSEngine` interface | Best free quality + resilience if the unofficial edge endpoint fails |
| D10 | v1: one consistent narrator voice. v2: distinct voices per character (LLM-based speaker attribution). No intermediate version | Agreed scope |
| D11 | Data model carries a `speaker` field on every segment from v1 (always `"narrator"`) | v2 needs no data-model change |
| D12 | Speed control is client-side (`playbackRate`), no regeneration | Instant, free |
| D13 | Chapters are generated in order and playable as soon as each is ready | 200-page books can take 20–40 min |
| D14 | Stack: FastAPI (Python) backend on Render, React + Vite frontend on Vercel | Same as Project 1 where possible |
| D15 | Development on Windows + VS Code (PowerShell), repo at `C:\dev\Project2` | User's setup |
| D16 | Chapter detection is evaluated against a hand-labeled test set (precision / recall / F1) | Measurable result for the viva |
| D17 | Started fresh on 2026-10-16; nothing reused from the earlier Project 2 attempt | User decision |

## Change log
- 2026-10-16 — Initial decisions recorded (Phase 0).
