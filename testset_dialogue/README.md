# Dialogue test set (v2 speaker attribution, SPEC §15.9, D35)

Three public-domain chapters, hand-labelled by the user, measure quote detection and speaker
attribution for character-voices mode.

| Name | Source | Chapter | Set |
|---|---|---|---|
| `alice_ch07` | `testset/docs/01_novel.pdf` | VII "A Mad Tea-Party" | dev |
| `brown_glass` | `testset/docs/05_styles.docx` | "The Absence of Mr Glass" | dev |
| `ball_ch03` | `testset_heldout/docs/H02_ball_and_cross.pdf` | chapter 3 | **held-out** |

## Files
- `text/<name>.json` — the chapter as the Phase 1 pipeline produces it:
  `{"source", "chapter_index", "title", "paragraphs": ["…", …]}`. Paragraph texts are exactly the
  pipeline's (quote marks preserved). Made by `backend/scripts/extract_dialogue_chapter.py`.
- `labels/<name>.jsonl` — gold quote spans, one JSON object per line:
  `{"id", "paragraph", "start", "end", "text", "speaker", "explicit", "utterance"}`.
  `paragraph` is the 0-based position in `text/<name>.json`'s `paragraphs`; `start`/`end` are
  character offsets in that paragraph (`end` exclusive) and include the quote marks.
  Spans with the same `utterance` (broken quotes, quotes continued over paragraphs) share a speaker.
  `speaker` is a cast name, or `UNKNOWN` when the text does not tell who speaks.
- `labels/<name>.cast.json` — `{canonical name: {"aliases": [...], "gender": "female|male|unknown"}}`.
- `labels/<name>.state.json` — the labelling tool's working state (every candidate and decision,
  including "not a quote"), so labelling can be resumed. The evaluation does not read it.

## Held-out rule (D25, D35)
`ball_ch03` is never opened, printed or inspected during development, and its labels are made by
the user only. Its first evaluation run is the number reported. Extract it with (from `backend/`):

```powershell
python scripts\extract_dialogue_chapter.py ..\testset_heldout\docs\H02_ball_and_cross.pdf ball_ch03 --chapter-number 3
```

The script prints only the chapter number, paragraph count and word count. If it says
`NO MATCH`, use `--index N` with the pipeline's chapter number instead.

## How to label
From `backend/` (venv active):

```powershell
python scripts\label_dialogue.py ..\testset_dialogue\text\alice_ch07.json
```

This opens `http://localhost:8765`. Everything is saved after each change; stop with Ctrl+C and
run the same command again to continue where you left off.

1. The highlighted span is a **candidate** from a deliberately crude quote finder (every span between
   an opening and a closing mark of the chapter's main quote style). It is independent of the
   segmenter that will be evaluated, so the labels do not inherit its mistakes.
2. Decide every candidate:
   - press **1–9** for one of the top characters (shown on the buttons), **0** for `UNKNOWN`, or type a
     name in the speaker box (`/` jumps there; aliases work). A new name asks for its gender;
   - **N** — not a quote (an apostrophe, a quoted title or word, etc.);
   - **E** — explicit: a speech tag names the speaker next to the quote (`said Alice`,
     `the Hatter said`). Pre-ticked when such a tag is adjacent; pronoun tags (`she said`) are not
     explicit;
   - **S** — same utterance as the previous quote (the second half of `‘Well,’ said Alice, ‘I suppose so.’`,
     or a quote continued from the previous paragraph);
   - **Enter** next, **Backspace** previous, **Next undecided** to jump.
3. Wrong span? Select the right text with the mouse in the current paragraph and click
   **Set span to selection**. A quote the finder missed? Select it in any paragraph (the
   **Full chapter** view shows all of them) and click **Add selection as missed quote**.
4. Cast panel: rename, set gender, add aliases (`the Hatter, Hatter`), remove unused characters.
5. **Export / Done** checks that every candidate is decided and every speaker is in the cast, and
   lists any problems.

Proposed conventions (not yet decided; settle them before labelling and apply them to all three
chapters): thoughts in quotes (`‘Very uncomfortable for the Dormouse,’ thought Alice`) are quotes
spoken by the thinker; a quote said by several characters together (`‘No room!’ they cried`) gets
`UNKNOWN`.
