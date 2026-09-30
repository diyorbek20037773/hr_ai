"""FastAPI application. Run with exactly one worker and no reload (CLAUDE.md §3.4):

    uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import ai_search, health, prompt, ui
from app.core.config import Settings, get_settings
from app.core.logging import get_logger, request_id_var, setup_logging
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS, MODEL_LOADED
from app.core.security import ApiError
from app.services.filter_resolver import FilterResolver
from app.services.llm_service import BaseLLMService, QwenLLMService
from app.services.reference_provider import ReferenceService
from app.services.search_client import HrSearchClient

log = get_logger("app")


def _load_model_in_background(app: FastAPI, llm: QwenLLMService) -> None:
    """Load once, off the event loop, so /health answers while the weights load; /ready turns 200 after."""
    try:
        llm.load()
        app.state.model_status = "loaded"
    except Exception as exc:  # noqa: BLE001 — surface any load failure via /ready
        app.state.model_status = "failed"
        app.state.model_error = f"{type(exc).__name__}: {exc}"
        MODEL_LOADED.set(0)
        log.error("Model load failed", exc_info=True)


def create_app(settings: Settings | None = None, llm: BaseLLMService | None = None) -> FastAPI:
    """`llm` given (tests) -> used as-is, no model loading. Otherwise the Qwen model loads once in lifespan."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.LOG_LEVEL, settings.resolve_path(settings.LOG_DIR))
        app.state.started_at = time.monotonic()
        loader: threading.Thread | None = None
        if llm is None:
            qwen = QwenLLMService(settings)
            app.state.llm = qwen
            app.state.model_status = "loading"
            loader = threading.Thread(target=_load_model_in_background, args=(app, qwen), name="model-loader",
                                      daemon=True)
            loader.start()
        else:
            app.state.llm = llm
            app.state.model_status = "loaded" if llm.is_loaded else "not_loaded"
            MODEL_LOADED.set(1 if llm.is_loaded else 0)
        log.info("startup", extra={"host": settings.APP_HOST, "port": settings.APP_PORT})
        yield
        log.info("shutdown")
        if isinstance(app.state.llm, QwenLLMService):
            app.state.llm.unload()

    app = FastAPI(
        title="hr_ai — AI Smart Search",
        version="0.1.0",
        description="Local Qwen3-4B translates a natural-language HR search request into the existing "
                    "ISearchModel. The AI never sees personnel records.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.started_at = time.monotonic()
    app.state.model_status = "not_loaded"
    app.state.model_error = None
    app.state.llm = llm
    app.state.references = ReferenceService(settings)
    app.state.resolver = FilterResolver(settings)
    app.state.hr_client = HrSearchClient(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID", "Authorization"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        rid = "".join(ch for ch in rid if ch.isalnum() or ch in "-_.")[:64] or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        status = 500
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.MAX_BODY_BYTES:
                response = _error(413, "PAYLOAD_TOO_LARGE", "request body too large", rid)
            else:
                try:
                    response = await call_next(request)
                except Exception as exc:  # noqa: BLE001 — build the 500 envelope here, while the request id is set
                    log.error("unhandled_error", extra={"errorType": type(exc).__name__})
                    log.debug("unhandled_error_traceback", exc_info=exc)
                    response = _error(500, "INTERNAL_ERROR", "Internal server error", rid)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            elapsed = time.perf_counter() - t0
            HTTP_REQUESTS.labels(method=request.method, path=path, status=str(status)).inc()
            HTTP_DURATION.labels(path=path).observe(elapsed)
            if path not in ("/metrics", "/health", "/ready"):
                log.info("http_request", extra={"method": request.method, "path": path, "status": status,
                                                "durationMs": int(elapsed * 1000)})
            request_id_var.reset(token)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return _error(exc.status_code, exc.code, exc.message, request_id_var.get())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()[:10]]
        return _error(422, "VALIDATION_ERROR", "Invalid request body", request_id_var.get(), details)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail), request_id_var.get())

    app.include_router(health.router)
    app.include_router(ai_search.router)
    app.include_router(prompt.router)
    app.include_router(ui.router)
    return app


def _error(status: int, code: str, message: str, request_id: str, details=None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message, "requestId": request_id}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers={"X-Request-ID": request_id})


app = create_app()
