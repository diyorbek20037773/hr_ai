/**
 * hr_ai — AI Smart Search integration example for the existing Angular / TypeScript frontend.
 *
 * Flow: user sentence -> POST /api/v1/ai-search/resolve -> render recognizedFilters as removable chips,
 * unresolvedFilters as candidate pickers -> then EITHER patch the Advanced Search form with `searchModel`
 * OR post `searchModel` to the existing Search API (same ISearchModel the manual form sends).
 */

import { ISearchModel, SearchResultDTO } from './search-models'; // existing frontend contract

export type MatchType = 'exact' | 'alias' | 'fuzzy' | 'direct' | 'enum';

export interface RecognizedFilter {
    field: keyof ISearchModel;
    label: string;       // Uzbek Latin
    labelRu: string;     // Russian
    displayValue: string;
    value: unknown;      // id, [id] for list fields, number/bool/string for direct fields
    matchType: MatchType;
    inputValue?: unknown;
    score?: number | null;
}

export interface Candidate { id: number; name: string; score: number; }

export interface UnresolvedFilter {
    field: keyof ISearchModel;
    label: string;
    labelRu: string;
    inputValue: unknown;
    reason: 'no_match' | 'ambiguous' | 'low_confidence' | 'validation' | 'no_reference_data';
    candidates: Candidate[];
    message?: string | null;
}

export interface ResolveResponse {
    query: string;
    intent: Record<string, unknown>;
    promptVersion: string;
    latencyMs: number;
    searchModel: ISearchModel;
    recognizedFilters: RecognizedFilter[];
    unresolvedFilters: UnresolvedFilter[];
    warnings: string[];
    readyToSearch: boolean;
}

export interface ApiErrorBody { error: { code: string; message: string; requestId: string } }

const AI_BASE_URL = 'http://127.0.0.1:8001'; // configure per environment
const AI_API_KEY = '';                       // only if AI_SEARCH_API_KEY is enabled on the server

/** 1. Resolve a sentence. Reference data may be passed to guarantee the same IDs the dropdowns use. */
export async function resolveQuery(
    query: string,
    referenceData?: Record<string, { id: number; name: string; aliases?: string[] }[]>,
    first = 0,
    rows = 20,
): Promise<ResolveResponse> {
    const res = await fetch(`${AI_BASE_URL}/api/v1/ai-search/resolve`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(AI_API_KEY ? { 'X-API-Key': AI_API_KEY } : {}),
        },
        body: JSON.stringify({ query, referenceData, first, rows }),
    });
    if (!res.ok) {
        const body = (await res.json()) as ApiErrorBody;
        // e.g. MODEL_NOT_READY (503) -> "AI qidiruv hozircha tayyor emas", LLM_OUTPUT_INVALID (422) -> ask to rephrase
        throw new Error(`${body.error.code}: ${body.error.message} (requestId ${body.error.requestId})`);
    }
    return (await res.json()) as ResolveResponse;
}

/** 2. Removing a chip = deleting that value from searchModel (list fields: remove just that id). */
export function removeChip(model: ISearchModel, chip: RecognizedFilter): ISearchModel {
    const next: any = { ...model };
    const current = next[chip.field];
    if (Array.isArray(current) && Array.isArray(chip.value)) {
        const remaining = current.filter((v: unknown) => !(chip.value as unknown[]).includes(v));
        next[chip.field] = remaining.length ? remaining : undefined;
    } else if (chip.field === 'academicTitleId') {
        next.academicTitleId = 0; // required field in ISearchModel; 0 = not requested
    } else {
        delete next[chip.field];
    }
    return next as ISearchModel;
}

/** 3. User picked a candidate for an unresolved filter (candidate picker in the UI). */
export function applyCandidate(model: ISearchModel, filter: UnresolvedFilter, candidate: Candidate): ISearchModel {
    const next: any = { ...model };
    const listFields: (keyof ISearchModel)[] = [
        'specialistIds', 'institutionTypeId', 'institutionGroupId', 'spCategoryOfPositionId',
    ];
    if (listFields.includes(filter.field)) {
        next[filter.field] = Array.from(new Set([...(next[filter.field] ?? []), candidate.id]));
    } else {
        next[filter.field] = candidate.id;
    }
    return next as ISearchModel;
}

/** 4a. Populate the existing Advanced Search reactive form (manual path stays untouched). */
export function patchAdvancedSearchForm(form: { patchValue(v: Partial<ISearchModel>): void }, model: ISearchModel) {
    form.patchValue(model);
}

/** 4b. Or run the search directly through the EXISTING HR Search API with the same ISearchModel. */
export async function runExistingSearch(
    searchApiUrl: string,
    model: ISearchModel,
): Promise<{ items?: SearchResultDTO[]; totalItems: number }> {
    const res = await fetch(searchApiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(model),
    });
    return res.json();
}

/** Example usage inside a component. */
export async function onSmartSearch(text: string, searchApiUrl: string) {
    const r = await resolveQuery(text);
    // chips: r.recognizedFilters.map(f => `${f.label}: ${f.displayValue}`)
    // warnings: r.warnings (show under the input)
    if (!r.readyToSearch) {
        // render r.unresolvedFilters with their candidates; call applyCandidate(...) on selection,
        // then run the search when every unresolved filter is picked or dismissed.
        return { model: r.searchModel, pending: r.unresolvedFilters };
    }
    const results = await runExistingSearch(searchApiUrl, r.searchModel);
    return { model: r.searchModel, results };
}
