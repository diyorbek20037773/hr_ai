"""Phase 8 hardening contract, written from the spec (not from the implementation)."""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.llm_service import FakeLLMService, LLMBusyError, LLMTimeoutError
from app.services.search_client import HrSearchClient
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

P = "/api/v1/ai-search"
HR_URL = "http://hr.test"
Q1 = "Oliy ma'lumotli, 30 yoshdan katta ayollar"
Q1_OUT = '{"education":"Oliy","fromAge":30,"sex":"Ayol"}'
Q_EMPTY = "yaxshi kadrlarni top"
Q_BAD_AGE = "40 yoshdan katta, 30 yoshgacha"
Q_BAD_AGE_OUT = '{"fromAge":40,"toAge":30}'
Q_AGE_ONLY = "30 yoshdan katta"
Q_AGE_ONLY_OUT = '{"fromAge":30}'
RESPONSES = {Q1: Q1_OUT, Q_EMPTY: "{}", Q_BAD_AGE: Q_BAD_AGE_OUT, Q_AGE_ONLY: Q_AGE_ONLY_OUT}
HR_RESULTS = {"items": [{"id": 1, "fullName": "A"}], "totalItems": 1}


def _envelope(resp, code):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] == code, body
    assert body["error"]["requestId"]
    return body["error"]


def _q1_refs(raw_references):
    return {k: raw_references[k] for k in ("educations", "sexes")}


def _hr_app(make_app, settings_factory, captured, results=HR_RESULTS, status=200, **overrides):
    app, _ = make_app(RESPONSES, HR_SEARCH_API_URL=HR_URL, **overrides)
    hr_settings = settings_factory(HR_SEARCH_API_URL=HR_URL, **overrides)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(status, json=results)

    def install():
        app.state.hr_client = HrSearchClient(hr_settings, transport=httpx.MockTransport(handler))

    return app, install


def _search(make_app, settings_factory, payload, results=HR_RESULTS, status=200, **overrides):
    captured: list[dict] = []
    app, install = _hr_app(make_app, settings_factory, captured, results=results, status=status, **overrides)
    with TestClient(app) as c:
        install()
        r = c.post(f"{P}/search", json=payload)
    return r, captured


# ---------------------------------------------------------------- referenceSources + local guard

def test_resolve_reference_sources_local_by_default(make_app):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        body = c.post(f"{P}/resolve", json={"query": Q1}).json()
    assert body["referenceSources"] == {"educations": "local", "sexes": "local"}


def test_resolve_reference_sources_request(make_app, raw_references):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        body = c.post(f"{P}/resolve", json={"query": Q1, "referenceData": {"sexes": raw_references["sexes"]}}).json()
    assert body["referenceSources"] == {"educations": "local", "sexes": "request"}


def test_resolve_reference_sources_empty_for_direct_filters(make_app):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        body = c.post(f"{P}/resolve", json={"query": Q_AGE_ONLY}).json()
    assert body["referenceSources"] == {}


