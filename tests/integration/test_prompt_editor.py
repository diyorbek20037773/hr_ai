"""Prompt viewer/editor contract (runtime override, memory only), written from the spec."""

from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.models.ai_intent import ParsedSearchIntent
from app.prompts import store
from app.prompts.search_parser import (
    FEW_SHOTS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_messages,
    build_retry_messages,
)
from app.services.intent_parser import IntentParser
from app.services.llm_service import FakeLLMService

pytestmark = pytest.mark.integration

P = "/api/v1/prompt"
S = "/api/v1/ai-search"
Q1 = "Oliy ma'lumotli, 30 yoshdan katta ayollar"
Q1_OUT = '{"education":"Oliy","fromAge":30,"sex":"Ayol"}'
CUSTOM_SYS = "CUSTOM-SYSTEM-PROMPT-MARKER-QWERTY: extract HR filters as compact JSON only."
CUSTOM_VERSION = f"{PROMPT_VERSION}+custom"
GOOD_SHOTS = [
    {"query": "30 yoshdan katta ayollar", "answer": {"fromAge": 30, "sex": "Ayol"}},
    {"query": "yaxshi kadrlar", "answer": {}},
]


@pytest.fixture(autouse=True)
def _restore_prompt():
    store.reset()
    yield
    store.reset()


def _envelope(resp, code):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] == code, body
    return body["error"]


def _put_body(system=CUSTOM_SYS, shots=None):
    return {"systemPrompt": system, "fewShots": GOOD_SHOTS if shots is None else shots}


# ---------------------------------------------------------------- store (unit-level)

def test_store_default_matches_search_parser():
    d = store.DEFAULT
    assert d.system_prompt == SYSTEM_PROMPT
    assert d.version == PROMPT_VERSION
    assert d.is_custom is False
    assert isinstance(d.few_shots, tuple)
    assert [(q, a) for q, a in d.few_shots] == [(q, a) for q, a in FEW_SHOTS]
    assert store.get_active() == d


def test_store_set_custom_and_reset():
    cfg = store.set_custom(CUSTOM_SYS, [("q1", {"fromAge": 30})])
    assert cfg.is_custom is True
    assert cfg.version == CUSTOM_VERSION
    assert cfg.system_prompt == CUSTOM_SYS
    assert tuple(cfg.few_shots) == (("q1", {"fromAge": 30}),)
    assert store.get_active() == cfg
    back = store.reset()
    assert back == store.DEFAULT
    assert store.get_active() == store.DEFAULT
    assert store.get_active().is_custom is False


def test_build_messages_uses_active_or_explicit_cfg():
    default_msgs = build_messages("hello query")
    assert default_msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert default_msgs[-1] == {"role": "user", "content": "hello query"}

    cfg = store.set_custom(CUSTOM_SYS, [("shot q", {"sex": "Ayol"})])
    active = build_messages("hello query")
    assert active[0]["content"] == CUSTOM_SYS
    assert len(active) == 4
    assert active[1] == {"role": "user", "content": "shot q"}
    assert active[2]["role"] == "assistant"
    assert json.loads(active[2]["content"]) == {"sex": "Ayol"}
    assert active[3] == {"role": "user", "content": "hello query"}

    explicit = build_messages("hello query", store.DEFAULT)
    assert explicit == default_msgs
    assert build_messages("hello query", cfg) == active


def test_build_retry_messages_uses_cfg():
    cfg = store.set_custom(CUSTOM_SYS, [])
    msgs = build_retry_messages("q", "not json")
    assert msgs[0]["content"] == CUSTOM_SYS
    assert msgs[-1]["role"] == "user"
    assert any(m["role"] == "assistant" and m["content"] == "not json" for m in msgs)
    assert any(m["role"] == "user" and m["content"] == "q" for m in msgs)
    msgs_default = build_retry_messages("q", "not json", store.DEFAULT)
    assert msgs_default[0]["content"] == SYSTEM_PROMPT
    assert build_retry_messages("q", "not json", cfg)[0]["content"] == CUSTOM_SYS


