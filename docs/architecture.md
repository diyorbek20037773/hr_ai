# Architecture

## Overview

```
                          ┌──────────────────────────── hr_ai (this service, 127.0.0.1:8001, 1 worker) ─────────────────────────┐
 "Toshkentda yashaydigan, │                                                                                                      │
  30 yoshdan katta,  ───► │ llm_service.QwenLLMService ──► intent_parser ──► filter_resolver ──► SearchModel ──► search_client  │ ──► existing HR Search API
  oliy ma'lumotli         │  (local Qwen3-4B, loaded once,  (prompt + robust   (exact → alias →    (== ISearchModel)  (httpx)      │      (does the real search)
  ayollar"                │   lock + thread pool)            JSON + 1 retry)    RapidFuzz → IDs)                                  │
                          │                                        ▲                                                             │
                          │                       reference_provider: request payload → remote reference API → local JSON        │
                          └──────────────────────────────────────────────────────────────────────────────────────────────────────┘

MANUAL ADVANCED SEARCH  ─┐
                         ├─►  ISearchModel  ─►  existing HR Search API  ─►  { items: SearchResultDTO[], totalItems }
AI SMART SEARCH (this)  ─┘
```

The AI never searches, ranks, or sees personnel records. It only translates one sentence into filters.

## Two representations (never merged)

| `ParsedSearchIntent` — LLM output (`app/models/ai_intent.py`) | `SearchModel` — HR API contract (`app/models/search.py`) |
|---|---|
| `"region": "Toshkent"` | `"regionId": 14` |
| `"sex": "Ayol"` | `"sexId": 2` |
| `"specialists": ["Iqtisodiyot"]` | `"specialistIds": [1]` |
| `"typeOfService": "CivilServant"` | `"typeOfService": 1` |

The LLM emits **text only**. IDs come exclusively from reference data via the resolver.

## Modules

| Module | Responsibility |
|---|---|
| `app/main.py` | `create_app()`, lifespan (model loads once in a background thread), request-id + metrics middleware, error envelope, CORS |
| `app/api/ai_search.py` | `/api/v1/ai-search/parse`, `/resolve`, `/search`, `/api/v1/references` |
| `app/api/health.py` | `/health`, `/ready`, `/metrics` |
| `app/services/llm_service.py` | `BaseLLMService` protocol, `QwenLLMService` (model discovery + completeness check, device/dtype/quantization choice, greedy generation under a lock, timeout), `FakeLLMService` |
| `app/prompts/search_parser.py` | versioned system prompt + few-shots (`PROMPT_VERSION`) — the committed default |
| `app/prompts/store.py` | active prompt: the default or a memory-only runtime override (`+custom`) |
| `app/api/prompt.py`, `app/api/ui.py` | prompt view/edit/render API; test page at `/` |
| `app/services/intent_parser.py` | messages → LLM → JSON extraction → `ParsedSearchIntent` → guards; one corrective retry; `LLM_OUTPUT_INVALID` |
| `app/services/intent_guards.py` | deterministic post-checks driven by the query text: age direction fix; drop numbers, education, sex, hiring date and position types with no evidence; move position types out of
`specialists` and canonicalise them to enum names (`["Leader","Director"]` -> `["Leader"]`). Codes returned as `corrections` |
| `app/utils/json_parser.py` | conservative JSON extraction (fences, first balanced object, trailing commas) — never evaluates output |
| `app/services/fuzzy_matcher.py` | exact name → exact alias → RapidFuzz, thresholds from config |
| `app/services/filter_resolver.py` | intent → `SearchModel` + `recognizedFilters` / `unresolvedFilters` / `warnings` / `readyToSearch` |
| `app/services/reference_provider.py` | `RequestReferenceProvider`, `RemoteReferenceProvider` (TTL cache), `LocalJsonReferenceProvider`, `CompositeReferenceProvider` |
| `app/services/search_client.py` | `HrSearchClient` → existing HR Search API |
| `app/models/enums.py`, `labels.py` | enum phrase tables, Uzbek/Russian labels for UI chips |
| `app/core/` | config (all thresholds), JSON logging with PII redaction, Prometheus metrics, API-key check |

## Sequences

**`POST /api/v1/ai-search/parse`**: validate query (≤ 1000 chars) → `run_in_threadpool(IntentParser.parse)` →
`QwenLLMService.generate` (lock, greedy, timeout) → `extract_json_object` → `ParsedSearchIntent` (on failure: one
retry with "Return only valid JSON matching the schema.") → `{query, intent, promptVersion, latencyMs}`.

**`POST /api/v1/ai-search/resolve`**: parse (as above) → collections needed by the intent → for each collection the
first provider that has it (request `referenceData` → remote API → local JSON) → `FilterResolver.resolve` →
`{query, intent, searchModel, recognizedFilters, unresolvedFilters, warnings, readyToSearch, latencyMs}`.

