# Operations

All commands from `D:\myprojects\hr_ai` in PowerShell.

## Docker

`./start.sh` (Linux, macOS) and `start.bat` / `.\start.ps1` (Windows) are the only entry points. They write
`.env.docker` (generated, gitignored), then run `docker compose --env-file .env.docker -f docker-compose.yml
-f docker/compose.<overlay>.yml`.

| Host | Overlay | torch | Model runs as |
|---|---|---|---|
| NVIDIA, driver CUDA ≥ 12.8 | `nvidia` | 2.11.0 cu128 | GPU; 4-bit below 9 GB free VRAM, else bf16 |
| NVIDIA, driver CUDA 12.6–12.7 | `nvidia` | 2.11.0 cu126 | same |
| NVIDIA, driver CUDA 11.8–12.5 | `nvidia` | 2.7.1 cu118 | same (not tested on real hardware) |
| NVIDIA with under 4.5 GB VRAM | `nvidia` | as above | GPU+CPU offload, no bitsandbytes; slower, needs RAM |
| AMD on Linux (`/dev/kfd`) | `amd` | 2.10.0 rocm7.0 | GPU, bf16; GPU+CPU offload below 9 GB free VRAM (not tested on real hardware) |
| AMD or Intel on Windows, Apple Silicon, no GPU | `cpu` | 2.11.0 cpu | CPU, bf16, needs 10 GB RAM for Docker; 6.6 min per query measured on an 8-core laptop |

Docker cannot pass AMD or Intel GPUs to containers on Windows, nor Apple GPUs on macOS, so those machines use the CPU.
With a GPU the script fails rather than silently falling back: it runs `torch.cuda.is_available()` in a container
before the model download. `--cpu` overrides this.

Flow: port and memory checks → build image → GPU check → `model-init` (one-shot, downloads to the `hr_ai_models`
volume) → `api`, `prometheus`, `grafana`. The model revision is pinned in `app/scripts/fetch_model.py`; a private
mirror can be set with `HF_MODEL_REPO` / `HF_MODEL_REVISION` and `HF_TOKEN`.

- **Offline rule:** only `model-init` reaches the Hub. `api` runs with `HF_HUB_OFFLINE=1`, a read-only model mount and
  `local_files_only=True`. After the first start the stack works without internet.
- **`./model` present:** it is mounted read-only and never downloaded over. If it is incomplete, `model-init` stops and
  lists the missing files.
- **Ports:** published on 127.0.0.1 only. Change them with `HR_AI_PORT` / `HR_AI_GRAFANA_PORT`, or on Windows with
  `start.bat -Port 8011 -GrafanaPort 3011`.
- **Shell variables, not `.env`:** `HF_TOKEN`, `HF_MODEL_REPO`, `HF_MODEL_REVISION`, `HR_AI_PORT`,
  `HR_AI_GRAFANA_PORT` are read from the shell environment. `start.*` sets `MODEL_*` and `LLM_TIMEOUT_SECONDS`.
- **Secrets:** an optional `.env` is passed to `api` (HR API URL, keys). `.env` is never copied into the image. A
  service on this machine is reachable as `host.docker.internal`, not `localhost`.
- **First-run time:** the image is ~12 GB unpacked; on a slow line the build took about an hour here.
- **After the first start** no internet is needed: if the image build cannot reach the network, the existing image is
  used.
- **Prompt edits** on the dashboard stay in memory, so `./start.sh stop` or a container restart resets them.

## Run / stop (without Docker)

```powershell
.\run.ps1                 # foreground (Ctrl+C to stop)
.\run.ps1 -Background     # background; PID in logs\server.pid; stdout/stderr in logs\server.out.log / server.err.log
.\run.ps1 -Stop           # stop the background server
```

`run.ps1` loads `.env` if present and starts `uvicorn app.main:app --workers 1` (never `--reload`, never more
workers — each worker would load its own copy of the model). The API answers `/health` immediately; the model loads
in a background thread (~12 s on the dev GPU with 4-bit, longer from a cold disk). Poll `/ready` until 200.

## Monitoring

```powershell
.\scripts\monitor.ps1 -IntervalSeconds 5 -DurationSeconds 120   # one line per check, appended to logs\monitor.log
```

Line format: `UP|STARTING|DOWN model=<bool> status=<loading|loaded|failed> device=… ready=<code> llm_p95<=<s>
queue=<n> gpuMB=<n> uptime=<s>`. Exits 1 after `-MaxFailures` (3) consecutive failures.

With the Docker stack, Grafana is already on port 3001: do not start `ops/` next to it.

Optional: `.\scripts\watchdog.ps1` restarts the server via `run.ps1 -Stop/-Background` after 3 failed `/health`
checks and waits for `/ready` (log: `logs\watchdog.log`). Optional Prometheus + Grafana:
`docker compose -f ops/docker-compose.monitoring.yml up -d` (Prometheus :9090, Grafana :3001).

## Smoke and load tests (server must be running)