def test_parser_uses_one_cfg_for_first_attempt_and_retry():
    """Prompt swapped between attempt 1 and the retry must not leak into the retry."""
    store.set_custom(CUSTOM_SYS, [])

    class SwappingLLM(FakeLLMService):
        def generate(self, messages):
            out = super().generate(messages)
            store.reset()  # simulate a concurrent DELETE after the first attempt
            return out

    llm = SwappingLLM({"30 yoshdan katta": ["not json at all", '{"fromAge":30}']})
    outcome = IntentParser(llm).parse("30 yoshdan katta")
    assert len(llm.calls) == 2, llm.calls
    assert llm.calls[0][0]["content"] == CUSTOM_SYS
    assert llm.calls[1][0]["content"] == CUSTOM_SYS
    assert outcome.prompt_version == CUSTOM_VERSION
    assert outcome.intent.fromAge == 30


def test_parser_default_version():
    outcome = IntentParser(FakeLLMService({"x": "{}"})).parse("x")
    assert outcome.prompt_version == PROMPT_VERSION


# ---------------------------------------------------------------- GET

def test_get_prompt_default_state(make_app):
    app, _ = make_app()
    with TestClient(app) as c:
        r = c.get(P)
    assert r.status_code == 200, r.text
    b = r.json()
    for key in ("version", "defaultVersion", "isCustom", "editingEnabled", "systemPrompt", "fewShots",
                "retryMessage", "tokenCount", "tokenCountQuery", "tokenLimit", "intentKeys", "warnings"):
        assert key in b, key
    assert b["version"] == PROMPT_VERSION
    assert b["defaultVersion"] == PROMPT_VERSION
    assert b["isCustom"] is False
    assert b["editingEnabled"] is True
    assert b["systemPrompt"] == SYSTEM_PROMPT
    assert b["fewShots"] == [{"query": q, "answer": a} for q, a in FEW_SHOTS]
    assert isinstance(b["retryMessage"], str) and b["retryMessage"]
    assert b["tokenCount"] is None
    assert b["tokenLimit"] == 1800
    assert b["intentKeys"] == sorted(ParsedSearchIntent.model_fields)
    assert b["warnings"] == []


def test_get_prompt_token_limit_from_settings(make_app):
    app, _ = make_app(PROMPT_MAX_TOKENS=1234)
    with TestClient(app) as c:
        assert c.get(P).json()["tokenLimit"] == 1234


def test_prompt_endpoints_require_api_key_when_configured(make_app):
    app, _ = make_app(AI_SEARCH_API_KEY="secret-key-123")
    with TestClient(app) as c:
        assert c.get(P).status_code == 401
        assert c.put(P, json=_put_body()).status_code == 401
        assert c.delete(P).status_code == 401
        assert c.post(f"{P}/render", json={"query": "x"}).status_code == 401
        assert store.get_active().is_custom is False
        ok = c.get(P, headers={"X-API-Key": "secret-key-123"})
        assert ok.status_code == 200


# ---------------------------------------------------------------- PUT / DELETE

def test_put_prompt_sets_custom(make_app):
    app, _ = make_app()
    with TestClient(app) as c:
        r = c.put(P, json=_put_body())
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["isCustom"] is True
        assert b["version"] == CUSTOM_VERSION
        assert b["defaultVersion"] == PROMPT_VERSION
        assert b["systemPrompt"] == CUSTOM_SYS
        assert b["fewShots"] == GOOD_SHOTS
        assert any("NOT been evaluated" in w for w in b["warnings"]), b["warnings"]
        g = c.get(P).json()
        assert g["isCustom"] is True and g["version"] == CUSTOM_VERSION and g["systemPrompt"] == CUSTOM_SYS
    assert store.get_active().is_custom is True


