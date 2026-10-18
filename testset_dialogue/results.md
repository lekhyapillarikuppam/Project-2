# Dialogue detection results

Generated 2026-10-18 22:13 by `scripts/evaluate_dialogue.py`, commit `86448ca + uncommitted changes`.
Match: same paragraph, overlap ≥ 80% of the longer span, one-to-one.

| Chapter | Family | Gold | Predicted | TP | P | R | F1 | Grouping (gold→pred, pred→gold) | Not-a-quote found |
|---|---|---|---|---|---|---|---|---|---|
| alice_ch07 | single | 142 | 147 | 137 | 0.93 | 0.96 | 0.95 | 0.09 (2/2, 2/44) | 5/6 |
| brown_glass | double | 124 | 124 | 124 | 1.00 | 1.00 | 1.00 | 0.98 (48/50, 50/50) | 0/0 |
| **micro** | | 266 | 271 | 261 | 0.96 | 0.98 | 0.97 | 0.70 | |

Grouping: of the utterances with ≥ 2 spans (gold, then predicted), the share whose spans are
all matched to spans of one utterance on the other side.

## Segmenter statistics

- **alice_ch07**: marks 359, apostrophes 70, stray closers 2, reopened 1, unclosed closed 2, continuations 0, speech-tag closures 0, in-paragraph tag closures 4
- **brown_glass**: marks 248, apostrophes 0, stray closers 0, reopened 0, unclosed closed 0, continuations 0, speech-tag closures 0, in-paragraph tag closures 0

## alice_ch07: errors

- FP ¶0 [270:308] `‘Very uncomfortable for the Dormouse,’`
- FP ¶0 [324:374] `‘only, as it’s asleep, I suppose it doesn‘t mind.’`
- FP ¶10 [0:35] `‘Come, we shall have some fun now!’`
- FP ¶36 [0:21] `‘Of course you don’t!`
- FP ¶36 [72:114] `’I dare say you never even spoke to Time!’`
- FP ¶43 [59:121] `‘We quarrelled last March—just before HE went mad, you know…`
- FP ¶43 [171:248] `’—it was at the great concert given by the Queen of Hearts,…`
- FP ¶76 [0:96] `‘There’s no such thing! Alice was beginning very angrily, b…`
- FP ¶95 [91:112] `‘That’s very curious!`
- FP ¶95 [126:196] `’But everything‘s curious today. I think I may as well go i…`
- FN ¶36 [0:114] `‘Of course you don’t! the Hatter said, tossing his head con…`
- FN ¶43 [59:248] `‘We quarrelled last March—just before HE went mad, you know…`
- FN ¶76 [0:23] `‘There’s no such thing!`
- FN ¶76 [0:209] `‘There’s no such thing! Alice was beginning very angrily, b…`
- FN ¶94 [0:151] `‘At any rate I’ll never go THERE again! said Alice as she p…`

## brown_glass: errors

None.
