"""AI Smart Search endpoints: parse -> resolve -> search."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_hr_client, get_intent_parser, get_reference_service, get_resolver
from app.core.config import Settings
from app.core.logging import get_logger, query_fingerprint, request_id_var
from app.core.security import ApiError, get_app_settings, require_api_key
from app.models.references import COLLECTION_KEYS
from app.models.responses import (
    ParseRequest,
    ParseResponse,
    ResolveRequest,
    ResolveResponse,
    SearchResponse,
)
from app.services.filter_resolver import FilterResolver
from app.services.intent_parser import IntentParseError, IntentParser, ParseOutcome
from app.services.llm_service import LLMBusyError, LLMTimeoutError
from app.services.reference_provider import ReferenceService
from app.services.search_client import NOT_CONFIGURED, HrSearchClient, HrSearchError

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["ai-search"], dependencies=[Depends(require_api_key)])


def _check_query(query: str, settings: Settings) -> str:
    q = query.strip()
    if not q:
        raise ApiError(422, "QUERY_EMPTY", "query must not be empty")
    if len(q) > settings.MAX_QUERY_LENGTH:
        raise ApiError(422, "QUERY_TOO_LONG", f"query must be at most {settings.MAX_QUERY_LENGTH} characters")
    return q


async def _parse(parser: IntentParser, query: str) -> ParseOutcome:
    try:
        return await run_in_threadpool(parser.parse, query)
    except IntentParseError as exc:
        raise ApiError(422, exc.code, "The model did not return a valid search intent; rephrase the query") from exc
    except LLMTimeoutError as exc:
        raise ApiError(504, "LLM_TIMEOUT", "The local model did not answer in time") from exc
    except LLMBusyError as exc:
        raise ApiError(503, "LLM_BUSY", "Too many AI search requests are waiting; retry shortly") from exc


async def _resolve(body: ResolveRequest, parser: IntentParser, refs: ReferenceService,
                   resolver: FilterResolver, settings: Settings) -> ResolveResponse:
    t0 = time.perf_counter()
    query = _check_query(body.query, settings)
    if body.referenceData:
        unknown = sorted(set(body.referenceData) - set(COLLECTION_KEYS))
        if unknown:
            raise ApiError(422, "REFERENCE_DATA_INVALID", f"unknown reference collections: {', '.join(unknown)}")
        total = sum(len(v) for v in body.referenceData.values())
        if total > settings.MAX_REFERENCE_ITEMS:
            raise ApiError(413, "REFERENCE_DATA_TOO_LARGE",
                           f"referenceData has {total} items; limit is {settings.MAX_REFERENCE_ITEMS}")
    outcome = await _parse(parser, query)
    provider = refs.for_request(body.referenceData)
    references, sources = await provider.load_with_sources(resolver.required_collections(outcome.intent))
    # fuzzy matching over large collections is CPU work - keep it off the event loop
    result = await run_in_threadpool(resolver.resolve, outcome.intent, references, body.first, body.rows)
    latency = int((time.perf_counter() - t0) * 1000)
    log.info("resolve_done", extra={**query_fingerprint(query), "durationMs": latency,
                                    "recognized": len(result.recognizedFilters),
                                    "unresolved": len(result.unresolvedFilters), "ready": result.readyToSearch})
    return ResolveResponse(query=query, intent=outcome.intent.to_compact_dict(), referenceSources=sources,
                           corrections=outcome.corrections,
                           promptVersion=outcome.prompt_version, latencyMs=latency, **result.model_dump())


@router.post("/ai-search/parse", response_model=ParseResponse, summary="Text -> intent (text values only)")
async def parse(body: ParseRequest, parser: IntentParser = Depends(get_intent_parser),
                settings: Settings = Depends(get_app_settings)) -> ParseResponse:
    t0 = time.perf_counter()
    query = _check_query(body.query, settings)
    outcome = await _parse(parser, query)
    latency = int((time.perf_counter() - t0) * 1000)
    log.info("parse_done", extra={**query_fingerprint(query), "durationMs": latency, "attempts": outcome.attempts})
    return ParseResponse(query=query, intent=outcome.intent.to_compact_dict(), corrections=outcome.corrections,
                         promptVersion=outcome.prompt_version, latencyMs=latency)


@router.post("/ai-search/resolve", response_model=ResolveResponse, summary="Text -> ISearchModel + filter chips")
async def resolve(body: ResolveRequest, parser: IntentParser = Depends(get_intent_parser),
                  refs: ReferenceService = Depends(get_reference_service),
                  resolver: FilterResolver = Depends(get_resolver),
                  settings: Settings = Depends(get_app_settings)) -> ResolveResponse:
    return await _resolve(body, parser, refs, resolver, settings)


@router.post("/ai-search/search", response_model=SearchResponse, response_model_exclude_none=False,
             summary="Resolve, then run the existing HR Search API")
async def search(body: ResolveRequest, parser: IntentParser = Depends(get_intent_parser),
                 refs: ReferenceService = Depends(get_reference_service),
                 resolver: FilterResolver = Depends(get_resolver),
                 hr: HrSearchClient = Depends(get_hr_client),
                 settings: Settings = Depends(get_app_settings)) -> SearchResponse:
    resolved = await _resolve(body, parser, refs, resolver, settings)
    base = resolved.model_dump()
    if not hr.configured:
        return SearchResponse(**base, executed=False, reason=NOT_CONFIGURED)
    if not resolved.readyToSearch:
        return SearchResponse(**base, executed=False, reason="NOT_READY_TO_SEARCH")
    if not resolved.recognizedFilters:
        # an empty intent would run an unfiltered personnel query nobody asked for
        return SearchResponse(**base, executed=False, reason="NO_FILTERS")
    placeholder = sorted(c for c, src in resolved.referenceSources.items() if src == "local")
    if placeholder and not settings.ALLOW_LOCAL_REFERENCES_FOR_SEARCH:
        # local JSON holds placeholder IDs - never send them to the real HR API
        log.warning("search_blocked_local_references", extra={"collections": placeholder})
        return SearchResponse(**base, executed=False, reason="LOCAL_REFERENCES_NOT_ALLOWED")
    try:
        results = await hr.search(resolved.searchModel, request_id=request_id_var.get())
    except HrSearchError as exc:
        raise ApiError(502, "HR_SEARCH_FAILED", str(exc)) from exc
    return SearchResponse(**base, executed=True, results=results)


@router.get("/references", summary="Reference collections available to the resolver")
async def references(collection: str | None = Query(default=None),
                     refs: ReferenceService = Depends(get_reference_service)) -> dict:
    if collection and collection not in COLLECTION_KEYS:
        raise ApiError(404, "UNKNOWN_COLLECTION", f"unknown collection '{collection}'")
    names = [collection] if collection else list(COLLECTION_KEYS)
    data, sources = await refs.for_request(None).load_with_sources(names)
    out = {name: {"source": sources[name], "count": len(items), "items": [{"id": i.id, "name": i.name} for i in items]}
           for name, items in data.items()}
    return {"collections": out, "missing": [n for n in names if n not in out]}
