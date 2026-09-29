"""Conservative extraction of one JSON object from LLM output. Never evaluates anything."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class JsonExtractionError(ValueError):
    pass


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def _repair(text: str) -> str:
    fixed = _TRAILING_COMMA_RE.sub(r"\1", text)
    # single-quoted keys/values -> double quotes, only when the text has no double quotes at all
    # (Uzbek apostrophes like "O'rta" make blanket replacement unsafe).
    if '"' not in fixed and "'" in fixed:
        fixed = re.sub(r"'([^']*)'", r'"\1"', fixed)
    return fixed


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def extract_json_object(raw: str) -> dict[str, Any]:
    """json.loads -> strip fences -> first balanced {...} -> conservative repair. Raises JsonExtractionError."""
    if raw is None:
        raise JsonExtractionError("empty output")
    text = raw.strip()
    if not text:
        raise JsonExtractionError("empty output")

    attempts: list[str] = [text]
    fence = _FENCE_RE.search(text)
    if fence:
        attempts.append(fence.group(1).strip())
    for candidate in list(attempts):
        obj = _first_balanced_object(candidate)
        if obj:
            attempts.append(obj)

    for candidate in attempts:
        parsed = _loads_object(candidate)
        if parsed is not None:
            return parsed
    for candidate in attempts[1:] or attempts:
        parsed = _loads_object(_repair(candidate))
        if parsed is not None:
            return parsed
    raise JsonExtractionError("no valid JSON object found in model output")
