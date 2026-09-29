# hr_ai — Claude Code kickoff prompt

Before pasting: save `CLAUDE.md` to `D:\myprojects\hr_ai\CLAUDE.md`, open PowerShell in
`D:\myprojects\hr_ai`, start `claude`, then paste everything below the line.

---

You are the **technical lead** of a small engineering team building this project inside
`D:\myprojects\hr_ai` on Windows (PowerShell). You will create four subagents in this repository —
`backend-dev`, `qa-engineer`, `ops-engineer`, `code-reviewer` — and delegate to them. You coordinate,
integrate, resolve conflicts, and make final decisions. **You BUILD the project end to end. You do not
stop at a plan, and you do not declare anything done that QA has not verified.**

Read `CLAUDE.md` in the repository root first. It is the permanent constitution of this project. Every
rule there applies to this whole session. If anything in this prompt conflicts with it, tell me and
follow `CLAUDE.md`.

The local model **Qwen3-4B-Instruct-2507 is already fully downloaded** into `D:\myprojects\hr_ai\model`
(either directly there or in `model\Qwen3-4B-Instruct-2507`). Do not download anything model-related.

The deliverable of this session: a working, tested, monitored FastAPI service running on
`http://127.0.0.1:8001`, committed to a local git repository, with documentation, plus a final
engineering report.

---

## 0. Hard rules (summary — full list in CLAUDE.md §3)

- Local AI only. No external LLM/embedding APIs. No BGE-M3, embeddings, vector DB, RAG, fine-tuning.
- Never touch `model/` contents. Never recreate `.venv`. Use the existing venv for everything.
- Model loads once (lifespan). uvicorn: `--workers 1`, no `--reload`.
- Only explicitly stated filters. Never invent. LLM outputs text, resolver assigns IDs.
- RapidFuzz only; low confidence → unresolved + candidates.
- No PINFL/INN/personnel data in logs at INFO+. Secrets only in `.env`.
- Never edit tests or golden expectations to make them pass. Report honestly.
- Commit locally after each phase. Never create/push to a remote unless I give you one.

## 1. Environment you will find

```
D:\myprojects\hr_ai\
├── .venv\                    existing Python venv (use it)
└── model\                    Qwen3-4B-Instruct-2507 safetensors + config + tokenizer
```

Possibly also a partially created `app/` or docs from earlier attempts. Inspect first; keep what is
correct, do not delete blindly.

## 2. Business problem

The HR system has a manual Advanced Search with ~40 filters. A user wanting
"Toshkentda yashaydigan, 30 yoshdan katta, oliy ma'lumotli ayollar" must find and fill region, sex,
min age, education one by one. With more filters (language, certificate, degree, specialization,
experience, institution type…) this is slow.

We add **AI Smart Search**: the user writes one sentence, the local model detects which filters were
requested, our code fills the same `ISearchModel` the Advanced Search already sends, and the existing
HR Search API executes it. The frontend shows the recognized filters as removable chips and the usual
results table.

```
MANUAL ADVANCED SEARCH  ─┐
                         ├─→  ISearchModel  ─→  existing HR Search API  ─→  SearchResultDTO[]
AI SMART SEARCH (this)  ─┘
```

## 3. Existing contract (TypeScript, from the frontend)

```ts
export interface ISearchModel {
    inn?: number | undefined;
    firstName?: string | undefined;
    lastName?: string | undefined;
    middleName?: string | undefined;
    regionId?: number | undefined;
    birthRegionId?: number | undefined;
    districtId?: number | undefined;
    sexId?: number | undefined;
    nationId?: number | undefined;
    languageId?: number | undefined;
    fromAge?: number | undefined;
    toAge?: number | undefined;
    educationId?: number | undefined;
    fromYear?: number | undefined;
    toYear?: number | undefined;
    specialistId?: number | undefined;
    specialistIds?: number[] | undefined;
    educationTypes?: number | undefined;
    eduTypeId?: number | undefined;
    degreeId?: number | undefined;
    academicDegreeId?: number | undefined;
    academicTitleId: number;
    institutionInn?: number | undefined;
    institutionId?: number | undefined;
    institutionName?: string | undefined;
    institutionTypeId?: number[] | undefined;
    institutionGroupId?: number[] | undefined;
    typeOfPosition?: TypeOfPosition[] | undefined;
    spCategoryOfPositionId?: number[] | undefined;
    isAwarded?: boolean | undefined;
    isReserved?: boolean | undefined;
    languageCertificateId?: number | undefined;
    isAcademicDegree?: boolean | undefined;
    isAcademicTitle?: boolean | undefined;
    isGlobalEducation?: boolean | undefined;
    term?: string | undefined;
    pinfl?: string | undefined;
    first: number;
    rows: number;
    experienceYear?: number | undefined;
    dateOfReceipt?: Date | undefined;
    typeOfService?: TypeOfService | undefined;
}

export enum TypeOfPosition {
    All = 0, Leader = 1, Management = 2, Техник = 3, Expert = 4,
    ProductionEmployee = 5, ServiceEmployee = 6,
}

export enum TypeOfService { NotCivilServant = 0, CivilServant = 1 }

export interface SearchResultDTO {
    id: number;
    fullName?: string | undefined;
    pinfl?: string | undefined;
    birthOfDate?: Date | undefined;
    institutionName?: string | undefined;
    dxCode?: string | undefined;
    positionName?: string | undefined;
    tin?: string | undefined;
    soato?: number | undefined;
    tbFileImageId?: number | undefined;
    surname?: string | undefined;
    name?: string | undefined;
    institutionId: number;
}
// Search response: { items?: SearchResultDTO[]; totalItems: number }
```

