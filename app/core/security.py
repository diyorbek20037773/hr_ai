"""Optional API-key check (disabled when AI_SEARCH_API_KEY is empty) and the shared error type."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, Request

from app.core.config import Settings, get_settings


async def get_app_settings(request: Request) -> Settings:
    """Settings the running app was created with (falls back to the global .env settings)."""
    return getattr(request.app.state, "settings", None) or get_settings()


class ApiError(Exception):
    """Rendered as {"error": {"code", "message", "requestId"}} by the exception handler in app.main."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_app_settings),
) -> None:
    expected = settings.AI_SEARCH_API_KEY
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise ApiError(401, "UNAUTHORIZED", "Missing or invalid X-API-Key header")
