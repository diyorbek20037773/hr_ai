# API reference

Base URL: `http://127.0.0.1:8001` · Swagger UI: `/docs` · OpenAPI: `/openapi.json`

All `/api/v1/*` endpoints require `X-API-Key: <AI_SEARCH_API_KEY>` **only when** `AI_SEARCH_API_KEY` is set.
Every response carries `X-Request-ID` (echoed if the client sent one, generated otherwise).

## Error envelope

```json
{ "error": { "code": "LLM_OUTPUT_INVALID", "message": "…", "requestId": "3f2a9c…" } }
```

| HTTP | code | When |
|---|---|---|
| 401 | `UNAUTHORIZED` | API key enabled and header missing/wrong |
| 404 | `NOT_FOUND`, `UNKNOWN_COLLECTION` | unknown route / reference collection |
| 413 | `PAYLOAD_TOO_LARGE`, `REFERENCE_DATA_TOO_LARGE` | body > 5 MB / `referenceData` > `MAX_REFERENCE_ITEMS` items |
| 422 | `VALIDATION_ERROR` | malformed body (details in `error.details`) |
| 422 | `QUERY_EMPTY`, `QUERY_TOO_LONG` | query blank / > `MAX_QUERY_LENGTH` (1000) chars |
| 422 | `REFERENCE_DATA_INVALID` | unknown collection key in `referenceData` |
| 422 | `LLM_OUTPUT_INVALID` | model output not valid JSON after one corrective retry |
| 502 | `HR_SEARCH_FAILED` | HR Search API error / unreachable |
| 503 | `MODEL_NOT_READY` | model still loading or failed to load |
| 503 | `LLM_BUSY` | more than `LLM_MAX_QUEUE` (8) requests already waiting for the model |
| 504 | `LLM_TIMEOUT` | generation exceeded `LLM_TIMEOUT_SECONDS` |
| 500 | `INTERNAL_ERROR` | unexpected error (logged with request id) |

## `GET /health`

Liveness — always 200 while the process runs.

```json
{ "status": "ok", "modelLoaded": true, "modelStatus": "loaded", "modelPath": "D:\\myprojects\\hr_ai\\model",
  "device": "cuda", "dtype": "bfloat16+4bit", "loadSeconds": 12.0, "promptVersion": "11", "uptimeSeconds": 431.2 }
```

`modelStatus`: `loading | loaded | failed | not_loaded`.

## `GET /ready`

200 `{ "ready": true, … }` when the model is loaded; 503 `{ "ready": false, "modelStatus": "loading", "error": null, … }`
otherwise (`error` holds the load failure message when `modelStatus` is `failed`).

## `GET /metrics`

Prometheus text format. See `docs/operations.md` for the catalogue.

## `POST /api/v1/ai-search/parse`

Request: `{ "query": "Oliy ma'lumotli, 30 yoshdan katta ayollar" }`

Response:
```json
{ "query": "Oliy ma'lumotli, 30 yoshdan katta ayollar",
  "intent": { "education": "Oliy", "fromAge": 30, "sex": "Ayol" },
  "promptVersion": "11", "latencyMs": 5210 }
```

`intent` is the model output after deterministic guards (age direction fixed from the query wording; ages, years,
experience without the digits in the query, education without an education word and sex without a gender word are
removed, as is a `dateOfReceipt` without a hiring word; a position type found in `specialists` is moved to
`typeOfPosition` and position types are rewritten to their enum names without duplicates; a position type with no matching word
in the query is removed).

`corrections` (also in `/resolve` and `/search`) lists the guard codes applied, e.g. `["toAge->fromAge", "drop:sex"]`,
so the UI can tell the user that a value was adjusted or ignored; `[]` when none.

`intent` keys (text values only, only those the user mentioned): `region, birthRegion, district, sex, nation,
language, languageCertificate, education, eduType, degree, academicDegree, academicTitle, specialists[],
institution, institutionTypes[], institutionGroups[], positionCategories[], typeOfPosition[], typeOfService,
fromAge, toAge, fromYear, toYear, experienceYear, firstName, lastName, middleName, pinfl, inn, institutionInn,
institutionName, isAwarded, isReserved, isAcademicDegree, isAcademicTitle, isGlobalEducation, term, dateOfReceipt`.

## `POST /api/v1/ai-search/resolve`

Request:
```json
{ "query": "Chilonzor tumanida yashaydigan, ingliz tilini biladigan, IELTS sertifikati bor yoshlar 35 yoshgacha",
  "referenceData": { "districts": [ { "id": 102, "name": "Chilonzor tumani", "aliases": ["Chilonzor", "Чиланзар"] } ] },
  "first": 0, "rows": 20 }
```

`referenceData` (optional) overrides collections per request; missing collections come from the remote reference
API (`REFERENCE_API_BASE_URL`) and then the local JSON. Allowed keys: `regions, districts, sexes, nations,
languages, educations, specialists, eduTypes, degrees, academicDegrees, academicTitles, institutions,
institutionTypes, institutionGroups, positionCategories, languageCertificates`. Item: `{id, name, aliases?}`.