Mirror this exactly in Pydantic (`app/models/search.py`) with camelCase aliases, `populate_by_name`,
and `exclude_none` on serialization. `academicTitleId` is required upstream → send
`ACADEMIC_TITLE_ID_DEFAULT` (0) when not requested; document this assumption.
`educationTypes` is a single number despite the plural name — keep it as-is.
`dateOfReceipt` is ISO-8601 string on the wire. `pinfl` stays a string (leading zeros).

## 4. Required AI behaviour

Extract **only** what is explicitly stated or linguistically unambiguous. Never invent.

| User writes | Intent |
|---|---|
| `Oliy ma'lumotli, 30 yoshdan katta ayollar` | `{"education":"Oliy","fromAge":30,"sex":"Ayol"}` |
| `Samarqand viloyatidagi 25-40 yoshli davlat xizmatchilari` | `{"region":"Samarqand viloyati","fromAge":25,"toAge":40,"typeOfService":"CivilServant"}` |
| `Toshkentda ishlaydigan, 3 yildan ortiq staji bor iqtisodchilar` | `{"region":"Toshkent","specialists":["Iqtisodiyot"],"experienceYear":3}` |
| `Ilmiy darajasi bor va mukofotlangan xodimlar` | `{"isAcademicDegree":true,"isAwarded":true}` |
| `Олий маълумотли 30 ёшдан катта аёллар` | `{"education":"Oliy","fromAge":30,"sex":"Ayol"}` |
| `Женщины старше 30 лет с высшим образованием` | `{"education":"Oliy","fromAge":30,"sex":"Ayol"}` |
| `Familiyasi Karimov, ismi Ali` | `{"lastName":"Karimov","firstName":"Ali"}` |
| `Chilonzor tumanida yashaydigan, ingliz tilini biladigan, IELTS sertifikati bor yoshlar 35 yoshgacha` | `{"district":"Chilonzor","language":"Ingliz tili","languageCertificate":"IELTS","toAge":35}` |
| `bo'lim boshliqlari va direktorlar` | `{"typeOfPosition":["Leader"]}` |
| `yaxshi kadrlarni top` | `{}` (nothing explicit → empty intent) |

Age phrases: "30 yoshdan katta / 30 ёшдан катта / старше 30" → `fromAge: 30`;
"40 yoshgacha / до 40 лет" → `toAge: 40`; "25-40 yosh", "25 dan 40 gacha", "от 25 до 40" →
both. Experience: "kamida 5 yil / 5 yildan ortiq / стаж от 5 лет" → `experienceYear: 5`.
Normalise semantic values to a canonical Uzbek-Latin form where a canonical form exists
(`sex` → "Erkak"/"Ayol"; `typeOfService` → "CivilServant"/"NotCivilServant";
`typeOfPosition` → enum names), but keep free-text values (region, specialist, institution) as the
user wrote them — the resolver handles spelling.

## 5. Enum phrase mapping (deterministic, in `app/models/enums.py`)

