import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.search_client import HrSearchClient

pytestmark = pytest.mark.integration

P = "/api/v1/ai-search"
Q1 = "Oliy ma'lumotli, 30 yoshdan katta ayollar"
Q1_OUT = '{"education":"Oliy","fromAge":30,"sex":"Ayol"}'
Q_BAD_AGE = "40 yoshdan katta, 30 yoshgacha"
Q_BAD_AGE_OUT = '{"fromAge":40,"toAge":30}'
Q_GARBAGE = "buzilgan javob"
RESPONSES = {Q1: Q1_OUT, Q_BAD_AGE: Q_BAD_AGE_OUT, Q_GARBAGE: "this is not json"}

HR_RESULTS = {
    "items": [
        {"id": 7, "fullName": "Karimova Dilnoza", "pinfl": "31234567890123", "birthOfDate": "1990-01-02T00:00:00",
         "institutionName": "Vazirlik", "positionName": "Bosh mutaxassis", "institutionId": 3, "soato": 1726,
         "customUpstreamField": {"nested": [1, 2]}},
    ],
    "totalItems": 1,
}


def _assert_envelope(resp, code=None):
    body = resp.json()
    assert set(body) == {"error"}, body
    err = body["error"]
    assert {"code", "message", "requestId"} <= set(err)
    assert isinstance(err["code"], str) and err["code"]
    assert isinstance(err["message"], str)
    assert err["requestId"]
    if code:
        assert err["code"] == code
    return err


@pytest.fixture
def client(make_app):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        yield c


def _expected_q1(ref_id):
    return {"educationId": ref_id("educations", "Oliy"), "fromAge": 30, "sexId": ref_id("sexes", "Ayol"),
            "academicTitleId": 0, "first": 0, "rows": 20}


# ---------------------------------------------------------------- health / ready / metrics

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    for key in ("status", "modelLoaded", "modelPath", "device", "dtype", "promptVersion", "uptimeSeconds"):
        assert key in body, key
    assert body["modelLoaded"] is True


def test_ready(client):
    assert client.get("/ready").status_code == 200


def test_metrics_after_request(client):
    client.post(f"{P}/parse", json={"query": Q1})
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "hr_ai_" in r.text
    assert "hr_ai_http_requests_total" in r.text


# ---------------------------------------------------------------- parse / resolve

def test_parse(client):
    r = client.post(f"{P}/parse", json={"query": Q1})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == Q1
    assert body["intent"] == {"education": "Oliy", "fromAge": 30, "sex": "Ayol"}
    assert body["promptVersion"]
    assert isinstance(body["latencyMs"], int)


def test_parse_empty_intent(client):
    r = client.post(f"{P}/parse", json={"query": "yaxshi kadrlarni top"})
    assert r.status_code == 200
    assert r.json()["intent"] == {}


def test_resolve(client, ref_id):
    r = client.post(f"{P}/resolve", json={"query": Q1})
    assert r.status_code == 200
    body = r.json()
    for key in ("query", "intent", "searchModel", "recognizedFilters", "unresolvedFilters", "warnings",
                "readyToSearch", "latencyMs"):
        assert key in body, key
    assert body["searchModel"] == _expected_q1(ref_id)
    assert body["readyToSearch"] is True
    assert body["unresolvedFilters"] == []
    assert {f["field"] for f in body["recognizedFilters"]} == {"educationId", "fromAge", "sexId"}


def test_resolve_first_rows(client):
    r = client.post(f"{P}/resolve", json={"query": Q1, "first": 20, "rows": 50})
    assert r.status_code == 200
    sm = r.json()["searchModel"]
    assert sm["first"] == 20 and sm["rows"] == 50


def test_resolve_request_reference_data_has_priority(client):
    ref = {"educations": [{"id": 777, "name": "Oliy"}], "sexes": [{"id": 888, "name": "Ayol"}]}
    r = client.post(f"{P}/resolve", json={"query": Q1, "referenceData": ref})
    assert r.status_code == 200
    sm = r.json()["searchModel"]
    assert sm["educationId"] == 777
    assert sm["sexId"] == 888


