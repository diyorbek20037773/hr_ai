# hr_ai — AI Smart Search for the HR Advanced Search

The user types one sentence, for example

> Toshkentda yashaydigan, 30 yoshdan katta, oliy ma'lumotli ayollar

A **local** `Qwen3-4B-Instruct-2507` model extracts the filters that were mentioned. Deterministic code turns them
into the exact `ISearchModel` JSON the existing Advanced Search already sends:

```json
{ "regionId": 14, "sexId": 2, "fromAge": 30, "educationId": 1, "academicTitleId": 0, "first": 0, "rows": 20 }
```

The existing HR Search API runs the actual search. **The AI never searches, ranks or sees personnel records.**
Nothing leaves the machine: no external LLM or embedding API is used. Input languages: Uzbek Latin, Uzbek
Cyrillic, Russian.

```
MANUAL ADVANCED SEARCH  ─┐
                         ├─►  ISearchModel  ─►  existing HR Search API  ─►  SearchResultDTO[] + totalItems
AI SMART SEARCH (this)  ─┘

text ─► QwenLLMService ─► intent_parser (JSON) ─► intent_guards ─► filter_resolver (exact → alias → RapidFuzz → IDs) ─► SearchModel ─► HrSearchClient
```

Details: [docs/architecture.md](docs/architecture.md) · API: [docs/api.md](docs/api.md) ·
Frontend: [docs/frontend-integration.md](docs/frontend-integration.md) · Ops: [docs/operations.md](docs/operations.md) ·
Eval: [docs/model-eval.md](docs/model-eval.md) · Machine: [docs/environment.md](docs/environment.md)

## Layout

```
app/        main.py, api/ (ai_search, health), core/ (config, logging, metrics, security),
            models/ (ai_intent, search, references, responses, enums, labels), prompts/ (search_parser),
            services/ (llm_service, intent_parser, intent_guards, fuzzy_matcher, filter_resolver, reference_provider,
                       search_client),
            scripts/ (test_model, eval_model, load_test), utils/ (text_normalizer, json_parser)
references/ example_references.json (dev data, placeholder IDs)
tests/      unit/, integration/, model/ (marker `model`), eval/golden_queries.jsonl (main), eval/golden_hard.jsonl (challenge)
scripts/    monitor.ps1, smoke_test.ps1, watchdog.ps1          run.ps1 (start/stop)
ops/        optional Prometheus + Grafana compose
model/      Qwen3-4B-Instruct-2507 weights (read only, not in git)
```

## Model location

The service looks for the model in `MODEL_PATH` (if set), else `.\model`, else `.\model\Qwen3-4B-Instruct-2507`.
The directory must contain `config.json`, `tokenizer.json`, `tokenizer_config.json` and every shard listed in
`model.safetensors.index.json`; otherwise startup fails with the list of missing files. **Nothing is ever
downloaded** (`local_files_only=True`).

Device selection (`MODEL_DEVICE=auto`, `MODEL_QUANTIZATION=auto`): GPU in bf16 if ≥ 9 GB VRAM is free; on a smaller
GPU a 4-bit (bitsandbytes NF4) model fully on the GPU; without CUDA the CPU (slow; float32 needs ~16 GB RAM).
On the dev laptop (RTX 3050, 6 GB) that is 4-bit on the GPU: about 3.3 s per query (p50).

O'zbekcha yo'riqnoma: [ISHGA_TUSHIRISH.md](ISHGA_TUSHIRISH.md)

## Quick start with Docker (any computer)

Needs Docker with Compose 2.24+, plus about 30 GB of free disk on the first run (image ~12 GB, model ~8 GB).

```bash
./start.sh          # Linux, macOS
start.bat           # Windows: double-click, or .\start.ps1 in PowerShell
```

The script detects the hardware, builds the image with the matching torch, downloads the model once from Hugging
Face (pinned revision, resumable), checks that the container really sees the GPU, then starts everything and opens
the dashboard. Later runs reuse the image and the model.

| Page | URL |
|---|---|
| Dashboard: query test, prompt viewer/editor | http://127.0.0.1:8001/ |
| Monitoring (Grafana) | http://127.0.0.1:3001/ |
| Swagger | http://127.0.0.1:8001/docs |

