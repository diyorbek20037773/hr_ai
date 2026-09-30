"""Liveness, readiness and Prometheus metrics."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.core.metrics import render_metrics
from app.prompts.store import get_active

router = APIRouter(tags=["health"])


def _llm_info(request: Request) -> dict:
    llm = getattr(request.app.state, "llm", None)
    loaded = bool(llm and llm.is_loaded)
    return {
        "modelLoaded": loaded,
        "modelStatus": getattr(request.app.state, "model_status", "unknown"),
        "modelPath": llm.model_path if llm else None,
        "device": llm.device if llm else None,
        "dtype": llm.dtype if llm else None,
        "loadSeconds": getattr(llm, "load_seconds", None),
    }


@router.get("/health")
async def health(request: Request) -> dict:
    info = _llm_info(request)
    return {
        "status": "ok",
        **info,
        "promptVersion": get_active().version,
        "promptCustom": get_active().is_custom,
        "uptimeSeconds": round(time.monotonic() - request.app.state.started_at, 1),
    }


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    info = _llm_info(request)
    if info["modelLoaded"]:
        return JSONResponse({"ready": True, **info})
    error = getattr(request.app.state, "model_error", None)
    return JSONResponse({"ready": False, **info, "error": error}, status_code=503)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