**`POST /api/v1/ai-search/search`**: resolve → if `HR_SEARCH_API_URL` empty → `executed:false,
reason:"HR_SEARCH_API_NOT_CONFIGURED"` (HTTP 200); if not `readyToSearch` → `executed:false,
reason:"NOT_READY_TO_SEARCH"`; else POST `searchModel` to the HR API and return `results` untouched.

**Startup**: lifespan starts a `model-loader` thread → `/health` answers immediately with `modelStatus: loading`,
`/ready` is 503 until the single "Model loaded" log line, then 200. A load failure sets `modelStatus: failed` and
`/ready` returns the error.

## Decision log

1. **RapidFuzz, not embeddings.** Reference values are short, closed vocabularies (regions, specialities …) with
   known aliases. Deterministic string similarity + curated aliases is explainable, testable, needs no extra model,
   and keeps the "never guess" guarantee enforceable by thresholds. Embeddings/BGE-M3/vector DBs are out of scope.
2. **Scorer = `token_sort_ratio`** (configurable `FUZZY_SCORER`). Measured on the example references:
   `WRatio` and `token_set_ratio` give ~100 to subset matches, so `"Toshkent vil."` silently resolved to
   *Toshkent shahri* (alias "Toshkent" ⊂ input) and `"moliya va bank"` to *Moliya*. `token_sort_ratio` returns
   those as low-confidence candidates instead, while typos still resolve (`"Toshken"` → Toshkent shahri 93.3,
   `"Chilonzr"` → Chilonzor tumani 94.1). Silent wrong IDs are worse than asking the user.
3. **Thresholds 88 / 75, ambiguity margin 3.** ≥ 88 auto-resolves typos of one or two characters on 7+ letter words;
   75–87 returns up to 3 candidates for the UI picker; if the runner-up is within 3 points of an auto-match the value
   is *ambiguous* and never picked. All in `app/core/config.py`.
4. **No IDs in prompts.** IDs differ per environment and change; putting them in the prompt would make the 4B model
   guess numbers and bloat the prompt. The model outputs names; the resolver is the single source of IDs.
5. **Single worker, model loaded once.** An ~8 GB model cannot be duplicated per worker on this hardware; one
   process holds it, generation is serialised by a lock and runs in a thread pool so the event loop stays free
   (`hr_ai_llm_queue_depth` shows waiting requests). No `--reload` (would reload the model).
6. **4-bit quantization when VRAM is short** (`MODEL_QUANTIZATION=auto`). The dev machine has a 6 GB GPU; bf16 needs
   ~8 GB. GPU+CPU offload worked but took ~40 s per query (weights stream over PCIe every token); float32 on CPU
   needs ~16 GB RAM (machine has 16 GB total). bitsandbytes NF4 keeps the whole model on the GPU (~5 s per query) with
   the same answers on the core scenarios. On a GPU with ≥ 9 GB free the model runs unquantized in bf16.
7. **Background model load.** `/health` must answer during the load so monitors can distinguish "starting" from
   "down"; `/ready` is the gate for traffic.
8. **Deterministic intent guards** (`intent_guards.py`). Prompt iterations showed two failure modes a 4B model does
   not fix reliably by wording alone: the age direction of Uzbek Cyrillic "N ёшдан катта" (sometimes `toAge`), and
   invented values (an age from "ёш", an education from a profession). Guards only use the literal query: they move
   an age to the side its phrase states; drop numeric values whose digits are not in the query, an education with
   no education word, a sex with no gender word and a `dateOfReceipt` with no hiring word; move a position type placed in `specialists` ("mutaxassislar"
   -> "Expert") to `typeOfPosition`; drop a position type with none of its stems in the query ("кадрлар" is not
   "ProductionEmployee"). They never add values. Limitations: numbers written as words ("o'ttiz") are dropped;
   evidence stems are substrings, so e.g. "qiz" inside "Qiziltepa" counts as a gender word (fails safe: keeps).
   Every correction increments `hr_ai_intent_corrections_total{kind}`.
9. **CUDA cache released after every generation** (`torch.cuda.empty_cache()` in `QwenLLMService.generate`). With
   prompts of varying length the PyTorch allocator cache grew from 4.2 GB to 7.4 GB reserved on the 6 GB dev GPU
   (only 2.7 GB actually allocated). Windows (WDDM) then spills silently into shared system memory and single calls
   jump from ~6 s to ~40 s. Releasing the cache keeps reserved memory at ~2.8 GB; measured over the same 50-query
   sequence: p95 34.6 s -> 8.7 s, max 42.3 s -> 10.7 s. Cost: a few ms per call.