def test_resolve_validation_not_ready(client):
    r = client.post(f"{P}/resolve", json={"query": Q_BAD_AGE})
    assert r.status_code == 200
    body = r.json()
    assert body["readyToSearch"] is False
    assert body["warnings"]


# ---------------------------------------------------------------- search

def test_search_not_configured(client, ref_id):
    r = client.post(f"{P}/search", json={"query": Q1})
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] is False
    assert body["reason"] == "HR_SEARCH_API_NOT_CONFIGURED"
    assert body["searchModel"] == _expected_q1(ref_id)


def _mock_hr(app, settings, captured, results=HR_RESULTS, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"url": str(request.url), "method": request.method, "body": json.loads(request.content)})
        return httpx.Response(status, json=results)

    app.state.hr_client = HrSearchClient(settings, transport=httpx.MockTransport(handler))


def _q1_request_refs(raw_references):
    """Q1's collections supplied by the caller, so their source is "request" (not local placeholder data)."""
    return {k: raw_references[k] for k in ("educations", "sexes")}


def test_search_with_mocked_hr_api(make_app, settings_factory, ref_id, raw_references):
    hr_settings = settings_factory(HR_SEARCH_API_URL="http://hr.test", HR_SEARCH_API_PATH="/api/search")
    app, _ = make_app(RESPONSES, HR_SEARCH_API_URL="http://hr.test", HR_SEARCH_API_PATH="/api/search")
    captured: list[dict] = []
    with TestClient(app) as c:
        _mock_hr(app, hr_settings, captured)
        r = c.post(f"{P}/search", json={"query": Q1, "referenceData": _q1_request_refs(raw_references)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True
    assert body["results"] == HR_RESULTS  # passed through untouched
    assert len(captured) == 1
    assert captured[0]["method"] == "POST"
    assert captured[0]["url"] == "http://hr.test/api/search"
    assert captured[0]["body"] == body["searchModel"]
    assert body["searchModel"] == _expected_q1(ref_id)
    assert body["referenceSources"] == {"educations": "request", "sexes": "request"}


def test_search_with_local_references_allowed_by_setting(make_app, settings_factory, ref_id):
    hr_settings = settings_factory(HR_SEARCH_API_URL="http://hr.test", ALLOW_LOCAL_REFERENCES_FOR_SEARCH=True)
    app, _ = make_app(RESPONSES, HR_SEARCH_API_URL="http://hr.test", ALLOW_LOCAL_REFERENCES_FOR_SEARCH=True)
    captured: list[dict] = []
    with TestClient(app) as c:
        _mock_hr(app, hr_settings, captured)
        r = c.post(f"{P}/search", json={"query": Q1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True
    assert len(captured) == 1
    assert body["searchModel"] == _expected_q1(ref_id)
    assert set(body["referenceSources"].values()) == {"local"}


def test_search_not_ready_does_not_call_hr(make_app, settings_factory):
    hr_settings = settings_factory(HR_SEARCH_API_URL="http://hr.test")
    app, _ = make_app(RESPONSES, HR_SEARCH_API_URL="http://hr.test")
    captured: list[dict] = []
    with TestClient(app) as c:
        _mock_hr(app, hr_settings, captured)
        r = c.post(f"{P}/search", json={"query": Q_BAD_AGE})
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] is False
    assert captured == []


def test_search_hr_api_error_is_envelope(make_app, settings_factory, raw_references):
    hr_settings = settings_factory(HR_SEARCH_API_URL="http://hr.test")
    app, _ = make_app(RESPONSES, HR_SEARCH_API_URL="http://hr.test")
    captured: list[dict] = []
    with TestClient(app) as c:
        _mock_hr(app, hr_settings, captured,
                 results={"error": "boom"}, status=500)
        r = c.post(f"{P}/search", json={"query": Q1, "referenceData": _q1_request_refs(raw_references)})
    assert len(captured) == 1
    assert r.status_code >= 500
    _assert_envelope(r, code="HR_SEARCH_FAILED")


# ---------------------------------------------------------------- references

def test_references(client, ref_id):
    r = client.get("/api/v1/references")
    assert r.status_code == 200
    body = r.json()
    text = json.dumps(body, ensure_ascii=False)
    assert "regions" in text
    assert "Toshkent shahri" in text


# ---------------------------------------------------------------- request id, api key, errors

def test_request_id_echoed(client):
    r = client.get("/health", headers={"X-Request-ID": "test-req-123"})
    assert r.headers.get("X-Request-ID") == "test-req-123"


def test_request_id_generated(client):
    r = client.post(f"{P}/parse", json={"query": Q1})
    assert r.headers.get("X-Request-ID")


def test_error_request_id_matches_header(client):
    r = client.post(f"{P}/parse", json={}, headers={"X-Request-ID": "err-req-1"})
    assert r.status_code == 422
    err = _assert_envelope(r)
    assert err["requestId"] == "err-req-1"


def test_api_key_off_by_default(client):
    assert client.post(f"{P}/parse", json={"query": Q1}).status_code == 200


def test_api_key_enforced(make_app):
    app, _ = make_app(RESPONSES, AI_SEARCH_API_KEY="s3cret")
    with TestClient(app) as c:
        missing = c.post(f"{P}/parse", json={"query": Q1})
        assert missing.status_code in (401, 403)
        _assert_envelope(missing)
        wrong = c.post(f"{P}/parse", json={"query": Q1}, headers={"X-API-Key": "nope"})
        assert wrong.status_code in (401, 403)
        _assert_envelope(wrong)
        ok = c.post(f"{P}/parse", json={"query": Q1}, headers={"X-API-Key": "s3cret"})
        assert ok.status_code == 200
        for path in ("resolve", "search"):
            assert c.post(f"{P}/{path}", json={"query": Q1}).status_code in (401, 403)
            assert c.post(f"{P}/{path}", json={"query": Q1}, headers={"X-API-Key": "s3cret"}).status_code == 200


def test_api_key_logic_via_global_settings_override(make_app, settings_factory):
    """Same check, with get_settings overridden: verifies the key logic itself (see bug: create_app settings)."""
    from app.core.config import get_settings

    keyed = settings_factory(AI_SEARCH_API_KEY="s3cret")
    app, _ = make_app(RESPONSES, AI_SEARCH_API_KEY="s3cret")
    app.dependency_overrides[get_settings] = lambda: keyed
    with TestClient(app) as c:
        missing = c.post(f"{P}/parse", json={"query": Q1})
        assert missing.status_code in (401, 403)
        _assert_envelope(missing)
        assert c.post(f"{P}/parse", json={"query": Q1}, headers={"X-API-Key": "s3cret"}).status_code == 200


def test_query_too_long_rejected(client):
    r = client.post(f"{P}/parse", json={"query": "a" * 1001})
    assert 400 <= r.status_code < 500
    _assert_envelope(r)


def test_query_at_limit_accepted(client):
    assert client.post(f"{P}/parse", json={"query": "a" * 1000}).status_code == 200


def test_invalid_body_envelope(client):
    r = client.post(f"{P}/parse", json={"q": "x"})
    assert r.status_code == 422
    _assert_envelope(r)


def test_invalid_llm_output(client):
    r = client.post(f"{P}/parse", json={"query": Q_GARBAGE})
    assert r.status_code == 422
    _assert_envelope(r, code="LLM_OUTPUT_INVALID")


def test_invalid_llm_output_on_resolve(client):
    r = client.post(f"{P}/resolve", json={"query": Q_GARBAGE})
    assert r.status_code == 422
    _assert_envelope(r, code="LLM_OUTPUT_INVALID")


def test_unknown_route_envelope(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    _assert_envelope(r)