Other commands: `stop`, `status`, `logs`; `--cpu` (`-Cpu`) forces CPU; `--download-model` (`-DownloadModel`) ignores a
local `./model`. Details and the hardware table: [docs/operations.md](docs/operations.md#docker).

## Install (without Docker)

```powershell
cd D:\myprojects\hr_ai
.\.venv\Scripts\Activate.ps1
# torch CUDA wheel first (skip if already installed):
pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
copy .env.example .env      # then edit .env (HR API URL, API key, CORS origins …)
```

## Run

```powershell
.\run.ps1 -Background                     # http://127.0.0.1:8001, 1 worker, no reload
Invoke-RestMethod http://127.0.0.1:8001/ready
.\scripts\monitor.ps1 -DurationSeconds 60
.\scripts\smoke_test.ps1
.\run.ps1 -Stop
```

Test page: **http://127.0.0.1:8001/**. Type a sentence and press **Qidirish** to see the filter chips, candidates
and `searchModel`. Press **Prompt (Qwen'ga beriladigan)** to see the system prompt, the few-shots and the exact
chat-template text Qwen receives. You can edit them and apply the change to the running server (memory only, not
evaluated, reset on restart; `PROMPT_EDITING_ENABLED=false` in production).

Try one query without the server: `python -m app.scripts.test_model "Oliy ma'lumotli 30 yoshdan katta ayollar"`.
Swagger UI: http://127.0.0.1:8001/docs

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness: status, modelLoaded, modelStatus, modelPath, device, dtype, promptVersion, uptimeSeconds |
| `GET /ready` | 200 when the model is loaded, 503 otherwise |
| `GET /metrics` | Prometheus metrics (`hr_ai_*`) |
| `POST /api/v1/ai-search/parse` | `{query}` → `{query, intent, promptVersion, latencyMs}` |
| `POST /api/v1/ai-search/resolve` | `{query, referenceData?, first?, rows?}` → intent + `searchModel` + chips + `readyToSearch` |
| `POST /api/v1/ai-search/search` | resolve, then call the HR Search API (`executed:false` when not configured) |
| `GET /api/v1/references` | reference collections currently available to the resolver |
| `GET/PUT/DELETE /api/v1/prompt`, `POST /api/v1/prompt/render` | view / runtime-edit / reset the Qwen prompt; exact rendered text |
| `GET /` | self-contained test page (no CDN) with the prompt viewer/editor |

PowerShell:
```powershell
$body = @{ query = "Samarqand viloyatidagi 25-40 yoshli davlat xizmatchilari" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8001/api/v1/ai-search/resolve -Method Post `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 6
Invoke-RestMethod http://127.0.0.1:8001/api/v1/ai-search/parse -Method Post `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes('{"query":"Familiyasi Karimov, ismi Ali"}'))
Invoke-RestMethod "http://127.0.0.1:8001/api/v1/references?collection=regions"
```

curl:
```bash
curl -s http://127.0.0.1:8001/health
curl -s -X POST http://127.0.0.1:8001/api/v1/ai-search/resolve -H "Content-Type: application/json" \
     -d '{"query":"Женщины старше 30 лет с высшим образованием"}'
curl -s -X POST http://127.0.0.1:8001/api/v1/ai-search/search -H "Content-Type: application/json" \
     -d '{"query":"bo'"'"'lim boshliqlari va direktorlar","rows":50}'
```

If `AI_SEARCH_API_KEY` is set, add `-H "X-API-Key: <key>"` (PowerShell: `-Headers @{ "X-API-Key" = "<key>" }`).

## Reference data and fuzzy matching

IDs come only from reference data: request `referenceData` → remote reference API (`REFERENCE_API_BASE_URL`,
cached) → `references/example_references.json` (placeholder IDs, see [references/README.md](references/README.md)).
Matching per value: exact name → exact alias → RapidFuzz `token_sort_ratio`:

| Score | Result |
|---|---|
| ≥ 88 (`FUZZY_AUTO_MATCH_THRESHOLD`) | resolved, `matchType: "fuzzy"` |
| 75–87 (`FUZZY_CANDIDATE_THRESHOLD`) | unresolved, up to 3 candidates |
| < 75 | unresolved, no candidates |
| runner-up within 3 points (`FUZZY_AMBIGUITY_MARGIN`) | ambiguous → unresolved with both |

Enum and numeric fields are never fuzzy-matched. Why `token_sort_ratio` and not `WRatio`: see the architecture
decision log.

## Frontend

Call `/resolve`, show `recognizedFilters` as removable chips (remove = delete the field from `searchModel`), show
`unresolvedFilters` with a candidate picker, then patch the Advanced Search form or post `searchModel` to the
existing Search API. See [examples/frontend-integration.ts](examples/frontend-integration.ts).

## Tests

```powershell
ruff check app tests
pytest -m "not model" -q          # unit + integration, fake LLM, < 60 s
pytest -m model -q                # real model
python -m app.scripts.eval_model  # main + challenge sets -> docs/model-eval.md (--set main|hard|all)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/ready` 503, `modelStatus: failed`, "incomplete; missing weight files" | restore the listed files in `model\` (never auto-downloaded) |
| `CUDA not available — running on CPU` | install the CUDA torch wheel matching `nvidia-smi`; CPU float32 needs ~16 GB RAM |
| CUDA out of memory | close other GPU apps; `MODEL_QUANTIZATION=4bit`; don't run eval/tests while the server holds the GPU |
| ~40 s per query | you are in GPU+CPU offload (`MODEL_QUANTIZATION=none` on a small GPU) — use `auto`/`4bit` |
| occasional 30–40 s calls on a small GPU | VRAM spilled to Windows shared memory; `nvidia-smi` shows the GPU full. Fixed by releasing the CUDA cache after each call (architecture decision 9); close other GPU apps |
| slow CPU inference | expected; use a GPU; keep one worker |
| `LLM_OUTPUT_INVALID` | model returned non-JSON twice; rephrase; check `hr_ai_llm_requests_total{status="json_error"}` |
| `/search` returns `executed: false` | set `HR_SEARCH_API_URL` (+ path / key) in `.env` |

## Monitoring

`scripts\monitor.ps1` (one-line status, `logs\monitor.log`), `scripts\watchdog.ps1` (auto-restart), Prometheus
metrics at `/metrics`, optional Prometheus + Grafana in `ops/`. Metric catalogue and alert thresholds:
[docs/operations.md](docs/operations.md).