def test_put_then_parse_resolve_search_and_health_report_custom(make_app):
    app, fake = make_app({Q1: Q1_OUT})
    with TestClient(app) as c:
        assert c.put(P, json=_put_body()).status_code == 200
        for ep in ("parse", "resolve", "search"):
            r = c.post(f"{S}/{ep}", json={"query": Q1})
            assert r.status_code == 200, (ep, r.text)
            assert r.json()["promptVersion"] == CUSTOM_VERSION, ep
        h = c.get("/health").json()
        assert h["promptVersion"] == CUSTOM_VERSION
        assert h["promptCustom"] is True
        assert fake.calls
        for msgs in fake.calls:
            assert msgs[0]["role"] == "system"
            assert msgs[0]["content"] == CUSTOM_SYS
            assert msgs[-1] == {"role": "user", "content": Q1}
            # the custom few-shots are used, not the default ones
            assert {"role": "user", "content": GOOD_SHOTS[0]["query"]} in msgs

        d = c.delete(P)
        assert d.status_code == 200
        r = c.post(f"{S}/parse", json={"query": Q1})
        assert r.json()["promptVersion"] == PROMPT_VERSION
        assert fake.calls[-1][0]["content"] == SYSTEM_PROMPT
        h = c.get("/health").json()
        assert h["promptVersion"] == PROMPT_VERSION
        assert h["promptCustom"] is False


def test_delete_restores_default(make_app):
    app, _ = make_app()
    with TestClient(app) as c:
        c.put(P, json=_put_body())
        r = c.delete(P)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["isCustom"] is False
    assert b["version"] == PROMPT_VERSION
    assert b["systemPrompt"] == SYSTEM_PROMPT
    assert store.get_active() == store.DEFAULT


@pytest.mark.parametrize(
    "answer",
    [
        {"regionId": 14},
        {"foo": "bar"},
        {"specialistIds": [25]},
        {"fromAge": "abc"},
        {"fromAge": "30"},
        {"typeOfPosition": "Leader"},
    ],
)
def test_put_rejects_bad_answers(make_app, answer):
    app, _ = make_app()
    with TestClient(app) as c:
        before = c.get(P).json()
        r = c.put(P, json=_put_body(shots=[{"query": "q", "answer": answer}]))
        assert r.status_code == 422, r.text
        _envelope(r, "PROMPT_INVALID")
        after = c.get(P).json()
    assert after == before
    assert store.get_active() == store.DEFAULT


def test_put_rejection_keeps_existing_custom(make_app):
    app, _ = make_app()
    with TestClient(app) as c:
        assert c.put(P, json=_put_body()).status_code == 200
        r = c.put(P, json=_put_body(system="another prompt that is long enough",
                                    shots=[{"query": "q", "answer": {"regionId": 1}}]))
        assert r.status_code == 422
        assert c.get(P).json()["systemPrompt"] == CUSTOM_SYS


@pytest.mark.parametrize(
    "body",
    [
        {"systemPrompt": "too short", "fewShots": []},
        {"systemPrompt": "x" * 20001, "fewShots": []},
        {"systemPrompt": CUSTOM_SYS, "fewShots": [{"query": f"q{i}", "answer": {}} for i in range(41)]},
        {"systemPrompt": CUSTOM_SYS, "fewShots": [{"query": "", "answer": {}}]},
        {"systemPrompt": CUSTOM_SYS, "fewShots": [{"query": "q" * 1001, "answer": {}}]},
        {"systemPrompt": CUSTOM_SYS, "fewShots": [{"query": "q", "answer": "notadict"}]},
        {"fewShots": []},
    ],
)
def test_put_body_validation(make_app, body):
    app, _ = make_app()
    with TestClient(app) as c:
        r = c.put(P, json=body)
    assert r.status_code == 422, r.text
    _envelope(r, "VALIDATION_ERROR")
    assert store.get_active() == store.DEFAULT


