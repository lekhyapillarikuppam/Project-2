# Phase 6.3 — Ship character voices: casting fix, jobs/API/UI, held-out run, docs

Read these first:
- `CLAUDE.md`
- `docs/DECISIONS.md`: **D40–D42** are new or changed. Also D25, D31–D39.
- `docs/SPEC.md` revision 11. Read all of §15 (§15.5 casting, §15.3 pacing, §15.7 timing and the rule-4 exception have changed), plus §7.2 and §9.
- `testset_dialogue/results.md`

**Check the docs first.** `git diff HEAD -- docs/DECISIONS.md` should show only the D41/D42 rows and change-log lines as new; D40 must be the same as yours. `docs/SPEC.md` gains the 6.2 interpretations. If anything else differs, stop and tell me.

**Held-out rule:**
- `ball_ch03` is run **once**, in step 6, after everything else in this phase is finished and committed.
- Never open its text or labels, and never print its errors.
- If anything in the code changes after that run, it doesn't change the reported number. Log any later run with `--history` and clearly label it as a later run.

## 1. Casting fix (D41, SPEC §15.5)
- Assign voices lazily per chapter, just before that chapter is narrated, using that chapter's attribution (LLM names and genders).
- The baseline book pass only **reserves** up to `MAX_CAST − 2` voices. To qualify, a name must be capitalised (never a lowercase epithet) and have ≥ 3 explicitly tagged lines in the book.
- Unreserved characters get a distinct voice only if they have ≥ 2 lines in the chapter and slots remain. Otherwise they get the minor voice for their gender.
- Voices never change once used.
- Tests:
  - an epithet can't take a slot;
  - a reserved character gets the LLM's gender;
  - a late major character still gets a distinct voice when slots remain;
  - one-line characters get the minor voice;
  - assignment is deterministic and never changes.
- Re-run the dev evaluation for all candidates from the disk cache, so there are no new Groq calls. Report old vs new voice accuracy. Re-apply D39: if the winner changes, update D40 and say so loudly. Append to the history with `--history`.

## 2. Output-token pacing (D42)
- Add `LLM_OUTPUT_TOKENS_PER_MINUTE=1000` (config and `.env.example`) as a second rolling budget in the limiter.
- Estimate completion tokens before each request as 12 × quote ids + 40, then correct from `usage`.
- Tests use the fake clock.

## 3. Jobs and API (SPEC §15.1)
**Upload:**
- `POST /api/upload` takes `narration_mode` (`single` default | `characters`; anything else → 400 `BAD_REQUEST`).
- `characters` without a configured key is also a 400, with a clear message.

**Job object** gains:
- `narration_mode`;
- status `attributing`;
- `progress.windows_total` and `progress.windows_done`;
- `cast`;
- per chapter `attribution` (`llm` | `baseline` | `mixed` | `none`);
- the §15.1 warning when any chapter used the baseline.

`GET /api/config` gains `character_voices_available`.

**Worker** (D20, D37, D41):
1. Extract, then segment.
2. Run the baseline book pass (reservations).
3. Attribute chapter 1 (status `attributing`).
4. While chapter N is narrated, attribute chapter N+1 in the same event loop, as a task one chapter ahead.
5. Cast that chapter's voices, then narrate it.

Requirements:
- Single-narrator jobs take exactly the v1 path.
- An LLM failure never fails the job (D34).
- Cancelling or expiring a job also cancels its attribution task.
- Characters mode must not raise peak memory meaningfully.

**Tests:**
- API tests with a fake LLM client: mode validation, the job object fields, chapter N+1 attributed before chapter N finishes, LLM down → `baseline` chapters plus the warning, single mode unchanged.
- `scripts/smoke_api.py --mode characters`.

**Full-book timing (real Groq, local):** run the whole Alice book through the API in characters mode. Report:
- attribution time, narration time and total time;
- time to the first playable chapter;
- the number of 429 waits;
- windows that fell back to the baseline;
- peak memory.

