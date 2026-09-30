"""FastAPI dependencies — process-wide singletons live on `app.state` (created once in `app.main`)."""

from __future__ import annotations

from fastapi import Request

from app.core.security import ApiError
from app.services.filter_resolver import FilterResolver
from app.services.intent_parser import IntentParser
from app.services.llm_service import BaseLLMService
from app.services.reference_provider import ReferenceService
from app.services.search_client import HrSearchClient


async def get_llm(request: Request) -> BaseLLMService:
    llm = getattr(request.app.state, "llm", None)
    if llm is None or not llm.is_loaded:
        status = getattr(request.app.state, "model_status", "loading")
        raise ApiError(503, "MODEL_NOT_READY", f"Local model is not ready (status: {status})")
    return llm


async def get_intent_parser(request: Request) -> IntentParser:
    return IntentParser(await get_llm(request))


async def get_reference_service(request: Request) -> ReferenceService:
    return request.app.state.references


async def get_resolver(request: Request) -> FilterResolver:
    return request.app.state.resolver


async def get_hr_client(request: Request) -> HrSearchClient:
    return request.app.state.hr_client
