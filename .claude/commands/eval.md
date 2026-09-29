---
description: Run the golden eval against the real local model and diff vs the previous report
---
1. Save the current report for comparison: copy `docs/model-eval.md` to the scratchpad as `model-eval.prev.md` (if it exists).
2. Run `.venv/Scripts/python.exe -m app.scripts.eval_model` (loads the model once; takes minutes).
3. Read the new `docs/model-eval.md`.
4. Report: prompt version, device, core X/10, full-match %, invented-field count, p50/p95 latency, and which queries
   changed PASS<->FAIL compared with the previous report.
Never edit `tests/eval/golden_queries.jsonl` to improve the score.