```powershell
.\scripts\smoke_test.ps1              # health, ready, 10 core queries via /resolve -> PASS/FAIL table, exit code
python -m app.scripts.load_test       # 20 sequential + 3x5 concurrent /parse; writes "Last load test" below
```

## QA

Lint on edit is automatic in Claude Code via the `PostToolUse` hook in `.claude/settings.json`
(`.claude/hooks/ruff_on_edit.py`, verified working on this Windows setup). Full gates:

```powershell
ruff check app tests
pytest -m "not model" -q      # fast, no model
pytest -m model -q            # real inference (stop the server first if VRAM is tight)
python -m app.scripts.eval_model
```

Slash commands: `/qa`, `/eval`, `/ops-check`, `/release`.

## Logs

| File | Content |
|---|---|
| `logs\app.log` (+ `.1` … `.5`, 10 MB each) | JSON lines: `ts, level, logger, requestId, event, durationMs, …` |
| `logs\server.out.log`, `logs\server.err.log` | raw process output when started with `-Background` |
| `logs\monitor.log`, `logs\watchdog.log` | script output |
| `logs\model-eval-last.json` | raw results of the last eval |

Privacy: every record passes a redaction filter (14-digit runs → `***PINFL***`, standalone 9-digit runs →
`***INN***`). Query text is logged only at DEBUG; INFO logs `queryLength` + `queryHash` (sha256, 12 chars). uvicorn
access logs are disabled (the middleware logs method/route/status/duration only). Keep `LOG_LEVEL=INFO` in production.

## Metrics catalogue (`GET /metrics`)

| Metric | Type | Labels |
|---|---|---|
| `hr_ai_http_requests_total` | counter | method, path (route template), status |
| `hr_ai_http_request_duration_seconds` | histogram | path |
| `hr_ai_llm_inference_duration_seconds` | histogram | — |
| `hr_ai_llm_requests_total` | counter | status = ok \| json_error \| timeout \| error |
| `hr_ai_llm_json_retry_total` | counter | — |
| `hr_ai_llm_queue_depth` | gauge | — |
| `hr_ai_model_loaded` | gauge | — |
| `hr_ai_gpu_memory_used_bytes` | gauge | — |
| `hr_ai_resolver_fields_total` | counter | result = resolved \| unresolved \| candidate |
| `hr_ai_fuzzy_score` | histogram | — |
| `hr_ai_hr_search_requests_total` | counter | status (HTTP code, timeout, error) |
| `hr_ai_intent_corrections_total` | counter | kind = toAge->fromAge \| fromAge->toAge \| specialists->typeOfPosition \| canonical:typeOfPosition \| drop:<field> |

## Suggested alerts (Grafana / Prometheus)

| Alert | Condition |
|---|---|
| Model not loaded | `hr_ai_model_loaded == 0` for 10 min |
| Slow inference | p95 of `hr_ai_llm_inference_duration_seconds` > 20 s for 10 min |
| Queue building up | `hr_ai_llm_queue_depth > 3` for 5 min |
| LLM failures | `rate(hr_ai_llm_requests_total{status!="ok"}[10m]) / rate(hr_ai_llm_requests_total[10m]) > 0.1` |
| HR API errors | `rate(hr_ai_hr_search_requests_total{status!~"2.."}[5m]) > 0` |
| 5xx | `rate(hr_ai_http_requests_total{status=~"5.."}[5m]) > 0` |

## Restart procedure

1. `.\run.ps1 -Stop` (or kill the PID in `logs\server.pid`).
2. Check `nvidia-smi` — VRAM should be released.
3. `.\run.ps1 -Background`, then `.\scripts\monitor.ps1 -DurationSeconds 60` until `UP`.
4. `.\scripts\smoke_test.ps1`.

## `/ready` returns 503 — what to check

1. `GET /health` → `modelStatus`: `loading` → wait; `failed` → `/ready` body `error` has the reason.
2. `logs\app.log`: search for `Model load failed`. Typical causes:
   - *Model at … is incomplete; missing weight files* → restore the listed files in `model\` manually. The service
     never downloads models.
   - CUDA out of memory → another process holds VRAM (`nvidia-smi`); set `MODEL_QUANTIZATION=4bit` or free the GPU.
   - bitsandbytes import error → `MODEL_QUANTIZATION=none` falls back to GPU+CPU offload (slow, ~40 s/query).
3. Confirm exactly one `Model loaded` line per process start (the model is never reloaded per request).

## Last verified run

_Filled in below._

## Last load test

_Generated by `python -m app.scripts.load_test` at 2026-09-30T14:39:18 against http://127.0.0.1:8001._

| Series | Calls | p50 ms | p95 ms | max ms |
|---|---|---|---|---|
| Sequential | 20 | 8404 | 12090 | 12145 |
| Concurrent (3 x 5) | 15 | 28902 | 40572 | 40610 |
| All | 35 | 8530 | 40481 | 40610 |

Errors: **0**. Concurrent calls queue behind the single-model lock, so their latency includes waiting time (expected with one worker).
