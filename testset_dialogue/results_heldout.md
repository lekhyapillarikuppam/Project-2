# Dialogue detection and attribution results

Generated 2026-10-19 03:04 by `scripts/evaluate_dialogue.py`, commit `b45c704`.
Match: same paragraph, overlap ≥ 80% of the longer span, one-to-one.

| Chapter | Family | Gold | Predicted | TP | P | R | F1 | Grouping (gold→pred, pred→gold) | Not-a-quote found |
|---|---|---|---|---|---|---|---|---|---|
| ball_ch03 | double | 76 | 76 | 75 | 0.99 | 0.99 | 0.99 | 0.95 (26/28, 26/27) | 0/0 |
| **micro** | | 76 | 76 | 75 | 0.99 | 0.99 | 0.99 | 0.95 | |

Grouping: of the utterances with ≥ 2 spans (gold, then predicted), the share whose spans are
all matched to spans of one utterance on the other side.

## Segmenter statistics

- **ball_ch03**: marks 152, apostrophes 0, stray closers 1, reopened 0, unclosed closed 0, continuations 0, speech-tag closures 0, in-paragraph tag closures 1

## Attribution candidates (D39)

Scored on gold utterances matched by detection. Voice accuracy uses the system's own voice map (each chapter ranked as a whole book). Tokens and latency are those of the original Groq requests.

| Candidate | Voice acc (micro) | Voice ball_ch03 | Attribution acc | Explicit | Implicit | % UNKNOWN | Requests | Prompt tok | Completion tok | Median latency (s) | Total latency (s) | Baseline windows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.48 | 0.48 | 0.60 | 0.96 | 0.14 | 29% | 0 | 0 | 0 | 0.00 | 0.0 | - |
| llm · qwen/qwen3.8-27b | 0.83 | 0.83 | 0.83 | 0.96 | 0.67 | 8% | 4 | 6445 | 642 | 0.69 | 2.6 | 0/4 |
| llm_with_baseline_override · qwen/qwen3.8-27b | 0.67 | 0.67 | 0.83 | 0.96 | 0.67 | 2% | 4 | 6445 | 642 | 0.69 | 2.6 | 0/4 |
| llm · openai/gpt-oss-120b | 0.54 | 0.54 | 0.69 | 0.89 | 0.43 | 15% | 4 | 6733 | 1809 | 1.59 | 5.9 | 0/4 |
| llm_with_baseline_override · openai/gpt-oss-120b | 0.65 | 0.65 | 0.79 | 0.96 | 0.57 | 6% | 4 | 6733 | 1809 | 1.59 | 5.9 | 0/4 |

Gold utterances missed by detection (not scored): ball_ch03 0.

**D39 selection: llm · qwen/qwen3.8-27b** (voice accuracy 0.833; tied within 2 points: llm · qwen/qwen3.8-27b).
