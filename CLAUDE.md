# CLAUDE.md — hr_ai

> Permanent project instructions for Claude Code. Read this file fully before any change.
> If a task prompt conflicts with this file, THIS FILE WINS — say so, then follow this file.

## 1. What this project is

`hr_ai` is an **AI smart-search layer** for an existing HR / personnel system that already has a
manual "Advanced Search" form with ~40 filters (region, sex, age range, education, specialization,
position type, civil-servant status, awards, academic degree, experience years, …).

A user types one sentence:

> Toshkentda yashaydigan, 30 yoshdan katta, oliy ma'lumotli ayollar

A **local** `Qwen3-4B-Instruct-2507` model extracts which filters were mentioned. Deterministic code
converts them into the exact `ISearchModel` JSON that the existing Advanced Search API already
accepts. The existing HR Search API does the actual search.

**The AI never searches, ranks, or sees personnel records. It only translates natural language into
filters.** Supported input languages: Uzbek Latin, Uzbek Cyrillic, Russian.

Manual Advanced Search and AI Smart Search are two entry points that converge on the same
`ISearchModel`. The manual path must keep working untouched.

## 2. Architecture (change only together with `docs/architecture.md`)

```
User text
  → app/services/llm_service.py      QwenLLMService — local model, loaded ONCE per process
  → app/services/intent_parser.py    system prompt + robust JSON parsing → ParsedSearchIntent
  → app/services/filter_resolver.py  exact → alias → RapidFuzz → numeric IDs / enums
  → app/models/search.py             SearchModel  (Python twin of ISearchModel)
  → app/services/search_client.py    HrSearchClient → existing HR Search API (httpx)
  → SearchResultDTO[] + totalItems
```

Two representations that must never be merged:

| `ParsedSearchIntent` (what the LLM outputs) | `SearchModel` (what the HR API accepts) |
|---|---|
| `"region": "Toshkent"`                      | `"regionId": 14`                        |
| `"sex": "Ayol"`                             | `"sexId": 2`                            |
| `"specialists": ["Iqtisodiyot"]`            | `"specialistIds": [25]`                 |
| `"typeOfService": "CivilServant"`           | `"typeOfService": 1`                    |

The LLM outputs **text values only**. It never outputs IDs. Resolver code assigns IDs from reference
data (request payload → remote reference API → local JSON, via `ReferenceProvider`).

## 3. Non-negotiable rules

1. **Local AI only.** Never call OpenAI, Anthropic, Gemini, or any remote LLM/embedding API. Never send
   queries or personnel data off this machine. `local_files_only=True` on every `from_pretrained`.
2. **Never download, re-download, move, rename, or modify anything under `model/`.** If the model looks
   incomplete, report it — do not fix it by downloading.
3. **Never recreate or delete `.venv`.** Always use `D:\myprojects\hr_ai\.venv`. Check what is
   installed before installing; never downgrade or replace a working CUDA PyTorch.
4. **Model loads once** in FastAPI `lifespan`. **uvicorn runs with exactly `--workers 1` and no
   `--reload`.** Never load the model per request. Inference runs behind a lock in a thread pool so the
   event loop is never blocked.
5. **Extract only what the user explicitly said.** Never invent, default, or "helpfully" add a filter the
   user did not mention. "yaxshi kadrlarni top" must produce an empty intent, not guesses.
6. **LLM extracts meaning; resolver assigns IDs.** No ID lists in prompts, no ID guessing by the model.
7. **Fuzzy matching = RapidFuzz only.** Below `FUZZY_AUTO_MATCH_THRESHOLD` a value is *unresolved* and
   returned with candidates — never a silent guess. Thresholds live in `app/core/config.py`, nowhere else.
8. **No embeddings, BGE-M3, sentence-transformers, FAISS/Chroma, RAG, fine-tuning** in this version.
9. **Privacy.** Never log full PINFL / INN / personnel records at INFO or above. All logging goes through
   `app/core/logging.py`, which redacts 14-digit and 9-digit identifiers. Raw query text is DEBUG-only;
   INFO logs length + hash.
10. **Secrets only in `.env`** (gitignored). `.env.example` is the committed template. No credentials,
    tokens, or real HR URLs in source.
11. **`SearchModel` is the shared contract.** Field names and JSON aliases must match `ISearchModel`
    exactly (camelCase). Optional `None` fields are excluded from outgoing JSON.
12. **Never commit** `model/`, `.venv/`, `logs/`, `.env`, `__pycache__/`, `.pytest_cache/`.
13. **Never lower a quality bar to make something pass.** Do not edit expected values in tests or the
    golden eval set to match model output. If a target is not reachable, report it honestly.

## 4. Repository layout

```
hr_ai/
├── .claude/
│   ├── agents/          backend-dev.md, qa-engineer.md, ops-engineer.md, code-reviewer.md
│   ├── commands/        qa.md, ops-check.md, eval.md, release.md
│   └── settings.json    hooks (optional; must work on Windows or be removed)
├── .venv/               existing Python venv — DO NOT TOUCH structure
├── model/               Qwen3-4B-Instruct-2507 — READ ONLY
├── app/
│   ├── main.py          FastAPI app, lifespan (model load), middleware, routers
│   ├── api/             ai_search.py (parse/resolve/search), health.py (health/ready/metrics)
│   ├── core/            config.py, logging.py, metrics.py, security.py
│   ├── models/          ai_intent.py, search.py, references.py, responses.py, enums.py, labels.py
│   ├── prompts/         search_parser.py (system prompt + few-shots)
│   ├── services/        llm_service.py, intent_parser.py, fuzzy_matcher.py,
│   │                    filter_resolver.py, reference_provider.py, search_client.py
│   ├── scripts/         test_model.py, eval_model.py, load_test.py
│   └── utils/           text_normalizer.py, json_parser.py
├── references/          example_references.json (dev data), README.md
├── tests/
│   ├── unit/            no model, fast
│   ├── integration/     FastAPI TestClient + FakeLLMService
│   ├── model/           real inference, marker `model`
│   └── eval/            golden_queries.jsonl
├── scripts/             monitor.ps1, smoke_test.ps1, watchdog.ps1
├── ops/                 docker-compose.monitoring.yml, prometheus.yml (optional, not required)
├── docs/                architecture.md, api.md, frontend-integration.md, operations.md,
│                        model-eval.md, environment.md
├── examples/            frontend-integration.ts
├── logs/                gitignored
├── .env.example  .gitignore  pyproject.toml  requirements.txt  run.ps1  README.md  CLAUDE.md
```

