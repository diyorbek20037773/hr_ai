# Frontend integration

Code example: [`examples/frontend-integration.ts`](../examples/frontend-integration.ts).

## Principle

The manual Advanced Search form and AI Smart Search produce the **same `ISearchModel`**. The AI service only fills
it; the existing HR Search API still executes the search. Nothing in the manual path changes.

```
[ Smart search input ] --POST /api/v1/ai-search/resolve--> hr_ai
        |                                                     |
        |<-- searchModel + recognizedFilters + unresolved ----|
        v
[ chips ] [ candidate pickers ] --> patch Advanced Search form  OR  POST searchModel to existing Search API
```

## Steps

1. **Call `/resolve`** with the user's sentence. Optionally send `referenceData` (the same dictionaries the dropdowns
   use) so IDs are guaranteed to match the current environment. Use `first`/`rows` from the table paginator.
2. **Render `recognizedFilters` as chips**: `label` (or `labelRu` for Russian UI) + `displayValue`. `matchType:
   "fuzzy"` chips can get a subtle "auto-corrected" hint (e.g. "Toshken" → "Toshkent shahri").
3. **Removing a chip** deletes the field from `searchModel` (for list fields remove only that id, see
   `removeChip`). `academicTitleId` is required by `ISearchModel`; reset it to `0`, don't delete it.
4. **`unresolvedFilters`** (when `readyToSearch` is false):
   - `ambiguous` / `low_confidence` → show a picker with `candidates` (`id`, `name`, `score`); on pick call
     `applyCandidate`.
   - `no_match` / `no_reference_data` → show `message`, let the user choose from the regular dropdown.
   - `validation` → show `message` (e.g. age out of range, `fromAge > toAge`); the value was not put into
     `searchModel`.
5. **`warnings`**: show under the input (e.g. "no filters recognized" for "yaxshi kadrlarni top").
6. **`referenceSources`**: if any collection says `local`, the IDs are placeholder dev data — send the real
   dictionaries in `referenceData` (or configure the reference API). `/search` refuses to execute in that case.
7. **Execute**: either `form.patchValue(searchModel)` on the Advanced Search form (user sees all filters filled and
   presses Search as usual) or post `searchModel` to the existing Search API and show `items` / `totalItems` in the
   usual results table. `/api/v1/ai-search/search` can do the second step server-side once `HR_SEARCH_API_URL` is
   configured.

## Errors

All errors use `{ "error": { "code", "message", "requestId" } }` — see [`api.md`](api.md). UX suggestions:

| code | UI |
|---|---|
| `MODEL_NOT_READY` (503) | "AI qidiruv yuklanmoqda, birozdan so'ng urinib ko'ring" — manual search still works |
| `LLM_OUTPUT_INVALID` (422) | "So'rovni boshqacha yozib ko'ring" |
| `LLM_TIMEOUT` (504) | retry once, then suggest manual search |
| `LLM_BUSY` (503) | "Navbat band, birozdan so'ng urinib ko'ring" |
| `QUERY_TOO_LONG` (422) | limit the input to 1000 characters client-side |

## CORS

Allowed origins come from `CORS_ALLOWED_ORIGINS` (default `http://localhost:4200,http://localhost:3000`). Add the
production frontend origin in `.env`.

## Latency

One local model serves requests one at a time: about 3.3 s per query on the dev GPU (RTX 3050 6 GB, 4-bit), minutes
on a CPU-only machine. Show a
spinner, debounce the input (search on Enter / button, not on each keystroke).