def test_search_blocks_local_references_by_default(make_app, settings_factory):
    r, captured = _search(make_app, settings_factory, {"query": Q1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is False
    assert body["reason"] == "LOCAL_REFERENCES_NOT_ALLOWED"
    assert body["referenceSources"] == {"educations": "local", "sexes": "local"}
    assert captured == []


def test_search_blocks_when_only_one_collection_is_local(make_app, settings_factory, raw_references):
    r, captured = _search(make_app, settings_factory,
                          {"query": Q1, "referenceData": {"sexes": raw_references["sexes"]}})
    body = r.json()
    assert body["executed"] is False
    assert body["reason"] == "LOCAL_REFERENCES_NOT_ALLOWED"
    assert captured == []


def test_search_direct_filters_need_no_references(make_app, settings_factory):
    r, captured = _search(make_app, settings_factory, {"query": Q_AGE_ONLY})
    body = r.json()
    assert body["executed"] is True, body
    assert len(captured) == 1
    assert captured[0]["fromAge"] == 30


def test_search_not_configured_reason_first(make_app):
    app, _ = make_app(RESPONSES)  # HR not configured, local refs
    with TestClient(app) as c:
        body = c.post(f"{P}/search", json={"query": Q1}).json()
    assert body["executed"] is False
    assert body["reason"] == "HR_SEARCH_API_NOT_CONFIGURED"
    assert body["referenceSources"] == {"educations": "local", "sexes": "local"}


# ---------------------------------------------------------------- NO_FILTERS / ordering

def test_search_empty_intent_no_filters(make_app, settings_factory):
    r, captured = _search(make_app, settings_factory, {"query": Q_EMPTY})
    assert r.status_code == 200
    body = r.json()
    assert body["readyToSearch"] is True
    assert body["executed"] is False
    assert body["reason"] == "NO_FILTERS"
    assert captured == []


def test_search_empty_intent_no_filters_even_if_local_allowed(make_app, settings_factory):
    r, captured = _search(make_app, settings_factory, {"query": Q_EMPTY}, ALLOW_LOCAL_REFERENCES_FOR_SEARCH=True)
    assert r.json()["reason"] == "NO_FILTERS"
    assert captured == []


def test_search_not_ready_reason_before_local_guard(make_app, settings_factory):
    r, captured = _search(make_app, settings_factory, {"query": Q_BAD_AGE})
    body = r.json()
    assert body["executed"] is False
    assert body["reason"] == "NOT_READY_TO_SEARCH"
    assert captured == []


# ---------------------------------------------------------------- HR upstream malformed

@pytest.mark.parametrize("results", [{"items": "x"}, {"items": [1, 2]}])
def test_search_malformed_upstream_is_502(make_app, settings_factory, raw_references, results):
    r, captured = _search(make_app, settings_factory, {"query": Q1, "referenceData": _q1_refs(raw_references)},
                          results=results)
    assert len(captured) == 1
    assert r.status_code == 502, r.text
    _envelope(r, "HR_SEARCH_FAILED")


def test_search_results_shape(make_app, settings_factory, raw_references):
    r, _ = _search(make_app, settings_factory, {"query": Q1, "referenceData": _q1_refs(raw_references)})
    body = r.json()
    assert body["executed"] is True
    assert body["results"]["items"] == HR_RESULTS["items"]
    assert body["results"]["totalItems"] == 1


# ---------------------------------------------------------------- LLM errors

class _RaisingLLM(FakeLLMService):
    def __init__(self, exc: Exception):
        super().__init__()
        self.exc = exc

    def generate(self, messages):
        raise self.exc


class _NotLoadedLLM(FakeLLMService):
    is_loaded = False


def _app_with(tmp_path, llm, **overrides):
    return create_app(settings=make_settings(tmp_path, **overrides), llm=llm)


@pytest.mark.parametrize("path", ["parse", "resolve", "search"])
def test_llm_busy_503(tmp_path, path):
    with TestClient(_app_with(tmp_path, _RaisingLLM(LLMBusyError("full")))) as c:
        r = c.post(f"{P}/{path}", json={"query": Q1})
    assert r.status_code == 503
    _envelope(r, "LLM_BUSY")


def test_llm_timeout_504(tmp_path):
    with TestClient(_app_with(tmp_path, _RaisingLLM(LLMTimeoutError("slow")))) as c:
        r = c.post(f"{P}/parse", json={"query": Q1})
    assert r.status_code == 504
    _envelope(r, "LLM_TIMEOUT")


def test_unhandled_exception_500_envelope_with_request_id(tmp_path):
    app = _app_with(tmp_path, _RaisingLLM(RuntimeError("kaboom")))
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post(f"{P}/parse", json={"query": Q1}, headers={"X-Request-ID": "boom-req-42"})
    assert r.status_code == 500
    err = _envelope(r, "INTERNAL_ERROR")
    assert err["requestId"] == "boom-req-42"
    assert r.headers.get("X-Request-ID") == "boom-req-42"
    assert "kaboom" not in r.text


def test_model_not_loaded(tmp_path):
    with TestClient(_app_with(tmp_path, _NotLoadedLLM())) as c:
        assert c.get("/ready").status_code == 503
        health = c.get("/health")
        assert health.json()["modelLoaded"] is False
        r = c.post(f"{P}/parse", json={"query": Q1})
    assert r.status_code == 503
    _envelope(r, "MODEL_NOT_READY")


# ---------------------------------------------------------------- request limits

def test_payload_too_large(make_app):
    app, _ = make_app(RESPONSES, MAX_BODY_BYTES=200)
    with TestClient(app) as c:
        r = c.post(f"{P}/parse", json={"query": "a" * 500})
        assert r.status_code == 413
        _envelope(r, "PAYLOAD_TOO_LARGE")
        assert c.post(f"{P}/parse", json={"query": Q1}).status_code == 200


def test_reference_data_unknown_collection(make_app):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        r = c.post(f"{P}/resolve", json={"query": Q1, "referenceData": {"noSuchCollection": [{"id": 1, "name": "x"}]}})
    assert r.status_code == 422
    _envelope(r, "REFERENCE_DATA_INVALID")


def test_reference_data_too_large(make_app):
    app, _ = make_app(RESPONSES, MAX_REFERENCE_ITEMS=2)
    items = [{"id": i, "name": f"Region {i}"} for i in range(3)]
    with TestClient(app) as c:
        r = c.post(f"{P}/resolve", json={"query": Q1, "referenceData": {"regions": items}})
        assert r.status_code == 413
        _envelope(r, "REFERENCE_DATA_TOO_LARGE")
        ok = c.post(f"{P}/resolve", json={"query": Q1, "referenceData": {"regions": items[:2]}})
        assert ok.status_code == 200


# ---------------------------------------------------------------- references endpoint

def test_references_have_source_local(make_app):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        body = c.get("/api/v1/references").json()
    entries = body.get("collections", body) if isinstance(body, dict) else body
    assert "regions" in entries, body
    for name, entry in entries.items():
        assert entry["source"] == "local", name


# ---------------------------------------------------------------- privacy

def test_resolve_does_not_log_raw_query_at_info(tmp_path, caplog):
    query = "Zyxwvuq Qqqmarkerov familiyali xodim"
    llm = FakeLLMService({query: '{"lastName":"Qqqmarkerov"}'})
    app = _app_with(tmp_path, llm, LOG_LEVEL="INFO")
    caplog.set_level(logging.INFO)
    with TestClient(app) as c:
        caplog.clear()
        caplog.set_level(logging.INFO)
        r = c.post(f"{P}/resolve", json={"query": query})
    assert r.status_code == 200
    records = [rec for rec in caplog.records if rec.levelno >= logging.INFO]
    assert any(rec.getMessage() == "resolve_done" for rec in records), [rec.getMessage() for rec in records]
    for rec in records:
        dump = rec.getMessage() + " " + " ".join(str(v) for v in vars(rec).values())
        assert "Zyxwvuq" not in dump, rec.getMessage()
        assert "Qqqmarkerov" not in dump, rec.getMessage()
