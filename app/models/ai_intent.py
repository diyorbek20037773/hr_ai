"""`ParsedSearchIntent` — what the LLM outputs: text values only, never IDs (CLAUDE.md §2).

Lenient on input shape (numeric strings, "ha"/"да" booleans, single value vs list, a few synonym keys),
strict on meaning: unknown keys are ignored, empty values are dropped, nothing is defaulted.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_TRUE = {"true", "1", "yes", "ha", "bor", "да", "ҳа", "есть", "y"}
_FALSE = {"false", "0", "no", "yo'q", "yoq", "нет", "йўқ", "n"}
_INT_RE = re.compile(r"-?\d+")

# Tolerated alternative keys the model may produce -> canonical key.
_KEY_SYNONYMS = {
    "specialist": "specialists",
    "specialization": "specialists",
    "institutionType": "institutionTypes",
    "institutionGroup": "institutionGroups",
    "positionCategory": "positionCategories",
    "gender": "sex",
    "experience": "experienceYear",
    "experienceYears": "experienceYear",
    "ageFrom": "fromAge",
    "ageTo": "toAge",
    "minAge": "fromAge",
    "maxAge": "toAge",
    "certificate": "languageCertificate",
}

TEXT_FIELDS = (
    "region", "birthRegion", "district", "sex", "nation", "language", "education", "eduType", "degree",
    "academicDegree", "academicTitle", "institution", "languageCertificate", "typeOfService",
    "firstName", "lastName", "middleName", "institutionName", "term", "pinfl", "dateOfReceipt",
)
LIST_FIELDS = ("specialists", "institutionTypes", "institutionGroups", "positionCategories", "typeOfPosition")
INT_FIELDS = ("fromAge", "toAge", "fromYear", "toYear", "experienceYear", "inn", "institutionInn")
BOOL_FIELDS = ("isAwarded", "isReserved", "isAcademicDegree", "isAcademicTitle", "isGlobalEducation")


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list | dict) and not v)


class ParsedSearchIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # dictionary-backed (resolved to IDs by the resolver)
    region: str | None = None
    birthRegion: str | None = None
    district: str | None = None
    sex: str | None = None
    nation: str | None = None
    language: str | None = None
    education: str | None = None
    specialists: list[str] | None = None
    eduType: str | None = None
    degree: str | None = None
    academicDegree: str | None = None
    academicTitle: str | None = None
    institution: str | None = None
    institutionTypes: list[str] | None = None
    institutionGroups: list[str] | None = None
    positionCategories: list[str] | None = None
    languageCertificate: str | None = None
    # enums (names or phrases)
    typeOfPosition: list[str] | None = None
    typeOfService: str | None = None
    # direct values
    firstName: str | None = None
    lastName: str | None = None
    middleName: str | None = None
    fromAge: int | None = None
    toAge: int | None = None
    fromYear: int | None = None
    toYear: int | None = None
    experienceYear: int | None = None
    inn: int | None = None
    institutionInn: int | None = None
    institutionName: str | None = None
    pinfl: str | None = None
    isAwarded: bool | None = None
    isReserved: bool | None = None
    isAcademicDegree: bool | None = None
    isAcademicTitle: bool | None = None
    isGlobalEducation: bool | None = None
    term: str | None = None
    dateOfReceipt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("intent must be a JSON object")
        out: dict[str, Any] = {}
        for key, value in data.items():
            canonical = _KEY_SYNONYMS.get(key, key)
            if _is_empty(value):
                continue
            if canonical in LIST_FIELDS:
                items = value if isinstance(value, list) else [value]
                items = [str(i).strip() for i in items if not _is_empty(i)]
                if canonical in out:
                    items = list(out[canonical]) + items
                if items:
                    out[canonical] = list(dict.fromkeys(items))
            elif canonical not in out:
                out[canonical] = value
        return out

    @field_validator(*INT_FIELDS, mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("boolean is not a number")
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str):
            m = _INT_RE.search(v)
            if not m:
                raise ValueError(f"not a number: {v!r}")
            return int(m.group())
        return v

    @field_validator(*BOOL_FIELDS, mode="before")
    @classmethod
    def _coerce_bool(cls, v: Any) -> Any:
        if isinstance(v, str):
            key = v.strip().casefold()
            if key in _TRUE:
                return True
            if key in _FALSE:
                return False
            raise ValueError(f"not a boolean: {v!r}")
        return v

    @field_validator(*TEXT_FIELDS, mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> Any:
        if isinstance(v, int | float) and not isinstance(v, bool):
            return str(v)
        if isinstance(v, list):
            vals = [str(i).strip() for i in v if not _is_empty(i)]
            return ", ".join(vals) if vals else None
        if isinstance(v, str):
            return v.strip()
        return v

    def to_compact_dict(self) -> dict[str, Any]:
        """Only the fields the user actually mentioned (None / empty removed)."""
        return {k: v for k, v in self.model_dump(exclude_none=True).items() if not _is_empty(v)}

    def is_empty(self) -> bool:
        return not self.to_compact_dict()
