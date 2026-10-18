# Phase 5 — Go live, polish, and viva preparation

Read first: `CLAUDE.md`, `docs/DECISIONS.md` (new **D30**), `docs/SPEC.md` **revision 7** (§4 upload limit row, §9 status table, §12 `RATE_LIMITED`), `docs/spike_results.md`, `testset/results.md`, `testset_heldout/results_history.md`.
Production: backend `https://lekhya-audiobook.centralindia.cloudapp.azure.com` (Azure VM, `deploy/`), frontend → Vercel (this phase).

---

## 1. Per-IP upload limit (D30) — backend
- In-memory sliding window: 10 uploads per client IP per rolling hour, counted at the start of `POST /api/upload` (rejected uploads count too). Over the limit → `429 RATE_LIMITED` with `Retry-After` (seconds until the oldest entry leaves the window), and the body is not read.
- Client IP: `X-Forwarded-For` (first address) **only** when the direct peer is the Caddy container (a private Docker network address); otherwise the socket peer address. Make the trusted-proxy check a small function with tests.
- Config: `UPLOADS_PER_IP_PER_HOUR=10` (0 disables). Tests: 10 pass, the 11th is 429 with Retry-After; different IPs are independent; spoofed `X-Forwarded-For` from a non-proxy peer is ignored; the window slides (inject a clock).
- Frontend: show the `RATE_LIMITED` message from SPEC §12 (add to `lib/errors.js`, plus a test).

## 2. Final held-out run
Run `python scripts\evaluate.py --testset ..\testset_heldout --history` once on the current commit (a logged later run — the first run, 0.99, stays the reported held-out number). Run the dev set too. Report both overall lines.

## 3. Docs
**`README.md` (root) — the project's front page.** Sections:
1. One-paragraph pitch + **live links** (Vercel URL placeholder `<VERCEL_URL>` until step 5, backend health URL) + a screenshot from `docs/screenshots/`.
2. Features (v1).
3. **How it works** — a Mermaid diagram of the pipeline: upload → validate → extract (PyMuPDF / python-docx) → clean (headers, footers, hyphenation, captions, footnotes, equations) → structure detection (outline → styles → typography → patterns → fallback) → text normalization → sentence chunking → edge-tts (4 in parallel) → ffmpeg assembly → chapter MP3s → player. Plus a deployment diagram: browser → Vercel (static) and browser → Caddy (HTTPS) → FastAPI container on the Azure VM.
4. **Evaluation** — chapter detection: dev set 1.00 F1 (rules developed on it), **held-out first run 0.99 F1 (78/79), 4/4 rejects, 0 noise**, later runs from the history file; how the matching works (SPEC §13) in three sentences; why a held-out set exists (D25).
5. **Performance** — table: Render free (172 chars/s, 0.15 CPU) vs Azure B2ats v2 (599–720 chars/s), Alice 2h36m of audio in 3m16s, largest allowed book ≈ 12–15 min, peak memory 131 MB; the Piper memory finding (D26) in one sentence.
6. Limits & non-goals (SPEC §2, §4).
7. Tech stack; repository layout (short tree).
8. Run locally (backend + frontend, Windows PowerShell); run tests (backend count, frontend count); deploy (link to `deploy/README.md` and the Vercel steps).
9. Key decisions — a short table of the 8–10 most important D-numbers with one-line reasons, linking to `docs/DECISIONS.md`.
10. Future work (v2: per-character voices with LLM speaker attribution; others from SPEC §15).

**`docs/VIVA.md` — viva preparation.** Written for the student to read before the viva:
- 2-minute project summary (what, why, how, results).
- "Where is the AI / data science?" — neural TTS (edge-tts neural voices; Piper is a local neural model, VITS-based, ONNX); text normalization for speech; document structure recovery as a classification/segmentation problem solved with interpretable heuristics; **evaluation methodology** (labelled test set, precision/recall/F1, fuzzy one-to-one matching, held-out set to avoid overfitting the rules); empirical system measurements (memory, throughput) driving design decisions.
- 20 likely examiner questions with short, honest answers (e.g. why heuristics instead of an ML model for headings; why the dev score is 1.00 but you report 0.99; what happens if edge-tts goes down; why not OCR; how two-column PDFs are handled; how you avoid reading page numbers; why Azure instead of Render; how chunks are joined without re-encoding; how seeking works (HTTP Range); what limits exist and why; what the job lifecycle is; how you'd scale to many users; privacy — files are temporary and deleted; what v2 adds).
- Known limitations, stated plainly (2-page documents keep a header that appears on only one page; equations are skipped so sentences can have gaps; compound words split across lines may lose the hyphen; edge-tts is an unofficial endpoint; English only; scanned PDFs rejected).
- A 3-minute **live demo script**: open the site → play the sample → upload a PDF → show progress and early playback of chapter 1 → change speed / seek → download a chapter → show a rejected scanned PDF → show the README evaluation table. Include a fallback plan if the network is bad (the sample works without the backend; have a pre-generated full book downloaded on the laptop).

**`docs/DEMO_VIDEO.md`** — a shot list for a ~3-minute screen recording (same flow as the demo script, with what to say in each shot).

Update `docs/SPEC.md` nothing (I own it). Update `CLAUDE.md` only if commands changed.

## 4. Tests, commit
`pytest -q` and `npm test` / `npm run build` clean. Commit `Phase 5: per-IP upload limit, README, viva notes` and push.

## 5. Stop and hand over: deploy steps for the user
Print these exact steps:

**A. Backend update on the VM** (gets the rate limit):
```
ssh -i $env:USERPROFILE\.ssh\audiobook-key.pem azureuser@lekhya-audiobook.centralindia.cloudapp.azure.com
cd ~/Project-2/deploy && bash deploy.sh
```

**B. Vercel** (vercel.com, sign in with GitHub — same account as Project 1):
1. Add New → Project → import `Project-2`.
2. Root Directory: `frontend`. Framework preset: Vite (auto). Build command `npm run build`, output `dist` (defaults).
3. Environment variable: `VITE_API_URL` = `https://lekhya-audiobook.centralindia.cloudapp.azure.com` (Production and Preview).
4. Deploy. Note the URL (e.g. `https://project-2-xxxx.vercel.app`). Optionally rename the project to `readaloud` under Settings → General so the URL becomes `readaloud-<something>.vercel.app`.

**C. Allow the Vercel site in the backend's CORS** (on the VM):
```
cd ~/Project-2/deploy && nano .env
# CORS_ORIGINS=https://<your-vercel-url>,http://localhost:5173
bash deploy.sh
```
(A redeploy empties running jobs — don't do it while someone is converting.)

**D. Production check** — on the Vercel URL, from the laptop **and from a phone on mobile data**: open `/sample` and play; upload `06_no_styles.docx`, play chapter 1 early, change speed, download a chapter and the full book; reload `/book/<id>` directly (tests the Vercel rewrite); upload `08_scanned.pdf` (rejection message).

Then wait for the user to send the Vercel URL and the result of D.

## 6. After the user replies
Replace `<VERCEL_URL>` in README.md and VIVA.md, add the production check results to `docs/spike_results.md` ("Production check" section), commit `Phase 5: live URLs`, push, and give a short final report: live URLs, test counts, evaluation numbers, anything left open.