## 4. Frontend (SPEC §15.8)
- **Upload page:** a "Single narrator" (default) / "Character voices (beta)" choice, with the privacy line (D36). Hide the choice when `character_voices_available` is false.
- Add a one-line note that character voices take longer on long books (D42).
- **Progress:** "Finding the characters… (window 3 of 12)".
- **Book page:**
  - a **Cast** section: Narrator first, then each character with voice label and lines;
  - a per-chapter note when the chapter's attribution is `baseline` or `mixed`.
- **Sample:** a static `/sample/characters` from the Alice ch. 7 characters-mode run, linked from the existing sample page.
  - It includes the MP3 plus a cast JSON.
  - Re-generate it after the step 1 fix.
  - Keep it under ~8 MB.
- **Tests:** add Vitest tests for each of these. `npm test` and `npm run build` must be clean.

## 5. Deploy preparation
- `deploy/.env.example` gains `GROQ_API_KEY=`, `LLM_MODEL`, `ATTRIBUTION_METHOD`, `LLM_TOKENS_PER_MINUTE`, `LLM_OUTPUT_TOKENS_PER_MINUTE`, `LLM_MAX_REQUESTS_PER_DAY`, `MAX_CAST`.
- Confirm `httpx` and `rapidfuzz` are runtime dependencies in the Docker image, not just test dependencies.
- Build the image locally if Docker is available and report its size.

## 6. Held-out first run (D25, D35)
Only after steps 1–5 are done, tests pass, and the work is committed as `Phase 6.3: character voices end to end`:
```
python scripts\evaluate_dialogue.py --set heldout --i-know-this-is-the-held-out-run --methods all --models qwen/qwen3.8-27b,openai/gpt-oss-120b --history
```
- This is one run. Do not open `results_heldout.md` beyond its summary tables, and do not change any code because of it.
- Report:
  - detection P/R/F1 and grouping;
  - every candidate's voice and attribution accuracy (explicit/implicit);
  - the D40 configuration's numbers as **the reported held-out result**;
  - dev vs held-out side by side.
- Commit `Held-out first run (dialogue)` and push.

## 7. Docs
**`README.md`:**
- v2 features;
- the pipeline diagram extended (segment → baseline pass → per-chapter attribution one chapter ahead → lazy casting → multi-voice TTS);
- an **evaluation** subsection: detection, the attribution candidates table (dev), the held-out first-run line, why D39 was fixed in advance;
- what the thinking-off test found;
- character-voices limits and timing (D42);
- the privacy note.

**`docs/VIVA.md`:**
- a v2 summary;
- "where is the AI" for v2 (LLM attribution vs rule baseline, a measured comparison of two LLMs, evaluation design, held-out honesty);
- about 12 more likely questions with honest answers. Examples: why the LLM beats the rules on Brown; why override hurt; why voice accuracy is the metric; how rate limits shaped the design; what happens when Groq is down; why the grouping score is below target and why it doesn't matter much; labelling consistency and the lint; privacy.
- the v2 known limitations.
- extend the demo script with a characters-mode segment.

**`docs/DEMO_VIDEO.md`:** add one shot for character voices.

Commit `Phase 6.3: docs` and push.

## 8. Stop and hand over: steps for the user
Print these exact steps.

**A. Update the backend on the VM:**
```
ssh -i $env:USERPROFILE\.ssh\audiobook-key.pem azureuser@lekhya-audiobook.centralindia.cloudapp.azure.com
cd ~/Project-2/deploy && nano .env    # add GROQ_API_KEY=... and the new LLM_* / ATTRIBUTION_METHOD / MAX_CAST lines
bash deploy.sh
```

**B. Vercel** redeploys automatically from the push. Check that the deployment shows the new commit.

**C. Production check** (laptop and phone):
- `/sample/characters` plays.
- The upload page shows the character-voices option.
- Upload `05_styles.docx` in characters mode. Watch "Finding the characters…", then confirm chapter 1 plays with distinct voices and the cast list looks right.
- Upload `06_no_styles.docx` in single mode: it should behave as before.
- On the VM, run `docker stats --no-stream` during the characters job and note the memory.

Then wait for the user's results.

## 9. After the user replies
Add a "Production check (v2)" section to `docs/spike_results.md`, and fill any placeholders in README and VIVA. Commit `Phase 6.3: production check` and push. Give a short final report:
- live URLs;
- test counts;
- dev and held-out numbers for v1 and v2;
- anything left open.