```
Leader(1):             rahbar, boshliq, direktor, bo'lim boshlig'i, raxbar, руководитель, начальник, директор
Management(2):         boshqaruv xodimi, menejer, менеджер, управленческий персонал
Technic(3):            texnik, техник, technician        (TS name is Техник; Python name Technic; value 3)
Expert(4):             mutaxassis, ekspert, yetakchi mutaxassis, специалист, эксперт
ProductionEmployee(5): ishlab chiqarish xodimi, производственный персонал
ServiceEmployee(6):    xizmat ko'rsatuvchi xodim, обслуживающий персонал
CivilServant(1):       davlat xizmatchisi, davlat fuqarolik xizmatchisi, госслужащий, государственный служащий
NotCivilServant(0):    davlat xizmatchisi bo'lmagan, не госслужащий
```

The LLM may return these as enum names; the resolver also accepts the phrases above via
exact/alias match. Never fuzzy-match numeric or boolean fields.

## 6. Reference data

Fields resolved through reference collections: `regionId, birthRegionId, districtId, sexId, nationId,
languageId, educationId, specialistId/specialistIds, eduTypeId, degreeId, academicDegreeId,
academicTitleId, institutionId, institutionTypeId, institutionGroupId, spCategoryOfPositionId,
languageCertificateId`.

Reference item shape: `{"id": 14, "name": "Toshkent shahri", "aliases": ["Toshkent","Tashkent","Тошкент","Ташкент"]}`
(aliases optional).

Collection keys (used in request payload, local JSON and remote API): `regions, districts, sexes,
nations, languages, educations, specialists, eduTypes, degrees, academicDegrees, academicTitles,
institutions, institutionTypes, institutionGroups, positionCategories, languageCertificates`.

Build `ReferenceProvider` (protocol) with:
- `RequestReferenceProvider` — collections passed in the request body (`referenceData`), highest priority;
- `RemoteReferenceProvider` — `REFERENCE_API_BASE_URL` + per-collection paths, httpx, in-memory TTL cache;
- `LocalJsonReferenceProvider` — `references/example_references.json` for dev/tests;
- `CompositeReferenceProvider` — request → remote → local, per collection.

