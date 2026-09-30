"""Natural language -> ParsedSearchIntent via the local LLM, with robust JSON parsing and one retry."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.logging import get_logger, query_fingerprint
from app.core.metrics import INTENT_CORRECTIONS, LLM_JSON_RETRY, LLM_REQUESTS
from app.models.ai_intent import ParsedSearchIntent
from app.prompts.search_parser import PROMPT_VERSION, build_messages, build_retry_messages
from app.prompts.store import get_active
from app.services.intent_guards import apply_all
from app.services.llm_service import BaseLLMService, LLMTimeoutError
from app.utils.json_parser import JsonExtractionError, extract_json_object

log = get_logger(__name__)


class IntentParseError(Exception):
    """The model did not produce a valid intent even after the corrective retry."""

    code = "LLM_OUTPUT_INVALID"


@dataclass
class ParseOutcome:
    intent: ParsedSearchIntent
    attempts: int
    raw_output: str
    prompt_version: str = PROMPT_VERSION
    corrections: list[str] = field(default_factory=list)  # codes from app/services/intent_guards.py


def _validate(raw: str) -> ParsedSearchIntent:
    obj = extract_json_object(raw)
    return ParsedSearchIntent.model_validate(obj)


def _finish(query: str, intent: ParsedSearchIntent, attempts: int, raw: str, version: str) -> ParseOutcome:
    """Apply the deterministic guards (outside the JSON-validation try, so a guard bug is never a retry)."""
    intent, fixes = apply_all(query, intent)
    for kind in fixes:
        INTENT_CORRECTIONS.labels(kind=kind).inc()
    if fixes:
        log.info("intent_corrected", extra={**query_fingerprint(query), "fixes": fixes})
    LLM_REQUESTS.labels(status="ok").inc()
    return ParseOutcome(intent=intent, attempts=attempts, raw_output=raw, prompt_version=version, corrections=fixes)


class IntentParser:
    def __init__(self, llm: BaseLLMService):
        self.llm = llm

    def parse(self, query: str) -> ParseOutcome:
        """Blocking call — run it in a thread pool from async code."""
        query = query.strip()
        cfg = get_active()  # one prompt for the whole request, including the corrective retry
        log.debug("parse_query", extra={"query": query})
        try:
            raw = self.llm.generate(build_messages(query, cfg))
        except LLMTimeoutError:
            LLM_REQUESTS.labels(status="timeout").inc()
            raise
        except Exception:
            LLM_REQUESTS.labels(status="error").inc()
            raise
        try:
            intent = _validate(raw)
        except (JsonExtractionError, ValidationError) as first_error:
            log.warning("llm_json_invalid_retrying",
                        extra={**query_fingerprint(query), "error": type(first_error).__name__})
            log.debug("llm_bad_output", extra={"output": raw[:500]})
        else:
            return _finish(query, intent, 1, raw, cfg.version)

        LLM_JSON_RETRY.inc()
        try:
            raw2 = self.llm.generate(build_retry_messages(query, raw, cfg))
        except LLMTimeoutError:
            LLM_REQUESTS.labels(status="timeout").inc()
            raise
        except Exception:
            LLM_REQUESTS.labels(status="error").inc()
            raise
        try:
            intent = _validate(raw2)
        except (JsonExtractionError, ValidationError) as exc:
            LLM_REQUESTS.labels(status="json_error").inc()
            log.warning("llm_json_invalid", extra={**query_fingerprint(query), "error": type(exc).__name__})
            raise IntentParseError("model output is not a valid search intent") from exc
        return _finish(query, intent, 2, raw2, cfg.version)
