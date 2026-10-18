# Dialogue detection and attribution results

Generated 2026-10-19 00:27 by `scripts/evaluate_dialogue.py`, commit `95e2da6 + uncommitted changes`.
Match: same paragraph, overlap ≥ 80% of the longer span, one-to-one.

| Chapter | Family | Gold | Predicted | TP | P | R | F1 | Grouping (gold→pred, pred→gold) | Not-a-quote found |
|---|---|---|---|---|---|---|---|---|---|
| alice_ch07 | single | 145 | 147 | 144 | 0.98 | 0.99 | 0.99 | 0.77 (27/27, 27/43) | 0/1 |
| brown_glass | double | 124 | 124 | 124 | 1.00 | 1.00 | 1.00 | 0.98 (48/50, 50/50) | 0/0 |
| **micro** | | 269 | 271 | 268 | 0.99 | 1.00 | 0.99 | 0.89 | |

Grouping: of the utterances with ≥ 2 spans (gold, then predicted), the share whose spans are
all matched to spans of one utterance on the other side.

## Segmenter statistics

- **alice_ch07**: marks 359, apostrophes 70, stray closers 2, reopened 1, unclosed closed 2, continuations 0, speech-tag closures 0, in-paragraph tag closures 4
- **brown_glass**: marks 248, apostrophes 0, stray closers 0, reopened 0, unclosed closed 0, continuations 0, speech-tag closures 0, in-paragraph tag closures 0

## Attribution candidates (D39)

Scored on gold utterances matched by detection. Voice accuracy uses the system's own voice map (each chapter ranked as a whole book). Tokens and latency are those of the original Groq requests.

| Candidate | Voice acc (micro) | Voice alice_ch07 | Voice brown_glass | Attribution acc | Explicit | Implicit | % UNKNOWN | Requests | Prompt tok | Completion tok | Median latency (s) | Total latency (s) | Baseline windows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.70 | 0.91 | 0.38 | 0.71 | 0.73 | 0.65 | 13% | 0 | 0 | 0 | 0.00 | 0.0 | - |
| llm · qwen/qwen3.8-27b | 0.99 | 0.99 | 0.99 | 0.94 | 0.93 | 0.96 | 3% | 7 | 14573 | 2060 | 0.93 | 7.2 | 0/7 |
| llm_with_baseline_override · qwen/qwen3.8-27b | 0.86 | 0.98 | 0.67 | 0.86 | 0.84 | 0.89 | 2% | 7 | 14573 | 2060 | 0.93 | 7.2 | 0/7 |
| llm · openai/gpt-oss-120b | 0.96 | 0.99 | 0.90 | 0.93 | 0.92 | 0.95 | 4% | 7 | 14640 | 4227 | 2.25 | 13.8 | 0/7 |
| llm_with_baseline_override · openai/gpt-oss-120b | 0.84 | 0.98 | 0.61 | 0.84 | 0.83 | 0.85 | 3% | 7 | 14640 | 4227 | 2.25 | 13.8 | 0/7 |

Gold utterances missed by detection (not scored): alice_ch07 1, brown_glass 0.

**D39 selection: llm · qwen/qwen3.8-27b** (voice accuracy 0.989; tied within 2 points: llm · qwen/qwen3.8-27b).

## alice_ch07: attribution errors (llm · qwen/qwen3.8-27b)

- ¶22 `‘you shouldn’t have put it in with the bread-knife.’` gold **the March Hare** → predicted **Hatter** (llm/llm); voice expected Voice 2 (female, American), heard Voice 13 (male, British)

## brown_glass: attribution errors (llm · qwen/qwen3.8-27b)

- ¶23 `"I'm sorry to interrupt you, sir,"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶25 `"James has been murdered, for all I can make out,"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶27 `"I know his name is Glass,"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶29 `"I tried to break down the door and couldn't,"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶36 `"But, good heavens!"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶40 `"Look here,"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶42 `"Are you going to take those ropes off?"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶58 `"His eyes do look queer,"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)
- ¶83 `"Do you mean inside Mr Todhunter's clothes?"` gold **Mrs MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 2 (female, American), heard Voice 12 (female, American)
- ¶87 `"But What about the two voices?"` gold **Maggie MacNab** → predicted **Maggie** (llm/llm); voice expected Voice 12 (female, American), heard Voice 12 (female, American)

## alice_ch07: detection errors

- FP ¶76 [0:96] `‘There’s no such thing! Alice was beginning very angrily, b…`
- FP ¶95 [91:112] `‘That’s very curious!`
- FP ¶95 [126:196] `’But everything‘s curious today. I think I may as well go i…`
- FN ¶76 [0:23] `‘There’s no such thing!`

## brown_glass: detection errors

None.