def test_put_accepts_boundaries(make_app):
    app, _ = make_app()
    shots = [{"query": f"q{i}", "answer": {}} for i in range(40)]
    with TestClient(app) as c:
        r = c.put(P, json={"systemPrompt": "x" * 20, "fewShots": shots})
        assert r.status_code == 200, r.text
        r = c.put(P, json={"systemPrompt": "x" * 20000, "fewShots": [{"query": "q" * 1000, "answer": {}}]})
        assert r.status_code == 200, r.text


def test_put_accepts_canonical_list_position(make_app):
    ans = ParsedSearchIntent.model_validate({"typeOfPosition": ["Leader"]}).to_compact_dict()
    app, _ = make_app()
    with TestClient(app) as c:
        r = c.put(P, json=_put_body(shots=[{"query": "rahbarlar", "answer": ans}]))
    assert r.status_code == 200, r.text


def test_editing_disabled(make_app):
    app, _ = make_app(PROMPT_EDITING_ENABLED=False)
    with TestClient(app) as c:
        g = c.get(P)
        assert g.status_code == 200
        assert g.json()["editingEnabled"] is False
        r = c.put(P, json=_put_body())
        assert r.status_code == 403
        _envelope(r, "PROMPT_EDITING_DISABLED")
        d = c.delete(P)
        assert d.status_code == 403
        _envelope(d, "PROMPT_EDITING_DISABLED")
        assert c.post(f"{P}/render", json={"query": "x"}).status_code == 200
    assert store.get_active() == store.DEFAULT


# ---------------------------------------------------------------- render

def _compact_json(answer):
    """Few-shot answers go into the chat as compact JSON (fewer tokens; the model imitates the format)."""
    return json.dumps(answer, ensure_ascii=False, separators=(",", ":"))


def _expected_messages(system, shots, query):
    msgs = [{"role": "system", "content": system}]
    for q, a in shots:
        msgs.append({"role": "user", "content": q})
        msgs.append({"role": "assistant", "content": _compact_json(a)})
    msgs.append({"role": "user", "content": query})
    return msgs


def test_render_default(make_app):
    app, _ = make_app()
    q = "Тошкентда яшайдиган аёллар"
    with TestClient(app) as c:
        r = c.post(f"{P}/render", json={"query": q})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["version"] == PROMPT_VERSION
    assert b["isCustom"] is False
    assert b["messages"] == _expected_messages(SYSTEM_PROMPT, FEW_SHOTS, q)
    assert isinstance(b["rendered"], str) and q in b["rendered"]
    assert b["tokenCount"] is None
    assert b["exact"] is False


def test_render_custom_non_ascii_not_escaped(make_app):
    app, _ = make_app()
    shots = [{"query": "Тошкентлик аёллар", "answer": {"region": "Тошкент", "sex": "Аёл"}}]
    shots[0]["answer"] = ParsedSearchIntent.model_validate(shots[0]["answer"]).to_compact_dict()
    with TestClient(app) as c:
        assert c.put(P, json=_put_body(shots=shots)).status_code == 200, "setup"
        b = c.post(f"{P}/render", json={"query": "q"}).json()
    assert b["version"] == CUSTOM_VERSION and b["isCustom"] is True
    assert b["messages"] == _expected_messages(CUSTOM_SYS, [(s["query"], s["answer"]) for s in shots], "q")
    assert "\\u" not in b["messages"][2]["content"]
    assert json.loads(b["messages"][2]["content"]) == shots[0]["answer"]
    assert b["messages"][2]["content"] == _compact_json(shots[0]["answer"])


def test_render_few_shot_answers_have_no_separator_whitespace(make_app):
    app, _ = make_app()
    shots = [{"query": "a", "answer": {"fromAge": 30, "toAge": 40, "sex": "Ayol"}}]
    with TestClient(app) as c:
        assert c.put(P, json=_put_body(shots=shots)).status_code == 200, "setup"
        b = c.post(f"{P}/render", json={"query": "q"}).json()
    content = b["messages"][2]["content"]
    assert b["messages"][2]["role"] == "assistant"
    assert " " not in content, content
    assert json.loads(content) == shots[0]["answer"]


