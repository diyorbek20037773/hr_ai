# Reference data

`example_references.json` is **development / test data**. All IDs in it are **placeholders** chosen for this
repository — they are NOT the IDs of the production HR system. Before production use, the resolver must get real
dictionaries from one of:

1. the request body (`referenceData` in `/resolve` and `/search`) — highest priority, per collection;
2. the reference API (`REFERENCE_API_BASE_URL` + `/<collection>`, or paths from `REFERENCE_API_PATHS`),
   cached in memory for `REFERENCE_CACHE_TTL_SECONDS`;
3. this local JSON file (`LOCAL_REFERENCES_PATH`) — fallback.

## Shape

```json
{ "regions": [ { "id": 14, "name": "Toshkent shahri", "aliases": ["Toshkent", "Tashkent", "Тошкент", "Ташкент"] } ] }
```

`aliases` is optional. Add Latin, Cyrillic and Russian spellings and common short forms as aliases — exact alias
matches are preferred over fuzzy matching. Remote APIs may return `{items: [...]}` / `{data: [...]}` and use
`nameUz` / `nameRu` / `nameCyrl` keys; those are mapped to `name` + `aliases` automatically.

## Collections

`regions` (14: 12 viloyat + Qoraqalpog'iston + Toshkent shahri), `districts` (15, mostly Toshkent shahri),
`sexes` (1 Erkak, 2 Ayol), `nations` (8), `languages` (9), `educations` (5), `specialists` (32), `eduTypes`,
`degrees`, `academicDegrees`, `academicTitles`, `institutions`, `institutionTypes`, `institutionGroups`,
`positionCategories`, `languageCertificates` (10).

IDs referenced by tests and `scripts/smoke_test.ps1`: regions Samarqand viloyati = 8, Toshkent shahri = 14;
district Chilonzor tumani = 102; sex Ayol = 2; education Oliy = 1; specialist Iqtisodiyot = 1; language Ingliz
tili = 3; certificate IELTS = 1. Changing them requires updating those tests.
