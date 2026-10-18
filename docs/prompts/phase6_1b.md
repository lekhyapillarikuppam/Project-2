# Phase 6.1b — Label lint and label fixes

Read first: `docs/SPEC.md` **revision 10** §15.2 (rule 5a) and §15.9 (label lint), `docs/DECISIONS.md` D25, D35, D38, `testset_dialogue/results.md`.
Short phase: tooling only, so the user can fix the labels. No segmenter or attribution changes.

**Held-out rule:** you may write code that ball_ch03 will later be run through, but never run anything on `ball_ch03` or open its text or labels. The user runs the lint on it.

## 1. `label_dialogue.py --lint <text.json>` (SPEC §15.9)
Label-file-only checks (use the `.jsonl`, `.cast.json`, `.state.json` and the text file; do **not** import `app.dialogue.segment`):
- **error** overlapping gold spans in the same paragraph (e.g. a crude candidate span left as a quote next to the corrected one);
- **warn** a span that doesn't start with, or doesn't end with, a quote mark of the chapter's family;
- **warn** a span that may contain narration: inside the span, `! ? , . ;` followed by a speech tag (speech verb near a capitalised name, `the` + noun, or a pronoun);
- **warn** a `Not a quote` decision whose following 25 characters contain `thought` (D38: thoughts belong to the thinker);
- **warn** two consecutive spans in one paragraph with different utterance ids where the narration between them contains a speech verb and ends with `,` `—` or `:` (possible broken quote not joined).
Output: one line per finding with chapter, paragraph number, span offsets, a 60-character excerpt and the problem; a summary count; exit code 1 if any error. Unit tests on synthetic label files.

## 2. Make fixing easy in the tool
Check that, after **Export / Done**, the user can still: jump to any candidate or paragraph (add a "go to paragraph N" box), change a decision, change a span, mark Not a quote, toggle Same utterance, delete an added span. Add what's missing. Export / Done also runs the lint and refuses on errors (warnings are shown and allowed).

## 3. Run and report
- Run `--lint` on `alice_ch07` and `brown_glass`; paste the full output in the report (it is the user's fix list).
- Print the exact command for the user to run on ball_ch03 themselves.
- `pytest -q` passes. Commit `Phase 6.1b: label lint` and push. Stop.
