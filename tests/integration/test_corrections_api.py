"""Spec tests (review item 3): `corrections` in /parse, /resolve and /search responses (FakeLLMService)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

P = "/api/v1/ai-search"
Q_FIX = "30 ёшдан катта аёллар"
Q_CLEAN = "Oliy ma'lumotli, 30 yoshdan katta ayollar"
Q_DROP = "Toshkentda ishlaydigan iqtisodchilar"
RESPONSES = {
    Q_FIX: '{"toAge":30}',
    Q_CLEAN: '{"education":"Oliy","fromAge":30,"sex":"Ayol"}',
    Q_DROP: '{"region":"Toshkent","specialists":["Iqtisodiyot"],"education":"Oliy"}',
}
ENDPOINTS = ["parse", "resolve", "search"]


@pytest.fixture
def client(make_app):
    app, _ = make_app(RESPONSES)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_corrections_age_fix(client, endpoint):
    r = client.post(f"{P}/{endpoint}", json={"query": Q_FIX})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["corrections"], list)
    assert "toAge->fromAge" in body["corrections"]
    assert all(isinstance(c, str) for c in body["corrections"])


def test_parse_corrections_intent_is_fixed(client):
    body = client.post(f"{P}/parse", json={"query": Q_FIX}).json()
    assert body["intent"].get("fromAge") == 30
    assert "toAge" not in body["intent"]


@pytest.mark.parametrize("endpoint", ["resolve", "search"])
def test_corrected_age_reaches_search_model(client, endpoint):
    sm = client.post(f"{P}/{endpoint}", json={"query": Q_FIX}).json()["searchModel"]
    assert sm.get("fromAge") == 30
    assert "toAge" not in sm


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_corrections_empty_list_when_none(client, endpoint):
    r = client.post(f"{P}/{endpoint}", json={"query": Q_CLEAN})
    assert r.status_code == 200, r.text
    assert r.json()["corrections"] == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_corrections_empty_list_for_empty_intent(client, endpoint):
    r = client.post(f"{P}/{endpoint}", json={"query": "yaxshi kadrlarni top"})
    assert r.status_code == 200, r.text
    assert r.json()["corrections"] == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_corrections_drop_code(client, endpoint):
    body = client.post(f"{P}/{endpoint}", json={"query": Q_DROP}).json()
    assert "drop:education" in body["corrections"]