Create `references/example_references.json` with realistic Uzbek data: all 14 regions of Uzbekistan +
Toshkent shahri with Latin/Cyrillic/Russian aliases, ~15 Toshkent districts, sexes, ~8 nations,
~8 languages, educations (Oliy, O'rta maxsus, O'rta, Boshlang'ich…), ~30 specialists
(Iqtisodiyot, Moliya, Huquqshunoslik, Axborot texnologiyalari, Transport, Qurilish, Tibbiyot…),
academic degrees (Fan nomzodi, Fan doktori, PhD, DSc), academic titles (Dotsent, Professor),
language certificates (IELTS, TOEFL, CEFR B2, CEFR C1…), institution types, position categories.
IDs are placeholders and must be documented as such in `references/README.md`.

## 7. Fuzzy matching (RapidFuzz)

Pipeline per value: normalised exact match on `name` → exact match on `aliases` → RapidFuzz
(`WRatio`, evaluate `token_set_ratio` too and pick the better one in tests) against names+aliases →
decision:

```
score >= FUZZY_AUTO_MATCH_THRESHOLD (88)   → resolved, matchType "fuzzy"
FUZZY_CANDIDATE_THRESHOLD (75) .. 87       → unresolved, top-3 candidates returned, warning
score <  FUZZY_CANDIDATE_THRESHOLD         → unresolved, no candidates
```

If two candidates are within 3 points of each other above the auto threshold → ambiguous →
unresolved with both candidates (never pick randomly). Thresholds only in config.

Normalisation (`app/utils/text_normalizer.py`): trim, collapse spaces, casefold, unify apostrophes
(`'`, `` ` ``, `’`, `ʻ`, `ʼ`, `‘`) to `'`, strip trailing punctuation. No destructive transliteration —
Cyrillic/Latin variants are handled via aliases. Keep original strings for display.

## 8. Direct fields (no dictionary)

`inn, firstName, lastName, middleName, fromAge, toAge, fromYear, toYear, institutionInn,
institutionName, isAwarded, isReserved, isAcademicDegree, isAcademicTitle, isGlobalEducation, term,
pinfl, experienceYear, dateOfReceipt`.

Validation: `0 <= fromAge <= toAge <= 120`; `1900 <= fromYear <= toYear <= current year`;
`experienceYear 0..60`; `pinfl` 14 digits string; `inn` 9 digits. Violations → `warnings` +
`readyToSearch: false` (do not drop silently).

`term` is for free text that has no structured field ("kiberxavfsizlik bilan shug'ullangan") — use it
only when no other field applies, and never duplicate a value that already went into a structured field.

## 9. LLM service (`app/services/llm_service.py`)

- `BaseLLMService` protocol: `generate(messages) -> str`, `is_loaded`, `device`, `model_path`.
- `QwenLLMService`:
  - auto-detect model dir: check `MODEL_PATH` if set, else `model/`, else `model/Qwen3-4B-Instruct-2507`;
    the winner contains `config.json` + tokenizer files; verify every file in
    `model.safetensors.index.json` `weight_map` exists — if not, fail startup with a clear message
    listing missing files (do NOT download).
  - `transformers >= 4.51` is required for Qwen3 — verify installed version, upgrade only if needed.
  - `AutoTokenizer` / `AutoModelForCausalLM`, `local_files_only=True`, loaded once.
  - Device: `MODEL_DEVICE=auto|cuda|cpu`. `auto` → CUDA if available **and** free VRAM ≥ 9 GB, else
    CPU with a WARNING. Dtype: `MODEL_DTYPE=auto|bfloat16|float16|float32` (auto → bf16 on CUDA,
    float32 on CPU). Use `dtype=` if the installed transformers supports it, else `torch_dtype=`.
  - `tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)`. This model
    is the non-thinking 2507 Instruct variant — do not pass `enable_thinking` unless the template
    accepts it; never expose reasoning.
  - Deterministic: `do_sample=False`, `max_new_tokens=MODEL_MAX_NEW_TOKENS` (512),
    `repetition_penalty` configurable (1.05), `pad_token_id=eos`. Decode only new tokens.
  - A `threading.Lock` around generation; called via `run_in_threadpool` from async endpoints;
    `LLM_TIMEOUT_SECONDS` (60) enforced; queue depth exposed as a metric.
  - Startup logs: "Detecting local Qwen model…", path, device, dtype, "Loading tokenizer…",
    "Loading model…", load seconds, VRAM used. `torch.set_num_threads` sensibly on CPU.
- `FakeLLMService` (in `app/services/llm_service.py` or `tests/`) returning canned JSON for tests.

## 10. System prompt (`app/prompts/search_parser.py`)

Separate, versioned module (`PROMPT_VERSION = "1"`, bump on change). The prompt tells the model:
it is a search-query parser for an HR advanced-search system; convert the request into JSON
matching the given schema; extract only explicit criteria; never invent filters, IDs, or values;
omit unmentioned fields; understand Uzbek Latin, Uzbek Cyrillic, Russian; preserve names exactly;
convert age/experience/year ranges as in §4; output **JSON only**, no prose, no markdown fences.
Include the compact JSON schema (field names + types) and 8–10 few-shot pairs covering all three
languages, ranges, booleans, position/service enums, names, and the empty-intent case.
Keep the prompt short (this is a 4B model): target < 1,800 tokens total.

## 11. Robust JSON (`app/utils/json_parser.py`, `app/services/intent_parser.py`)

Try `json.loads` → strip markdown fences → extract first balanced `{…}` → repair trailing commas /
single quotes conservatively → validate via `ParsedSearchIntent` (Pydantic, `extra="ignore"`, coerce
numeric strings, normalise booleans). On failure: **one** retry with a shorter corrective user message
("Return only valid JSON matching the schema."). Still failing → structured 422-style error
`{"error":"LLM_OUTPUT_INVALID"}`, counted in metrics. Never execute anything the model emits.

## 12. Filter resolver (`app/services/filter_resolver.py`)

`resolve(intent, references, first, rows) -> ResolveResult` with:
- `searchModel: SearchModel`
- `recognizedFilters: [{field, label, labelRu, displayValue, value, matchType: exact|alias|fuzzy|direct|enum, score?}]`
- `unresolvedFilters: [{field, label, inputValue, reason: no_match|ambiguous|low_confidence|validation, candidates: [{id,name,score}]}]`
- `warnings: [str]`
- `readyToSearch: bool` (true only if zero unresolved and zero validation errors)

Uzbek/Russian labels in `app/models/labels.py`, e.g. `regionId → "Hudud" / "Регион"`,
`sexId → "Jinsi"`, `educationId → "Ma'lumoti"`, `fromAge → "Yoshi (dan)"`, `toAge → "Yoshi (gacha)"`,
`specialistIds → "Mutaxassisligi"`, `typeOfPosition → "Lavozim turi"`, `typeOfService → "Xizmat turi"`,
`experienceYear → "Ish staji (yil)"`, `isAwarded → "Mukofotlangan"`, `isReserved → "Zaxirada"`,
`isAcademicDegree → "Ilmiy darajasi bor"`, `pinfl → "JSHSHIR"`, `inn → "STIR"`, etc. for every field.
Single `specialist` → `specialistIds` (list). Multiple `typeOfPosition` → list of ints.

## 13. API (FastAPI, `app/api/`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness: `{status, modelLoaded, modelPath, device, dtype, promptVersion, uptimeSeconds}` |
| `GET /ready` | 200 only when model loaded; 503 otherwise |
| `GET /metrics` | Prometheus text format (`prometheus-client`) |
| `POST /api/v1/ai-search/parse` | `{query}` → `{query, intent, promptVersion, latencyMs}` |
| `POST /api/v1/ai-search/resolve` | `{query, referenceData?, first?, rows?}` → `{query, intent, searchModel, recognizedFilters, unresolvedFilters, warnings, readyToSearch, latencyMs}` |
| `POST /api/v1/ai-search/search` | resolve, then if `readyToSearch` and `HR_SEARCH_API_URL` set → call HR API, return `{…resolve fields, executed: true, results: {items, totalItems}}`; if URL empty → `executed: false, reason: "HR_SEARCH_API_NOT_CONFIGURED"` (200, not an error) |
| `GET /api/v1/references` | collections currently available to the resolver (names/ids, for the frontend) |

Also: request-id middleware (`X-Request-ID` echoed), `CORS_ALLOWED_ORIGINS` (default localhost dev
origins), optional `AI_SEARCH_API_KEY` header check (disabled when empty), consistent error envelope
`{"error": {"code", "message", "requestId"}}`, request size limit for `query` (≤ 1,000 chars),
`referenceData` size limit. `HrSearchClient` uses httpx with `HR_SEARCH_API_TIMEOUT`, base URL +
path + optional bearer/API-key header from config; never hardcoded. Swagger at `/docs`.

## 14. Observability (`app/core/logging.py`, `app/core/metrics.py`)

- JSON structured logs (one line per event) to console **and** `logs/app.log` (RotatingFileHandler,
  10 MB × 5). Fields: `ts, level, logger, requestId, event, durationMs, …`.
- Redaction filter: any 14-digit run → `***PINFL***`, any standalone 9-digit run → `***INN***`,
  applied to every record before output. Raw `query` text only at DEBUG; INFO logs
  `queryLength` and `queryHash` (sha256 first 12 chars).
- Metrics (all prefixed `hr_ai_`): `http_requests_total{method,path,status}`,
  `http_request_duration_seconds{path}`, `llm_inference_duration_seconds`,
  `llm_requests_total{status=ok|json_error|timeout|error}`, `llm_json_retry_total`,
  `llm_queue_depth`, `model_loaded`, `gpu_memory_used_bytes` (when CUDA),
  `resolver_fields_total{result=resolved|unresolved|candidate}`, `fuzzy_score` histogram,
  `hr_search_requests_total{status}`.

## 15. Ops scripts

- `run.ps1` — activate venv if needed, load `.env` if present, `uvicorn app.main:app --host
  $env:APP_HOST --port $env:APP_PORT --workers 1 --log-level info` (no reload). `-Background` switch
  starts it with `Start-Process` and writes the PID to `logs/server.pid`; `-Stop` kills it.
- `scripts/monitor.ps1` — every `-IntervalSeconds` (5) hit `/health`, `/ready`, `/metrics`; print a
  one-line status (UP/DOWN, model loaded, p95 latency parsed from metrics, queue depth, GPU MB);
  append to `logs/monitor.log`; exit non-zero after `-MaxFailures` (3) consecutive failures.
- `scripts/smoke_test.ps1` — against the running server: health, ready, the 10 core queries through
  `/resolve` with `references/example_references.json` as `referenceData`, assert `readyToSearch`
  and the expected IDs; print PASS/FAIL table; exit code reflects result.
- `scripts/watchdog.ps1` (optional) — restart via `run.ps1 -Background` when monitor reports DOWN.
- `app/scripts/load_test.py` — 20 sequential + 3×5 concurrent `/parse` calls; print p50/p95/max
  latency and error count; write to `docs/operations.md` section "Last load test".
- `ops/docker-compose.monitoring.yml` + `ops/prometheus.yml` — optional Prometheus + Grafana scraping
  `host.docker.internal:8001/metrics`. Not required for DoD; do not run Docker if it is not installed.

## 16. Team setup (do this in Phase 1, before feature work)

Create `.claude/agents/*.md` with frontmatter (`name`, `description`, `tools`) and a system prompt
that starts with "Read CLAUDE.md first" and states the ownership/restrictions from CLAUDE.md §6:

- `backend-dev` — tools: Read, Edit, Write, Bash, Grep, Glob. Implements per spec; runs
  `ruff check` + `pytest -m "not model" -q` before reporting; reports files changed + test output.
- `qa-engineer` — tools: Read, Write, Edit, Bash, Grep, Glob, restricted by instruction to `tests/` and
  `docs/model-eval.md`. Writes tests from the spec it is given, runs them, returns a verdict
  `PASS` / `FAIL` with the exact command and output, and a bug list with reproduction steps.
  Never edits `app/`. Never trusts dev-written tests as proof.
- `ops-engineer` — tools: Read, Edit, Write, Bash, Grep, Glob. Owns env, deps, scripts, logging,
  metrics, git. Verifies startup, health, monitor, smoke, load test on the real machine.
- `code-reviewer` — tools: Read, Grep, Glob, Bash (for `git diff` only). Reviews for: privacy leaks in
  logs, hardcoded paths/URLs/secrets, model re-loading, contract mismatches with `ISearchModel`,
  duplicated code, silent fuzzy guesses, invented defaults, missing tests. Returns a numbered list
  of findings with severity.

Create `.claude/commands/` (or `.claude/skills/<name>/SKILL.md` if that is what your version uses):
`qa.md` (lint + fast tests + summary), `eval.md` (golden eval + diff vs previous), `ops-check.md`
(start server if down, health/ready/metrics, smoke test, report), `release.md` (qa + eval + ops-check
+ conventional commit + tag).

`.claude/settings.json`: a `PostToolUse` hook on `Edit|Write` running `ruff check` on changed `.py`
files — **test that it actually executes on this Windows setup; if it does not, remove it** and note
in `docs/operations.md` that QA is run via `/qa`. Do not add hooks that run pytest on every edit.

## 17. Configuration (`app/core/config.py`, pydantic-settings, `.env.example`)

```
APP_HOST=127.0.0.1            APP_PORT=8001              LOG_LEVEL=INFO
MODEL_PATH=                   MODEL_DEVICE=auto          MODEL_DTYPE=auto
MODEL_MAX_NEW_TOKENS=512      MODEL_REPETITION_PENALTY=1.05   LLM_TIMEOUT_SECONDS=60
DEFAULT_SEARCH_ROWS=20        ACADEMIC_TITLE_ID_DEFAULT=0
FUZZY_AUTO_MATCH_THRESHOLD=88 FUZZY_CANDIDATE_THRESHOLD=75   FUZZY_AMBIGUITY_MARGIN=3
HR_SEARCH_API_URL=            HR_SEARCH_API_PATH=/api/search   HR_SEARCH_API_KEY=   HR_SEARCH_API_TIMEOUT=30
REFERENCE_API_BASE_URL=       REFERENCE_CACHE_TTL_SECONDS=3600
AI_SEARCH_API_KEY=            CORS_ALLOWED_ORIGINS=http://localhost:4200,http://localhost:3000
LOCAL_REFERENCES_PATH=references/example_references.json
```

## 18. Dependencies

Inspect the venv first (`pip list`, `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`,
`nvidia-smi`). Keep a working CUDA torch. If torch is missing, install the CUDA wheel matching the
driver reported by `nvidia-smi` (CPU wheel only if no NVIDIA GPU). Then: `fastapi uvicorn[standard]
pydantic pydantic-settings transformers>=4.51 accelerate safetensors rapidfuzz httpx python-dotenv
prometheus-client pytest pytest-asyncio ruff`. Pin versions in `requirements.txt` from the resolved
environment. Nothing heavier.

## 19. Tests

Registered markers in `pyproject.toml`: `model`, `integration`. Layout per CLAUDE.md §4.

**Unit (no model):** normalizer (apostrophes, spaces, case); fuzzy matcher (exact, alias, typo
"Toshken"→"Toshkent shahri", low score → unresolved, ambiguity → unresolved with 2 candidates,
thresholds from config); enums (all phrases → correct ints, value 3 for Technic); resolver (10 core
intents → exact `SearchModel`, `academicTitleId` default, `exclude_none`, camelCase aliases,
specialist → `specialistIds`, validation warnings for `fromAge > toAge`, empty intent → empty
model + `readyToSearch: true` with a warning "no filters"); JSON parser (fenced, prefixed with text,
trailing commas, garbage → error after one retry); logging redaction; `SearchModel` round-trip to the
exact TypeScript field set.

**Integration (TestClient + FakeLLMService):** every endpoint, request-id echo, API key on/off,
`/search` with URL empty → `executed: false`, `/search` with a mocked httpx HR API → results passed
through untouched, error envelope shape, `/metrics` contains `hr_ai_` series after a request.

**Model (`-m model`):** model loads once per session (fixture scope session); the 10 core queries
produce the expected core fields; empty-intent query produces `{}`; output is valid JSON on first
attempt for ≥ 9/10.

**Golden eval:** `tests/eval/golden_queries.jsonl`, ≥ 20 entries `{query, expectedIntent,
coreFields, lang, tags}`. `app/scripts/eval_model.py` runs all, compares field-by-field
(case-insensitive on strings, exact on numbers/bools/enums; extra invented fields = FAIL), writes
`docs/model-eval.md` with a table (query, lang, expected, got, core PASS/FAIL, full PASS/FAIL,
latency ms) and a summary (core accuracy, full accuracy, invented-field count, p50/p95 latency,
prompt version, device).

**Targets:** 10/10 core scenarios pass on core fields; ≥ 85 % full-match overall; 0 invented
fields on negative queries. Prompt iteration loop: max 5 iterations (edit few-shots/wording, bump
`PROMPT_VERSION`, re-run). If still below target, stop and report the failing cases — never edit
expectations, never lower thresholds.

## 20. Documentation

- `README.md` — what/why, architecture diagram, layout, model location + detection, install, run,
  every endpoint with PowerShell `Invoke-RestMethod` and `curl` examples, reference data, fuzzy
  matching, frontend integration summary, troubleshooting (CUDA/CPU, VRAM, slow CPU inference,
  model incomplete), monitoring.
- `docs/architecture.md` — diagram, the two-representation principle, sequence per endpoint,
  decision log (why RapidFuzz not embeddings, why IDs are not in prompts, threshold choice, single
  worker).
- `docs/api.md` — full request/response schemas with examples, error codes.
- `docs/frontend-integration.md` + `examples/frontend-integration.ts` — how the existing Angular/TS
  frontend calls `/resolve`, renders `recognizedFilters` as chips, removes a chip by deleting the
  field from `searchModel`, handles `unresolvedFilters` (candidate picker), and then either
  populates the Advanced Search form or posts `searchModel` to the existing Search API.
- `docs/operations.md` — run/stop/monitor/smoke/load/watchdog, log locations, metrics catalogue,
  alert thresholds to set in Grafana, restart procedure, what to check when `/ready` is 503.
- `docs/environment.md` — Phase 0 findings; `docs/model-eval.md` — generated by eval.

## 21. Phases and QA gates — execute in order, commit after each gate

**Phase 0 — Inspect (lead, no changes).** Directory tree; Python version; `pip list`; torch + CUDA;
`nvidia-smi`; model dir detection and completeness check against `model.safetensors.index.json`;
disk free; `git status`. Write `docs/environment.md`. If the model is incomplete → tell me and stop
model-dependent phases (still build everything else).

**Phase 1 — Team & repo scaffolding (lead + ops-engineer).** `.claude/` agents, commands,
settings; `.gitignore`; `git init` if needed; `pyproject.toml` (ruff + pytest config, markers);
`requirements.txt` after venv inspection; `.env.example`; package skeleton with `__init__.py`;
README skeleton. Gate: `python -c "import app"`, `pytest --collect-only`, `ruff check` clean.
Commit `chore: scaffold project, team agents, tooling`.

**Phase 2 — Deterministic core (backend-dev → qa-engineer → code-reviewer).** config, enums,
labels, Pydantic models (intent, search, references, responses), normalizer, fuzzy matcher, reference
providers + example JSON, filter resolver. Gate: QA unit tests PASS; reviewer findings fixed.
Commit `feat: resolver core, fuzzy matching, reference providers`.

**Phase 3 — LLM layer (backend-dev → qa-engineer → ops-engineer).** `BaseLLMService`,
`QwenLLMService`, `FakeLLMService`, prompt module, intent parser, JSON utils,
`app/scripts/test_model.py`. Gate: QA unit tests with fake LLM PASS; ops loads the real model once
on this machine and records load time / device / VRAM in `docs/environment.md`.
Commit `feat: local Qwen intent extraction`.

**Phase 4 — API (backend-dev → qa-engineer → code-reviewer).** FastAPI app, lifespan, routers,
middleware, security, `HrSearchClient`, error envelope. Gate: QA integration tests PASS.
Commit `feat: ai-search API`.

**Phase 5 — Observability & ops (ops-engineer → qa-engineer).** logging + redaction, metrics,
`run.ps1`, `monitor.ps1`, `smoke_test.ps1`, `watchdog.ps1`, `load_test.py`, `docs/operations.md`,
optional `ops/` compose. Gate: redaction tests PASS; `/metrics` test PASS; `run.ps1 -Background`
starts and `/ready` returns 200 within the model load time.
Commit `ops: logging, metrics, run/monitor/smoke scripts`.

**Phase 6 — Real model evaluation (qa-engineer runs, backend-dev iterates prompt).** `pytest -m
model`, then `python -m app.scripts.eval_model`. Iterate on the prompt (max 5) until targets in §19
are met or report the gap. Gate: `docs/model-eval.md` shows targets met (or an honest gap report).
Commit `feat(prompt): vN — eval core X/10, full Y%`.

**Phase 7 — Run, monitor, smoke, load on localhost (ops-engineer).** `run.ps1 -Background` →
`monitor.ps1` for ≥ 2 minutes → `smoke_test.ps1` → `load_test.py`. Paste real outputs into
`docs/operations.md` ("Last verified run"). Gate: smoke PASS, monitor 0 failures, load test 0 errors.
Commit `ops: verified local run`.

**Phase 8 — Hardening (code-reviewer → backend-dev → qa-engineer).** Full-repo review: duplicated
code, hardcoded paths, sensitive logging, error handling, unnecessary deps, model reloads, fuzzy
risks, contract drift vs §3, docs in sync. Fix; full `pytest -q` (all markers) green. Commit
`chore: hardening`, tag `v0.1.0`.

**Phase 9 — Final report (lead).** See §23.

Between phases, send me a 3–5 line status: phase, gate result (command + pass/fail counts),
commit hash, next phase. Do not wait for my approval unless a decision is irreversible or the spec is
genuinely contradictory — then ask one precise question and continue with everything else.

## 22. Definition of done (verified by qa-engineer and ops-engineer, not self-declared)

1. `pytest -m "not model" -q` green, < 60 s.
2. `pytest -m model -q` green on this machine.
3. `docs/model-eval.md` present with targets met or an explicit gap report.
4. `.\run.ps1 -Background` → `/ready` 200; server currently running on `http://127.0.0.1:8001`.
5. `scripts\monitor.ps1` ran ≥ 2 min with 0 failures; `scripts\smoke_test.ps1` PASS; load test 0 errors.
6. `/parse`, `/resolve`, `/search`, `/references`, `/health`, `/ready`, `/metrics` all respond per §13.
7. `/search` with `HR_SEARCH_API_URL` empty returns `executed: false`, not an error.
8. Logs redact PINFL/INN; no query text at INFO.
9. Model loaded exactly once (log shows one "Model loaded" per process); single uvicorn worker.
10. No external LLM/embedding dependency in `requirements.txt` or code.
11. `.claude/agents/` (4), `.claude/commands/` (4) exist and were actually used during this session.
12. Git: clean tree, conventional commits per phase, tag `v0.1.0`, `model/ .venv/ logs/ .env` never committed.
13. `CLAUDE.md`, `README.md`, `docs/*` consistent with the code.

## 23. Final report format

1. Files created / modified (grouped by folder).
2. Final architecture (one diagram) and any deviation from this prompt, with reasons.
3. Environment: Python, torch, CUDA yes/no, GPU model + VRAM, model path detected, load time,
   dtype/device used, packages installed (with versions).
4. Test results: unit/integration counts, model tests, eval core X/10, full Y %, p50/p95 latency.
5. Ops results: monitor summary, smoke table, load test numbers, server PID/URL.
6. Git log (one line per commit) and tag.
7. Exact commands: start server, stop server, monitor, smoke, run one query, run eval.
8. What I still need to provide: real HR Search API URL/path/auth and response sample; real
   reference API endpoints or a dump of the dropdown values; frontend origin(s) for CORS; whether
   `academicTitleId = 0` is correct; a git remote URL if I want pushes.
9. Known limitations and recommended next steps (e.g. conversational refinement of a previous intent
   via `previousIntent` in `/resolve`, reference cache warm-up, GPU quantization if VRAM is short).

## 24. Non-goals for this version

BGE-M3 / embeddings / vector DB / RAG / FAISS / Chroma, external LLM APIs, fine-tuning, a full
frontend app, Docker as a requirement, multi-worker serving, conversational multi-turn refinement
(document it as the next step only).

Start now with Phase 0.
