"""API request / response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.references import ReferenceItem

MatchType = Literal["exact", "alias", "fuzzy", "direct", "enum"]
UnresolvedReason = Literal["no_match", "ambiguous", "low_confidence", "validation", "no_reference_data"]


class Candidate(BaseModel):
    id: int
    name: str
    score: float


class RecognizedFilter(BaseModel):
    field: str
    label: str
    labelRu: str
    displayValue: str
    value: Any
    matchType: MatchType
    inputValue: Any | None = None
    score: float | None = None


class UnresolvedFilter(BaseModel):
    field: str
    label: str
    labelRu: str
    inputValue: Any
    reason: UnresolvedReason
    candidates: list[Candidate] = Field(default_factory=list)
    message: str | None = None


class ResolveResult(BaseModel):
    searchModel: dict[str, Any]
    recognizedFilters: list[RecognizedFilter] = Field(default_factory=list)
    unresolvedFilters: list[UnresolvedFilter] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    readyToSearch: bool = False


# ---- requests ----

class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1)
    referenceData: dict[str, list[ReferenceItem]] | None = None
    first: int | None = Field(default=None, ge=0)
    rows: int | None = Field(default=None, ge=1, le=1000)


# ---- responses ----

class ParseResponse(BaseModel):
    query: str
    intent: dict[str, Any]
    corrections: list[str] = Field(default_factory=list)  # deterministic guard fixes applied to the LLM intent
    promptVersion: str
    latencyMs: int


class ResolveResponse(ResolveResult):
    query: str
    referenceSources: dict[str, str] = Field(default_factory=dict)  # collection -> request | remote | local
    corrections: list[str] = Field(default_factory=list)  # deterministic guard fixes applied to the LLM intent
    intent: dict[str, Any]
    promptVersion: str
    latencyMs: int


class SearchResults(BaseModel):
    items: list[dict[str, Any]] | None = None
    totalItems: int = 0


class SearchResponse(ResolveResponse):
    executed: bool
    reason: str | None = None
    results: SearchResults | None = None
