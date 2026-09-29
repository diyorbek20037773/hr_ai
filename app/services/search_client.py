"""Client for the EXISTING HR Search API. The AI layer only forwards the SearchModel; results pass through."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.metrics import HR_SEARCH_REQUESTS
from app.models.responses import SearchResults

log = get_logger(__name__)

NOT_CONFIGURED = "HR_SEARCH_API_NOT_CONFIGURED"


class HrSearchError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HrSearchClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.settings.HR_SEARCH_API_URL)

    @property
    def url(self) -> str:
        s = self.settings
        return s.HR_SEARCH_API_URL.rstrip("/") + "/" + s.HR_SEARCH_API_PATH.lstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        key = self.settings.HR_SEARCH_API_KEY
        if key:
            headers["Authorization"] = key if key.lower().startswith(("bearer ", "basic ")) else f"Bearer {key}"
        return headers

    async def search(self, search_model: dict[str, Any], request_id: str | None = None) -> SearchResults:
        """POST the ISearchModel JSON; returns items / totalItems untouched."""
        if not self.configured:
            raise HrSearchError(NOT_CONFIGURED)
        headers = self._headers()
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(timeout=self.settings.HR_SEARCH_API_TIMEOUT,
                                         transport=self._transport) as client:
                resp = await client.post(self.url, json=search_model, headers=headers)
        except httpx.TimeoutException as exc:
            HR_SEARCH_REQUESTS.labels(status="timeout").inc()
            raise HrSearchError("HR Search API timed out") from exc
        except httpx.HTTPError as exc:
            HR_SEARCH_REQUESTS.labels(status="error").inc()
            raise HrSearchError(f"HR Search API unreachable: {type(exc).__name__}") from exc

        HR_SEARCH_REQUESTS.labels(status=str(resp.status_code)).inc()
        if resp.status_code >= 400:
            log.warning("hr_search_http_error", extra={"status": resp.status_code})
            raise HrSearchError(f"HR Search API returned HTTP {resp.status_code}", resp.status_code)
        try:
            body = resp.json()
        except ValueError as exc:
            raise HrSearchError("HR Search API returned non-JSON") from exc
        if isinstance(body, list):  # tolerate a bare list
            body = {"items": body, "totalItems": len(body)}
        if not isinstance(body, dict):
            raise HrSearchError("HR Search API returned an unexpected payload")
        try:
            return SearchResults(items=body.get("items"), totalItems=body.get("totalItems") or 0)
        except ValidationError as exc:
            raise HrSearchError("HR Search API returned a malformed result set") from exc
