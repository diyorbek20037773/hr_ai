---
description: Start the server if down, check health/ready/metrics, run the smoke test, report
---
1. `Invoke-RestMethod http://127.0.0.1:8001/health` — if it fails, run `.\run.ps1 -Background` and poll `/ready`
   every 5 s until 200 (max 5 minutes, the model load is slow).
2. Show `/health` JSON, `/ready` status code, and the `hr_ai_` lines of `/metrics` (model_loaded, llm_queue_depth,
   gpu_memory_used_bytes, request counters).
3. Run `.\scripts\monitor.ps1 -IntervalSeconds 5 -DurationSeconds 30`.
4. Run `.\scripts\smoke_test.ps1` and paste its PASS/FAIL table.
5. Verdict: OK only if ready=200, monitor 0 failures, smoke all PASS.
