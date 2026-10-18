# Dialogue evaluation history

Appended by `evaluate_dialogue.py --history`; never overwritten. The held-out chapter's first
entry is its reported score (D25, D35).

## 2026-10-19 00:27 - commit `95e2da6 + uncommitted changes` - set `dev` (detection + attribution)

- Precision / recall / F1 (micro): 0.99 / 1.00 / 0.99 (TP 268, predicted 271, gold 269)
- Grouping accuracy (micro): 0.89
- Per chapter: alice_ch07 F1 0.99 grouping 0.77, brown_glass F1 1.00 grouping 0.98
- baseline: voice 0.70, attribution 0.71 (explicit 0.73, implicit 0.65), tokens 0
- llm · qwen/qwen3.8-27b: voice 0.99, attribution 0.94 (explicit 0.93, implicit 0.96), tokens 16633
- llm_with_baseline_override · qwen/qwen3.8-27b: voice 0.86, attribution 0.86 (explicit 0.84, implicit 0.89), tokens 16633
- llm · openai/gpt-oss-120b: voice 0.96, attribution 0.93 (explicit 0.92, implicit 0.95), tokens 18867
- llm_with_baseline_override · openai/gpt-oss-120b: voice 0.84, attribution 0.84 (explicit 0.83, implicit 0.85), tokens 18867
- D39 selection: llm · qwen/qwen3.8-27b

## 2026-10-19 02:34 - commit `66b8cd3 + uncommitted changes` - set `dev` (detection + attribution)

- Precision / recall / F1 (micro): 0.99 / 1.00 / 0.99 (TP 268, predicted 271, gold 269)
- Grouping accuracy (micro): 0.90
- Per chapter: alice_ch07 F1 0.99 grouping 0.79, brown_glass F1 1.00 grouping 0.98
- baseline: voice 0.71, attribution 0.72 (explicit 0.74, implicit 0.67), tokens 0
- llm · qwen/qwen3.8-27b: voice 0.99, attribution 0.99 (explicit 1.00, implicit 0.98), tokens 16633
- llm_with_baseline_override · qwen/qwen3.8-27b: voice 0.87, attribution 0.88 (explicit 0.86, implicit 0.91), tokens 16633
- llm · openai/gpt-oss-120b: voice 0.96, attribution 0.97 (explicit 0.96, implicit 0.98), tokens 18867
- llm_with_baseline_override · openai/gpt-oss-120b: voice 0.84, attribution 0.86 (explicit 0.85, implicit 0.89), tokens 18867
- D39 selection: llm · qwen/qwen3.8-27b

## 2026-10-19 03:04 - commit `b45c704` - set `heldout` (detection + attribution)

- Precision / recall / F1 (micro): 0.99 / 0.99 / 0.99 (TP 75, predicted 76, gold 76)
- Grouping accuracy (micro): 0.95
- Per chapter: ball_ch03 F1 0.99 grouping 0.95
- baseline: voice 0.48, attribution 0.60 (explicit 0.96, implicit 0.14), tokens 0
- llm · qwen/qwen3.8-27b: voice 0.83, attribution 0.83 (explicit 0.96, implicit 0.67), tokens 7087
- llm_with_baseline_override · qwen/qwen3.8-27b: voice 0.67, attribution 0.83 (explicit 0.96, implicit 0.67), tokens 7087
- llm · openai/gpt-oss-120b: voice 0.54, attribution 0.69 (explicit 0.89, implicit 0.43), tokens 8542
- llm_with_baseline_override · openai/gpt-oss-120b: voice 0.65, attribution 0.79 (explicit 0.96, implicit 0.57), tokens 8542
- D39 selection: llm · qwen/qwen3.8-27b
