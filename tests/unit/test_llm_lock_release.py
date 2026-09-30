"""Spec test (review item 5): QwenLLMService.generate always releases its lock, even if empty_cache raises.

No real model and no GPU: _model/_tokenizer are replaced with tiny CPU fakes and torch.cuda.empty_cache is
monkeypatched to raise.
"""

from __future__ import annotations

import contextlib

import pytest

from app.services.llm_service import QwenLLMService
from tests.conftest import make_settings

torch = pytest.importorskip("torch")

MSGS = [{"role": "user", "content": "x"}]


class _Batch(dict):
    def to(self, device):
        return self


class _FakeTokenizer:
    eos_token_id = 0

    def __call__(self, texts, return_tensors=None):
        return _Batch(input_ids=torch.tensor([[1, 2, 3]]), attention_mask=torch.tensor([[1, 1, 1]]))

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "rendered prompt"

    def decode(self, tokens, skip_special_tokens=True):
        return "{}"


class _FakeModel:
    device = "cpu"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("generate failed")
        return torch.tensor([[1, 2, 3, 4, 5]])


def _service(tmp_path, model) -> QwenLLMService:
    svc = QwenLLMService(make_settings(tmp_path, LLM_TIMEOUT_SECONDS=5))
    svc._model = model
    svc._tokenizer = _FakeTokenizer()
    svc._device = "cuda"
    return svc


def _raising_empty_cache(calls: list):
    def _boom():
        calls.append(1)
        raise RuntimeError("empty_cache failed")

    return _boom


def _assert_lock_free(svc: QwenLLMService):
    assert svc._lock.acquire(blocking=False), "lock still held after generate"
    svc._lock.release()
    assert svc._waiting == 0


def test_lock_released_when_empty_cache_raises(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(torch.cuda, "empty_cache", _raising_empty_cache(calls))
    svc = _service(tmp_path, _FakeModel())
    with contextlib.suppress(Exception):  # the contract is about the lock; the call may return or raise
        svc.generate(MSGS)
    assert calls, "empty_cache was expected to be called on a cuda device"
    _assert_lock_free(svc)


def test_repeated_calls_not_blocked_after_empty_cache_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, "empty_cache", _raising_empty_cache([]))
    model = _FakeModel()
    svc = _service(tmp_path, model)
    for _ in range(3):
        with contextlib.suppress(Exception):
            svc.generate(MSGS)
    assert model.calls == 3
    _assert_lock_free(svc)


def test_generate_result_survives_empty_cache_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, "empty_cache", _raising_empty_cache([]))
    svc = _service(tmp_path, _FakeModel())
    assert svc.generate(MSGS) == "{}"
    _assert_lock_free(svc)


def test_lock_released_when_generate_and_empty_cache_both_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, "empty_cache", _raising_empty_cache([]))
    svc = _service(tmp_path, _FakeModel(fail=True))
    with pytest.raises(RuntimeError, match="generate failed"):
        svc.generate(MSGS)
    _assert_lock_free(svc)


def test_cpu_device_does_not_call_empty_cache(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(torch.cuda, "empty_cache", _raising_empty_cache(calls))
    svc = _service(tmp_path, _FakeModel())
    svc._device = "cpu"
    assert svc.generate(MSGS) == "{}"
    assert calls == []
    _assert_lock_free(svc)
