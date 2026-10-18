# Dialogue detection results

Generated 2026-10-18 23:47 by `scripts/evaluate_dialogue.py`, commit `2c96be1`.
Match: same paragraph, overlap ≥ 80% of the longer span, one-to-one.

| Chapter | Family | Gold | Predicted | TP | P | R | F1 | Grouping (gold→pred, pred→gold) | Not-a-quote found |
|---|---|---|---|---|---|---|---|---|---|
| alice_ch07 | single | 145 | 147 | 144 | 0.98 | 0.99 | 0.99 | 0.76 (27/27, 27/44) | 0/1 |
| brown_glass | double | 124 | 124 | 124 | 1.00 | 1.00 | 1.00 | 0.98 (48/50, 50/50) | 0/0 |
| **micro** | | 269 | 271 | 268 | 0.99 | 1.00 | 0.99 | 0.89 | |

Grouping: of the utterances with ≥ 2 spans (gold, then predicted), the share whose spans are
all matched to spans of one utterance on the other side.

## Segmenter statistics

- **alice_ch07**: marks 359, apostrophes 70, stray closers 2, reopened 1, unclosed closed 2, continuations 0, speech-tag closures 0, in-paragraph tag closures 4
- **brown_glass**: marks 248, apostrophes 0, stray closers 0, reopened 0, unclosed closed 0, continuations 0, speech-tag closures 0, in-paragraph tag closures 0

## alice_ch07: errors

- FP ¶76 [0:96] `‘There’s no such thing! Alice was beginning very angrily, b…`
- FP ¶95 [91:112] `‘That’s very curious!`
- FP ¶95 [126:196] `’But everything‘s curious today. I think I may as well go i…`
- FN ¶76 [0:23] `‘There’s no such thing!`

## brown_glass: errors

None.
