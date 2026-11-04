"""The prompt-v11 guards seen through the public API (FakeLLMService; written from the spec)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

P = "/api/v1/ai-search"


@pytest.mark.parametrize(
    "query,llm,intent",
    [
        (
            "Олий маълумотли, 45 ёшгача бўлган эркаклар",
            '{"education":"Oliy","fromAge":45,"toAge":45,"sex":"Erkak"}',
            {"education": "Oliy", "toAge": 45, "sex": "Erkak"},
        ),
        (
            "Mukofotlangan, ilmiy unvonga ega xodimlar",
            '{"academicTitle":"Professor"}',
            {"isAcademicTitle": True},
        ),
        (
            "Ilmiy unvoni bor professorlar",
            '{"academicTitle":"Professor"}',
            {"academicTitle": "Professor"},
        ),
        (
            "Фамилияси Тошматов бўлган ходимлар",
            '{"lastName":"Toшматов"}',
            {"lastName": "Тошматов"},
        ),
        (
            "25 yoshli xodimlar",
            '{"fromAge":25,"toAge":25}',
            {"fromAge": 25, "toAge": 25},
        ),
    ],
)
def test_parse_returns_guarded_intent(make_app, query, llm, intent):
    app, _ = make_app({query: llm})
    with TestClient(app) as c:
        r = c.post(f"{P}/parse", json={"query": query})
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == intent


def test_resolve_generic_title_reaches_search_model_as_flag(make_app):
    q = "Mukofotlangan, ilmiy unvonga ega xodimlar"
    app, _ = make_app({q: '{"academicTitle":"Professor"}'})
    with TestClient(app) as c:
        r = c.post(f"{P}/resolve", json={"query": q})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == {"isAcademicTitle": True}
    assert body["searchModel"]["isAcademicTitle"] is True
    assert body["unresolvedFilters"] == []


def test_resolve_fixed_person_name_reaches_search_model(make_app):
    q = "Фамилияси Тошматов бўлган ходимлар"
    app, _ = make_app({q: '{"lastName":"Toшматов"}'})
    with TestClient(app) as c:
        r = c.post(f"{P}/resolve", json={"query": q})
    assert r.status_code == 200, r.text
    assert r.json()["searchModel"]["lastName"] == "Тошматов"