def test_build_messages_default_few_shots_compact():
    answers = [m["content"] for m in build_messages("q") if m["role"] == "assistant"]
    assert len(answers) == len(FEW_SHOTS) > 0
    assert answers == [_compact_json(a) for _, a in FEW_SHOTS]


def test_build_retry_messages_few_shots_compact():
    # the failed raw output is echoed verbatim; only the few-shots are serialised by the prompt code
    store.set_custom(CUSTOM_SYS, [("shot q", {"fromAge": 30, "sex": "Ayol"})])
    msgs = build_retry_messages("q", "not json")
    assert {"role": "assistant", "content": '{"fromAge":30,"sex":"Ayol"}'} in msgs


def test_render_default_query_when_omitted(make_app):
    app, _ = make_app()
    with TestClient(app) as c:
        r = c.post(f"{P}/render", json={})
    assert r.status_code == 200, r.text
    last = r.json()["messages"][-1]
    assert last["role"] == "user" and last["content"].strip()


# ---------------------------------------------------------------- test page

def test_ui_page_has_prompt_controls(make_app):
    app, _ = make_app()
    with TestClient(app) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert re.search(r"""id=["']promptBtn["']""", html)
    assert re.search(r"""id=["']promptPanel["']""", html)
    assert not re.search(r"""<script[^>]+src=["']https?://""", html, re.I)
    assert not re.search(r"""<link[^>]+href=["']https?://""", html, re.I)
    assert not re.search(r"""@import\s+(url\()?["']?https?://""", html, re.I)


def test_ui_result_card_comes_before_prompt_panel(make_app):
    """A result rendered below an open prompt panel looked like "no answer"."""
    app, _ = make_app()
    with TestClient(app) as c:
        html = c.get("/").text
    out = list(re.finditer(r"""<[a-zA-Z][^<>]*\sid=["']out["']""", html))
    panel = list(re.finditer(r"""<[a-zA-Z][^<>]*\sid=["']promptPanel["']""", html))
    assert len(out) == 1, [m.group(0) for m in out]
    assert len(panel) == 1, [m.group(0) for m in panel]
    assert out[0].start() < panel[0].start()


# ---------------------------------------------------------------- privacy

def _dump(rec):
    return rec.getMessage() + " " + " ".join(str(v) for v in vars(rec).values())


def test_render_and_put_do_not_log_text(make_app, caplog):
    app, _ = make_app(LOG_LEVEL="INFO")
    marker_q = "Zyxwvuq Qqqmarkerov render query"
    shot_q = "Plmokn shot query marker"
    with TestClient(app) as c:
        caplog.clear()
        caplog.set_level(logging.INFO)
        r = c.post(f"{P}/render", json={"query": marker_q})
        assert r.status_code == 200
        render_records = [rec for rec in caplog.records if rec.levelno >= logging.INFO]
        for rec in render_records:
            assert "Zyxwvuq" not in _dump(rec), rec.getMessage()

        caplog.clear()
        r = c.put(P, json=_put_body(shots=[{"query": shot_q, "answer": {}}]))
        assert r.status_code == 200
        records = [rec for rec in caplog.records if rec.levelno >= logging.INFO]
    overridden = [rec for rec in records if rec.getMessage() == "prompt_overridden"]
    assert overridden, [rec.getMessage() for rec in records]
    assert overridden[0].levelno == logging.WARNING
    for rec in records:
        d = _dump(rec)
        assert "CUSTOM-SYSTEM-PROMPT-MARKER-QWERTY" not in d, rec.getMessage()
        assert "Plmokn" not in d, rec.getMessage()
