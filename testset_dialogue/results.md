# Dialogue detection results

Generated 2026-10-18 23:08 by `scripts/evaluate_dialogue.py`, commit `b0d1d77`.
Match: same paragraph, overlap ≥ 80% of the longer span, one-to-one.

| Chapter | Family | Gold | Predicted | TP | P | R | F1 | Grouping (gold→pred, pred→gold) | Not-a-quote found |
|---|---|---|---|---|---|---|---|---|---|
| alice_ch07 | single | 140 | 147 | 140 | 0.95 | 1.00 | 0.98 | 0.69 (25/26, 23/44) | 0/1 |
| brown_glass | double | 124 | 124 | 124 | 1.00 | 1.00 | 1.00 | 0.98 (48/50, 50/50) | 0/0 |
| **micro** | | 264 | 271 | 264 | 0.97 | 1.00 | 0.99 | 0.86 | |

Grouping: of the utterances with ≥ 2 spans (gold, then predicted), the share whose spans are
all matched to spans of one utterance on the other side.

## Segmenter statistics

- **alice_ch07**: marks 359, apostrophes 70, stray closers 2, reopened 1, unclosed closed 2, continuations 0, speech-tag closures 0, in-paragraph tag closures 4
- **brown_glass**: marks 248, apostrophes 0, stray closers 0, reopened 0, unclosed closed 0, continuations 0, speech-tag closures 0, in-paragraph tag closures 0

## alice_ch07: errors

- FP ¶36 [0:21] `‘Of course you don’t!`
- FP ¶43 [59:121] `‘We quarrelled last March—just before HE went mad, you know…`
- FP ¶76 [0:96] `‘There’s no such thing! Alice was beginning very angrily, b…`
- FP ¶76 [97:105] `’Sh! sh!`
- FP ¶94 [91:151] `’It‘s the stupidest tea-party I ever was at in all my life!’`
- FP ¶95 [91:112] `‘That’s very curious!`
- FP ¶95 [126:196] `’But everything‘s curious today. I think I may as well go i…`

## brown_glass: errors

None.
