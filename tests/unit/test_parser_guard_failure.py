"""Spec tests (review items 3-4): ParseOutcome.corrections and guard failures are not JSON errors."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

import app.services.intent_parser as intent_parser_mod
from app.models.ai_intent import ParsedSearchIntent
from app.services.intent_parser import IntentParseError, IntentParser, ParseOutcome
from app.services.llm_service import FakeLLMService

Q = "30 ёшдан катта аёллар"


def _llm(status: str) -> float:
    return REGISTRY.get_sample_value("hr_ai_llm_requests_total", {"status": status}) or 0.0


def _retries() -> float:
    return REGISTRY.get_sample_value("hr_ai_llm_json_retry_total") or 0.0


def _boom(query, intent):
    raise RuntimeError("guard bug")


def test_parse_outcome_has_corrections_default_empty():
    o = ParseOutcome(intent=ParsedSearchIntent(), attempts=1, raw_output="{}")
    assert o.corrections == []
    o2 = ParseOutcome(intent=ParsedSearchIntent(), attempts=1, raw_output="{}")
    o.corrections.append("x")
    assert o2.corrections == []  # no shared mutable default


def test_corrections_list_age_fix():
    outcome = IntentParser(FakeLLMService({Q: '{"toAge":30}'})).parse(Q)
    assert isinstance(outcome.corrections, list)
    assert "toAge->fromAge" in outcome.corrections
    assert outcome.intent.fromAge == 30 and outcome.intent.toAge is None


def test_corrections_empty_when_clean():
    q = "30 yoshdan katta ayollar"
    outcome = IntentParser(FakeLLMService({q: '{"fromAge":30,"sex":"Ayol"}'})).parse(q)
    assert outcome.corrections == []


def test_corrections_after_retry():
    fake = FakeLLMService({Q: ["garbage", '{"toAge":30}']})
    outcome = IntentParser(fake).parse(Q)
    assert outcome.attempts == 2
    assert "toAge->fromAge" in outcome.corrections


def test_guard_exception_is_not_retried_and_not_json_error(monkeypatch):
    monkeypatch.setattr(intent_parser_mod, "apply_all", _boom)
    fake = FakeLLMService({Q: '{"toAge":30}'})
    json_err, retries = _llm("json_error"), _retries()
    with pytest.raises(RuntimeError, match="guard bug") as exc:
        IntentParser(fake).parse(Q)
    assert not isinstance(exc.value, IntentParseError)
    assert len(fake.calls) == 1, "a guard failure must not trigger the corrective retry"
    assert _llm("json_error") == json_err
    assert _retries() == retries


def test_guard_exception_on_retry_path_propagates(monkeypatch):
    monkeypatch.setattr(intent_parser_mod, "apply_all", _boom)
    fake = FakeLLMService({Q: ["garbage", '{"toAge":30}']})
    json_err = _llm("json_error")
    with pytest.raises(RuntimeError, match="guard bug") as exc:
        IntentParser(fake).parse(Q)
    assert not isinstance(exc.value, IntentParseError)
    assert len(fake.calls) == 2  # one corrective retry for the invalid JSON, none for the guard
    assert _llm("json_error") == json_err
