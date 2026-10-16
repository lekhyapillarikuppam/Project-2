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
| D18 | Jobs are not restored after a restart; startup empties `storage/jobs/`. Unknown and expired jobs both show "no longer available" | Render free tier wipes disk and memory on sleep/redeploy anyway |
| D19 | The UI never promises 24 h; it says audio is temporary and to download what you want to keep. A free uptime monitor keeps the backend awake (Phase 5) | Honest about free-tier behaviour |
| D20 | Heavy work runs in one job worker thread with its own event loop; blocking steps via `asyncio.to_thread`; ffmpeg via `subprocess.run` only | Keeps the API responsive; avoids the Windows asyncio subprocess problem |
| D21 | Only chapters under 40 words are merged; short real sections stay as chapters. References/Bibliography/Index are dropped | Listening is fine with short chapters; nobody wants references read aloud |
| D22 | Sample audiobook is static files in `frontend/public/sample/`, served by Vercel | Plays instantly even when the backend is asleep |
| D23 | `react-router-dom` is an approved frontend dependency, with a `vercel.json` rewrite | Needed for `/book/:jobId` deep links |
| D24 | Python dependencies are pinned to exact versions (`==`) from Phase 0 onward | edge-tts and piper-tts change their interfaces between versions |
| D25 | A held-out test set (`testset_heldout/`) is never inspected during development; its first-run score is the honest number reported in the viva | Dev-set scores are inflated because the rules were tuned while looking at those files |

## Change log
- 2026-10-16 — Initial decisions recorded (Phase 0).
- 2026-10-16 — D18–D24 added after the Phase 0 review; SPEC revision 2.
- 2026-10-16 — D25 added and Phase 1 implementation rules recorded in SPEC §6.3.6 (revision 3).
- 2026-10-16 — Held-out first run (commit 1dbf9ac): F1 0.99 (78/79), rejects 4/4, noise 0. SPEC revision 4: compound-hyphen rule narrowed, bare-number title prefix stripped in evaluation.
