# Phase 6.0 — v2 groundwork: verify services, extract chapters, labelling tool

Read first: `CLAUDE.md`, `docs/DECISIONS.md` (**D31–D36**, and D25 for held-out rules), `docs/SPEC.md` **revision 8 §15** (all of it) plus §6.6, §7.1, §7.2.
This phase writes **no production code** for v2 (no segmenter, no attribution, no API/UI changes). It prepares everything the next phases need.

## Held-out rule (D25/D35)
`ball_ch03` is the held-out dialogue chapter. You may write the code that extracts it, but **do not print, open, read or inspect its text or labels**, and never run the labelling tool on it yourself. The user labels it.

## 1. Verify external services (report findings, don't guess)
**Groq** — the user will put `GROQ_API_KEY=...` in `backend/.env` (never commit it; check `.gitignore` covers `.env`). Then:
- Call Groq's OpenAI-compatible chat completions endpoint with `httpx` (no SDK). List available models (`/models`) and pick the current Llama 3.3 70B class model id (or its current successor); report the exact id.
- Confirm JSON response mode works (`response_format`), with a tiny prompt.
- Read the rate-limit response headers (requests/tokens per minute and per day) and report the actual limits for this key.
- Measure latency for a ~1,500-word window prompt (use a public-domain paragraph block from `testset/output/01_novel.txt`, not a held-out file).
- Add `GROQ_API_KEY=` (empty), `LLM_MODEL=<id>`, `LLM_MAX_REQUESTS_PER_MINUTE=25`, `LLM_MAX_REQUESTS_PER_DAY=800`, `MAX_CAST=8` to `backend/.env.example` and `config.py` (settings only; nothing uses them yet). Also add `GROQ_API_KEY=` and `LLM_MODEL=` to `deploy/.env.example`.
- Add a Groq check to `scripts/check_env.py`: skipped with a WARN when no key is set; when set, one tiny JSON-mode request.

**edge-tts voices** — run the voice listing for the pinned edge-tts version. From English neural voices (en-US, en-GB, en-AU, en-CA, en-IE, en-IN, en-NZ, en-ZA…), propose:
- a **female pool** and a **male pool** of at least 8 each, excluding the narrator `en-US-AriaNeural`, ordered so neighbours differ in accent; and
- for each voice a short human label for the UI (e.g. `Voice 3 (female, British)`).
Synthesize the same sentence with every proposed voice into `testset/output/voices/` and make `voices_preview.mp3` (each voice says its own label, 300 ms gap) so the user can listen and veto voices. Put the proposed pools in `backend/app/dialogue/voice_pool.json` (data only).

## 2. Dialogue chapters (`testset_dialogue/`)
Create `scripts/extract_dialogue_chapter.py` that runs the Phase 1 pipeline on a document and writes one chapter as `testset_dialogue/text/<name>.json`: `{"source", "chapter_index", "title", "paragraphs": ["…", "…"]}` (paragraph texts exactly as the pipeline produces them, quote marks preserved).
- Dev: `alice_ch07` from `testset/docs/01_novel.pdf` (chapter "A Mad Tea-Party"); `brown_glass` from `testset/docs/05_styles.docx` (story "The Absence of Mr Glass"). Run these yourself.
- Held-out: `ball_ch03` from `testset_heldout/docs/H02_ball_and_cross.pdf`, chapter 3. **Do not run it** — print the exact command for the user.
- `.gitignore`: commit `testset_dialogue/labels/` and `testset_dialogue/text/alice_ch07.json`, `brown_glass.json` (public domain); keep `testset_dialogue/text/ball_ch03.json` committed too (public domain) — but you still must not read it.
- `testset_dialogue/README.md`: what each file is, the held-out rule, how to label.

## 3. Labelling tool `scripts/label_dialogue.py`
A small **local web page** is easiest for the user (they'll label ~315 quotes):
- `python scripts\label_dialogue.py ..\testset_dialogue\text\alice_ch07.json` starts a tiny server on `http://localhost:8765` (standard library `http.server` + one static HTML/JS page; no new dependencies) and opens the browser.
- **Candidate quotes** come from a deliberately simple, high-recall finder that is **independent of the future segmenter**: every span between an opening and a closing mark of the chapter's dominant quote style, with a crude apostrophe filter. (Detection quality is measured against the user's corrected spans, so the labels must not come from the code being evaluated.)
- Per candidate, the page shows the paragraph with the span highlighted plus the previous and next paragraph, and lets the user:
  - pick the speaker from the chapter's character list (type-ahead) or add a new character (name + gender);
  - mark **Not a quote**;
  - adjust the span (drag-select a different range in the paragraph text);
  - mark **Explicit** (a speech tag names the speaker nearby) — pre-ticked when `said <Name>`-like text is adjacent, but the user decides;
  - mark **Same utterance as previous** (for broken quotes);
  - **Add missed quote** by selecting text in any paragraph.
  - Keyboard shortcuts for speed: number keys for the top 9 characters, `N` not a quote, `E` toggle explicit, `Enter` next, `Backspace` previous.
- Character list per chapter with aliases (e.g. `Hatter` = `the Hatter`), editable on the page.
- Autosaves after every change to `testset_dialogue/labels/<name>.jsonl` and `<name>.cast.json` in the SPEC §15.9 format (`id, paragraph, start, end, text, speaker, explicit, utterance`), resumable, with a progress counter and an **Export / Done** button that validates (every candidate decided, every speaker in the cast).
- Test the tool yourself on `alice_ch07` only: label the **first 10** candidates to check it works, then **reset** that file so the user labels from scratch (your labels must not remain).

## 4. Spec review
Read SPEC §15 critically and list contradictions, gaps, or things that look hard (especially the apostrophe rules in §15.2 and window sizing in §15.3) — do not edit the spec.

## 5. Report
- Groq: model id, real limits, latency, JSON mode result.
- Voice pools (tables) and where the preview MP3 is.
- Chapters extracted (paragraph counts and candidate-quote counts for the two dev chapters).
- How to run the labelling tool, the exact held-out extraction command for the user, and an estimate of labelling time.
- Spec review findings.
Commit `Phase 6.0: v2 groundwork (Groq check, voice pools, dialogue chapters, labelling tool)` and push. Do not start 6.1.