## 5. Commands (PowerShell, from `D:\myprojects\hr_ai`)

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

ruff check app tests                        # lint
pytest -m "not model" -q                    # fast suite, no model (< 60 s)
pytest -m model -q                          # real inference tests
python -m app.scripts.eval_model            # golden eval → docs/model-eval.md
python -m app.scripts.test_model "Oliy ma'lumotli 30 yoshdan katta ayollar"

.\run.ps1                                   # API on http://127.0.0.1:8001 (1 worker, no reload)
.\scripts\monitor.ps1                       # poll /health + /metrics
.\scripts\smoke_test.ps1                    # end-to-end against the running server
python -m app.scripts.load_test             # latency p50/p95 against the running server
```

## 6. Team workflow (subagents in `.claude/agents/`)

| Agent | Owns | May edit | Never |
|---|---|---|---|
| `backend-dev`   | features, bug fixes | `app/`, `references/`, `examples/`, `docs/api.md`, `docs/architecture.md` | edits `tests/` to make a test pass |
| `qa-engineer`   | tests, golden eval, verdicts | `tests/`, `docs/model-eval.md` | edits `app/`; trusts dev's own tests |
| `ops-engineer`  | env, deps, run/monitor scripts, logging, metrics, git hygiene | `scripts/`, `run.ps1`, `ops/`, `.env.example`, `requirements.txt`, `pyproject.toml`, `.gitignore`, `app/core/logging.py`, `app/core/metrics.py`, `docs/operations.md`, `docs/environment.md` | touches `model/` or `.venv/` |
| `code-reviewer` | read-only review | nothing | writes code |

**Every change:** backend-dev → qa-engineer (writes tests *from the spec*, runs them) → fix loop →
code-reviewer → ops-engineer verifies startup/health → commit.
**A phase is not done until qa-engineer says PASS with evidence (command + output).**
When delegating to QA, hand over the spec section and public interfaces — not the implementation
or the dev's tests.

## 7. Testing rules

- Unit + integration tests must not need the model. Use `FakeLLMService` via dependency override.
- Tests that run real inference carry `@pytest.mark.model` (registered in `pyproject.toml`).
- Golden eval set: `tests/eval/golden_queries.jsonl` (≥ 20 queries: uz-Latin, uz-Cyrillic, Russian,
  typos, "no filter" negatives). `python -m app.scripts.eval_model` writes `docs/model-eval.md`.
- Targets: the 10 core scenarios in `docs/model-eval.md` must all pass on their core fields; overall
  ≥ 85 %. Prompt changes require re-running eval before commit.
- After any code change: run the relevant tests. After any change in `app/prompts/` or
  `llm_service.py`: run `pytest -m model` and the eval.

## 8. Git rules

- Repo is local. Conventional commits (`feat:`, `fix:`, `test:`, `ops:`, `docs:`, `chore:`).
- Commit after every completed phase / green QA gate. Tag releases `v0.x.y`.
- Never create or push to a remote unless one is explicitly given. Never force-push.

## 9. Known assumptions — confirm with the HR team

| Assumption | Where | Change if wrong |
|---|---|---|
| `academicTitleId` is required in `ISearchModel`; we send `0` when not requested | `ACADEMIC_TITLE_ID_DEFAULT` in config | one config value |
| `TypeOfPosition.Техник` (3) is `Technic` in Python; numeric value 3 preserved | `app/models/enums.py` | none needed |
| `rows` default 20, `first` default 0 | `DEFAULT_SEARCH_ROWS` | config |
| HR Search API URL / path / auth are unknown | `HR_SEARCH_API_URL`, `HR_SEARCH_API_PATH`, `HR_SEARCH_API_KEY` | `.env` only |
| When `HR_SEARCH_API_URL` is empty, `/search` returns `searchModel` with `executed: false` | `search_client.py` | none |
| `dateOfReceipt` is sent as `YYYY-MM-DD` (date only) | `app/models/search.py` | send datetime if the API needs it |
| `/search` refuses IDs from the local placeholder JSON | `ALLOW_LOCAL_REFERENCES_FOR_SEARCH` | config |
| 6 GB dev GPU → 4-bit bitsandbytes model (`MODEL_QUANTIZATION=auto`) | `llm_service.py`, `docs/architecture.md` #6 | config |
| Reference collections keys: `regions, districts, sexes, nations, languages, educations, specialists, eduTypes, degrees, academicDegrees, academicTitles, institutions, institutionTypes, institutionGroups, positionCategories, languageCertificates` | `app/models/references.py` | one mapping table |

## 10. Documentation that must stay in sync

`README.md`, `docs/architecture.md`, `docs/api.md`, `docs/frontend-integration.md`,
`docs/operations.md`, `docs/model-eval.md`, `docs/environment.md`, and this file.
If behaviour, an endpoint, a config key, or a threshold changes, update the matching doc in the same
commit.
