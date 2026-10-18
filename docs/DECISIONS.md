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
| D14 | Stack: FastAPI (Python) backend, React + Vite frontend on Vercel. Backend host: see D28 (was Render) | Same as Project 1 where possible |
| D15 | Development on Windows + VS Code (PowerShell), repo at `C:\dev\Project2` | User's setup |
| D16 | Chapter detection is evaluated against a hand-labeled test set (precision / recall / F1) | Measurable result for the viva |
| D17 | Started fresh on 2026-10-16; nothing reused from the earlier Project 2 attempt | User decision |
| D18 | Jobs are not restored after a restart; startup empties `storage/jobs/`. Unknown and expired jobs both show "no longer available" | Render free tier wipes disk and memory on sleep/redeploy anyway |
| D19 | The UI never promises 24 h; it says audio is temporary and to download what you want to keep. (The uptime monitor is no longer needed on the always-on Azure VM.) | A redeploy still empties job storage |
| D20 | Heavy work runs in one job worker thread with its own event loop; blocking steps via `asyncio.to_thread`; ffmpeg via `subprocess.run` only | Keeps the API responsive; avoids the Windows asyncio subprocess problem |
| D21 | Only chapters under 40 words are merged; short real sections stay as chapters. References/Bibliography/Index are dropped | Listening is fine with short chapters; nobody wants references read aloud |
| D22 | Sample audiobook is static files in `frontend/public/sample/`, served by Vercel | Plays instantly even when the backend is asleep |
| D23 | `react-router-dom` is an approved frontend dependency, with a `vercel.json` rewrite | Needed for `/book/:jobId` deep links |
| D24 | Python dependencies are pinned to exact versions (`==`) from Phase 0 onward | edge-tts and piper-tts change their interfaces between versions |
| D26 | The deployed server uses edge-tts only (`TTS_FALLBACK=none`, 5 patient retries). Piper stays a local-only engine and is not in the deployed image | Render: every Piper variant exceeded 512 MB. Azure: edge-tts had 0 retries over two books, while Piper would need ~700 of the VM's 837 MB; D27 already makes edge outages visible. Re-test optional, not required |
| D27 | A chunk that fails on every available engine fails its whole chapter; no silent gaps in audio | A listener must never miss text without being told |
| D28 | Backend moves from Render free to an **Azure for Students B2ats v2 VM** (2 vCPU, 1 GiB, always on) running the Docker image behind Caddy (HTTPS). Render services for Project 2 are deleted after Azure passes its smoke tests | Render free: 0.15 CPU (172 chars/s) and sleeps after 15 min; the free Azure VM is ~10× the CPU, always on, so retention really holds |
| D29 | Frontend dev dependencies approved: `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `jsdom`. No UI component library; plain CSS | Component tests for the player and polling logic without a heavy framework |
| D30 | Uploads are limited to 10 per client IP per hour (in memory, reset on restart) | The site is public and the VM runs on student credit; the job queue limits load but not one person repeatedly uploading |
| D31 | v2 character voices is an opt-in mode chosen at upload (`narration_mode=characters`); single narrator stays the default and v1 behaviour is unchanged | User choice; non-fiction gains nothing from it |
| D32 | Speaker attribution LLM: Groq free tier via its OpenAI-compatible API over `httpx`; key only on the server. The model is chosen by the Phase 6.2 dev-set comparison among models Groq currently offers (initially `qwen/qwen3.8-27b` vs `openai/gpt-oss-120b`) | Llama 3.3 70B is no longer offered on the key; free, no training on free-tier data; no new SDK; the comparison is itself a result |
| D33 | Unknown or uncertain speakers are read in the narrator voice | A wrong character voice is worse than the neutral narrator |
| D34 | If the LLM fails, is rate-limited or out of daily budget, the affected windows use the rule-based baseline; the job never fails because of the LLM, and the chapter shows which method was used | Robustness on a free tier |
| D35 | Attribution is evaluated on 2 dev chapters (Alice ch. 7, "The Absence of Mr Glass") and 1 held-out chapter (*The Ball and the Cross* ch. 3), hand-labelled by the user; baseline vs LLM vs combined; held-out first run reported | Measurable NLP result; same honesty rule as D25 |
| D36 | Character-voices mode sends the document text to Groq; this is stated on the upload page and in the viva notes | Privacy disclosure |
| D37 | Attribution runs per chapter, one chapter ahead of narration, with a token-based rate limiter; a local baseline pre-pass ranks characters so voices are fixed before narration starts | Groq's 8,000 tokens/min allows only ~2–4 windows/min; overlapping with narration hides most of the wait |
| D38 | Labelling conventions: quoted thoughts belong to the thinker; group speech is UNKNOWN; quoted writing not spoken aloud is not an utterance; spans include quote marks; paragraphs 0-based in label files | Consistent gold labels across the three chapters |
| D25 | A held-out test set (`testset_heldout/`) is never inspected during development; its first-run score is the honest number reported in the viva | Dev-set scores are inflated because the rules were tuned while looking at those files |

## Change log
- 2026-10-16 — Initial decisions recorded (Phase 0).
- 2026-10-16 — D18–D24 added after the Phase 0 review; SPEC revision 2.
- 2026-10-16 — D25 added and Phase 1 implementation rules recorded in SPEC §6.3.6 (revision 3).
- 2026-10-17 — Render spike done; D26, D27 added; SPEC revision 5.
- 2026-10-17 — D28: backend moves to an Azure VM; D14, D19, D26 updated; SPEC revision 6.
- 2026-10-18 — Azure VM verified (600–720 chars/s, 131 MB peak, 0 retries). D26 finalised (no Piper on the server); D29 added. Render service deleted.
- 2026-10-18 — Phase 4 frontend done. D30 (per-IP upload limit) added; SPEC revision 7.
- 2026-10-18 — v1 live (Vercel + Azure). v2 planned: D31–D36; SPEC revision 8 (§15).
- 2026-10-18 — Phase 6.0 review: D32 revised (model chosen by evaluation), D37, D38 added; SPEC revision 9.
- 2026-10-16 — Held-out first run (commit 1dbf9ac): F1 0.99 (78/79), rejects 4/4, noise 0. SPEC revision 4: compound-hyphen rule narrowed, bare-number title prefix stripped in evaluation.
