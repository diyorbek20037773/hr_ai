"""Real-inference tests. Load the local Qwen model once per session. Run with `pytest -m model`."""

from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.models.enums import parse_type_of_position, parse_type_of_service
from app.models.references import ReferenceItem
from app.services.fuzzy_matcher import FuzzyMatcher
from app.services.intent_parser import IntentParser
from tests.conftest import EXAMPLE_REFERENCES_PATH, GOLDEN_PATH

pytestmark = pytest.mark.model

COLLECTION_FOR = {
    "region": "regions", "birthRegion": "regions", "district": "districts", "sex": "sexes", "nation": "nations",
    "language": "languages", "education": "educations", "specialists": "specialists", "eduType": "eduTypes",
    "degree": "degrees", "academicDegree": "academicDegrees", "academicTitle": "academicTitles",
    "institution": "institutions", "institutionTypes": "institutionTypes", "institutionGroups": "institutionGroups",
    "positionCategories": "positionCategories", "languageCertificate": "languageCertificates",
}

CORE_ROWS = [r for r in (json.loads(x) for x in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if x.strip())
             if "core" in r["tags"]]


@pytest.fixture(scope="session")
def llm():
    from app.services.llm_service import QwenLLMService

    service = QwenLLMService(get_settings())
    service.load()
    yield service
    if hasattr(service, "unload"):
        service.unload()


@pytest.fixture(scope="session")
def parser(llm):
    return IntentParser(llm)


@pytest.fixture(scope="session")
def refs():
    raw = json.loads(EXAMPLE_REFERENCES_PATH.read_text(encoding="utf-8"))
    return {k: [ReferenceItem.model_validate(i) for i in v] for k, v in raw.items()}


@pytest.fixture(scope="session")
def core_outcomes(parser):
    return {r["query"]: parser.parse(r["query"]) for r in CORE_ROWS}


def _as_list(v):
    return v if isinstance(v, list) else [v]


def _same_text(field, expected, got, refs, matcher) -> bool:
    if str(expected).casefold() == str(got).casefold():
        return True
    collection = COLLECTION_FOR.get(field)
    if not collection:
        return False
    e, g = matcher.match(str(expected), refs[collection]), matcher.match(str(got), refs[collection])
    return e.status == "resolved" and g.status == "resolved" and e.item.id == g.item.id


def _field_ok(field, expected, got, refs, matcher) -> bool:
    if got is None:
        return False
    if field == "typeOfPosition":
        exp = [parse_type_of_position(v)[0] for v in _as_list(expected)]
        act = [parse_type_of_position(v)[0] for v in _as_list(got)]
        return None not in act and set(exp) == set(act)
    if field == "typeOfService":
        e, g = parse_type_of_service(expected)[0], parse_type_of_service(got)[0]
        return e is not None and e == g
    if isinstance(expected, (bool, int)):
        return got == expected and type(got) is type(expected)
    if isinstance(expected, list):
        ge = _as_list(got)
        return len(ge) == len(expected) and all(
            any(_same_text(field, e, g, refs, matcher) for g in ge) for e in expected)
    return _same_text(field, expected, got, refs, matcher)


def test_model_loaded_once(llm):
    assert llm.is_loaded


@pytest.mark.parametrize("row", CORE_ROWS, ids=[r["query"][:40] for r in CORE_ROWS])
def test_core_queries(row, core_outcomes, refs):
    got = core_outcomes[row["query"]].intent.to_compact_dict()
    matcher = FuzzyMatcher(get_settings())
    if not row["expectedIntent"]:
        assert got == {}, f"invented fields: {got}"
        return
    failures = [f for f in row["coreFields"] if not _field_ok(f, row["expectedIntent"][f], got.get(f), refs, matcher)]
    assert not failures, f"expected {row['expectedIntent']}, got {got}; failing fields {failures}"


def test_empty_intent_query(parser):
    assert parser.parse("yaxshi kadrlarni top").intent.to_compact_dict() == {}


def test_valid_json_first_attempt(core_outcomes):
    first = sum(1 for o in core_outcomes.values() if o.attempts == 1)
    assert first >= 9, f"only {first}/10 valid on first attempt"
