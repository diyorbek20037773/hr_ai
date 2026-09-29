"""Reference data providers: request payload -> remote reference API -> local JSON (per collection)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.references import COLLECTION_KEYS, ReferenceData, ReferenceItem

log = get_logger(__name__)


@runtime_checkable
class ReferenceProvider(Protocol):
    name: str

    async def get(self, collection: str) -> list[ReferenceItem] | None:
        """Items of a collection, or None when this provider does not have it."""
        ...


class RequestReferenceProvider:
    """Collections passed by the caller in `referenceData` (highest priority)."""

    name = "request"

    def __init__(self, data: ReferenceData | None):
        self._data = data or {}

    async def get(self, collection: str) -> list[ReferenceItem] | None:
        items = self._data.get(collection)
        return list(items) if items else None


class LocalJsonReferenceProvider:
    """`references/example_references.json` — dev/test data (placeholder IDs)."""

    name = "local"

    def __init__(self, path: Path):
        self.path = path
        self._cache: ReferenceData | None = None
        self._mtime: float | None = None

    def _load(self) -> ReferenceData:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            log.warning("local_references_missing", extra={"path": str(self.path)})
            return {}
        if self._cache is None or mtime != self._mtime:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._cache = {k: [ReferenceItem.model_validate(i) for i in v] for k, v in raw.items()
                               if k in COLLECTION_KEYS and isinstance(v, list)}
            except (ValueError, ValidationError, AttributeError) as exc:
                log.error("local_references_invalid", extra={"path": str(self.path), "error": type(exc).__name__})
                self._cache = {}
            self._mtime = mtime
        return self._cache

    async def get(self, collection: str) -> list[ReferenceItem] | None:
        items = self._load().get(collection)
        return list(items) if items else None


def _to_items(payload: Any) -> list[ReferenceItem]:
    """Accept a list of items or {"items": [...]} / {"data": [...]}; tolerate nameUz/nameRu/title keys."""
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("data") or payload.get("result") or []
    items: list[ReferenceItem] = []
    for raw in payload if isinstance(payload, list) else []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name") or raw.get("nameUz") or raw.get("nameLat") or raw.get("title")
        if raw.get("id") is None or not name:
            continue
        aliases = list(raw.get("aliases") or [])
        for k in ("nameRu", "nameCyrl", "nameUzCyrl", "nameEn", "shortName"):
            if raw.get(k) and raw[k] != name:
                aliases.append(raw[k])
        try:
            items.append(ReferenceItem(id=int(raw["id"]), name=str(name), aliases=[str(a) for a in aliases]))
        except (ValidationError, ValueError, TypeError):
            continue
    return items


class RemoteReferenceProvider:
    """`REFERENCE_API_BASE_URL` + per-collection path, with an in-memory TTL cache."""

    name = "remote"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client
        self._cache: dict[str, tuple[float, list[ReferenceItem]]] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.REFERENCE_API_BASE_URL)

    def _url(self, collection: str) -> str:
        path = self.settings.REFERENCE_API_PATHS.get(collection, f"/{collection}")
        return self.settings.REFERENCE_API_BASE_URL.rstrip("/") + "/" + path.lstrip("/")

    async def get(self, collection: str) -> list[ReferenceItem] | None:
        if not self.enabled:
            return None
        now = time.monotonic()
        cached = self._cache.get(collection)
        if cached and now - cached[0] < self.settings.REFERENCE_CACHE_TTL_SECONDS:
            return cached[1]
        async with self._lock:
            try:
                client = self._client or httpx.AsyncClient(timeout=self.settings.REFERENCE_API_TIMEOUT)
                try:
                    resp = await client.get(self._url(collection))
                    resp.raise_for_status()
                    items = _to_items(resp.json())
                finally:
                    if self._client is None:
                        await client.aclose()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("reference_api_failed", extra={"collection": collection, "error": type(exc).__name__})
                return cached[1] if cached else None
        if items:
            self._cache[collection] = (now, items)
            return items
        return None


class CompositeReferenceProvider:
    """Per collection: first provider that has it wins (request -> remote -> local)."""

    name = "composite"

    def __init__(self, providers: Iterable[ReferenceProvider]):
        self.providers = list(providers)

    async def get(self, collection: str) -> list[ReferenceItem] | None:
        for p in self.providers:
            items = await p.get(collection)
            if items:
                return items
        return None

    async def load_with_sources(self, collections: Iterable[str]) -> tuple[ReferenceData, dict[str, str]]:
        """Items per collection plus which provider ("request" | "remote" | "local") served each one."""
        data: ReferenceData = {}
        sources: dict[str, str] = {}
        for c in dict.fromkeys(collections):
            for p in self.providers:
                items = await p.get(c)
                if items:
                    data[c] = items
                    sources[c] = p.name
                    break
        return data, sources

    async def load(self, collections: Iterable[str]) -> ReferenceData:
        return (await self.load_with_sources(collections))[0]


class ReferenceService:
    """Process-wide holder of the shared providers; builds a composite per request."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.remote = RemoteReferenceProvider(self.settings)
        self.local = LocalJsonReferenceProvider(self.settings.resolve_path(self.settings.LOCAL_REFERENCES_PATH))

    def for_request(self, request_data: ReferenceData | None = None) -> CompositeReferenceProvider:
        return CompositeReferenceProvider([RequestReferenceProvider(request_data), self.remote, self.local])