Response:
```json
{
  "query": "…",
  "intent": { "district": "Chilonzor", "language": "Ingliz tili", "languageCertificate": "IELTS", "toAge": 35 },
  "promptVersion": "11",
  "latencyMs": 5873,
  "referenceSources": { "districts": "request", "languages": "local", "languageCertificates": "local" },
  "searchModel": { "districtId": 102, "languageId": 3, "toAge": 35, "academicTitleId": 0,
                   "languageCertificateId": 1, "first": 0, "rows": 20 },
  "recognizedFilters": [
    { "field": "districtId", "label": "Tuman", "labelRu": "Район", "displayValue": "Chilonzor tumani",
      "value": 102, "matchType": "alias", "inputValue": "Chilonzor", "score": 100.0 },
    { "field": "toAge", "label": "Yoshi (gacha)", "labelRu": "Возраст (до)", "displayValue": "35",
      "value": 35, "matchType": "direct", "inputValue": null, "score": null }
  ],
  "unresolvedFilters": [],
  "warnings": [],
  "readyToSearch": true
}
```

- `matchType`: `exact | alias | fuzzy | direct | enum`. List fields (`specialistIds`, `typeOfPosition`, …) produce
  one chip per item with `value: [id]`.
- `unresolvedFilters[]`: `{field, label, labelRu, inputValue, reason, candidates: [{id, name, score}], message}`;
  `reason`: `no_match | ambiguous | low_confidence | validation | no_reference_data`. Unresolved values are **not**
  put into `searchModel`. For an inverted range (`fromAge > toAge`, `fromYear > toYear`) both values are removed
  and reported with `reason: "validation"`.
- `referenceSources` tells, per collection the intent needed, where the IDs came from: `request`, `remote` or
  `local` (placeholder dev data).
- `readyToSearch` is true only with zero unresolved filters and zero validation errors. An empty intent gives an
  empty filter set, `readyToSearch: true` and the warning "no filters recognized".
- `searchModel` always contains `academicTitleId` (`ACADEMIC_TITLE_ID_DEFAULT`, 0, unless requested), `first`, `rows`.
- Validation: `0 ≤ fromAge ≤ toAge ≤ 120`, `1900 ≤ fromYear ≤ toYear ≤ current year`, `experienceYear 0..60`,
  `pinfl` 14 digits (string), `inn`/`institutionInn` 9 digits.

## `POST /api/v1/ai-search/search`

Same request as `/resolve`. Response = `/resolve` fields plus:

| Situation | `executed` | `reason` | `results` |
|---|---|---|---|
| `HR_SEARCH_API_URL` empty | `false` | `HR_SEARCH_API_NOT_CONFIGURED` | `null` |
| `readyToSearch` false | `false` | `NOT_READY_TO_SEARCH` | `null` |
| no filter recognized (empty intent) | `false` | `NO_FILTERS` | `null` |
| a needed collection came from the local placeholder JSON (and `ALLOW_LOCAL_REFERENCES_FOR_SEARCH=false`) | `false` | `LOCAL_REFERENCES_NOT_ALLOWED` | `null` |
| searched | `true` | `null` | `{ "items": SearchResultDTO[], "totalItems": n }` (passed through untouched) |

The HR API call is `POST {HR_SEARCH_API_URL}{HR_SEARCH_API_PATH}` with the `searchModel` JSON as body,
`Authorization: Bearer {HR_SEARCH_API_KEY}` when set, and `X-Request-ID`.

## Prompt viewer / editor (`/api/v1/prompt`)

Shows exactly what Qwen receives and lets a developer try an edited prompt. The edited prompt is a **runtime
override**: it is kept in memory only, is **not evaluated**, is reported as version `"<default>+custom"` (in
`/health`, `promptVersion` of every parse/resolve/search response, and `promptCustom: true` in `/health`), and is
lost on restart. To make a prompt permanent, copy it into `app/prompts/search_parser.py`, bump `PROMPT_VERSION` and
run the eval. Disable editing in production with `PROMPT_EDITING_ENABLED=false` (GET and render keep working).

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/prompt` | `{version, defaultVersion, isCustom, editingEnabled, systemPrompt, fewShots:[{query, answer}], retryMessage, tokenCount, tokenCountQuery, tokenLimit, intentKeys, warnings}`. `tokenCount` is measured with a sample query (null while the model is not loaded) |
| `PUT /api/v1/prompt` | body `{systemPrompt, fewShots:[{query, answer}]}` → the new state. Answers must use intent keys only, contain no `*Id` keys, and be in canonical form. Otherwise 422 `PROMPT_INVALID`. Adds a warning when the prompt exceeds `PROMPT_MAX_TOKENS` (1800) |
| `DELETE /api/v1/prompt` | restore the committed default |
| `POST /api/v1/prompt/render` | body `{query}` → `{messages, rendered, tokenCount, exact}`. `rendered` is the exact text after Qwen's chat template (`<\|im_start\|>system …`) when the model is loaded (`exact: true`) |

PUT/DELETE with editing disabled → 403 `PROMPT_EDITING_DISABLED`.

## `GET /api/v1/references[?collection=regions]`

```json
{ "collections": { "regions": { "source": "local", "count": 14, "items": [ { "id": 1, "name": "Qoraqalpog'iston Respublikasi" } ] } },
  "missing": [] }
```

`source`: `remote | local`.

## Examples

PowerShell:
```powershell
$body = @{ query = "Oliy ma'lumotli, 30 yoshdan katta ayollar" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8001/api/v1/ai-search/resolve -Method Post `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

curl:
```bash
curl -s http://127.0.0.1:8001/api/v1/ai-search/resolve -H "Content-Type: application/json" \
  -d '{"query":"Женщины старше 30 лет с высшим образованием"}'
```
