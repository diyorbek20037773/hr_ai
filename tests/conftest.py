"""Shared fixtures. No fixture here loads the real model."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.references import ReferenceItem

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REFERENCES_PATH = PROJECT_ROOT / "references" / "example_references.json"
GOLDEN_PATH = PROJECT_ROOT / "tests" / "eval" / "golden_queries.jsonl"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings isolated from any developer `.env` (explicit values for everything that matters)."""
    base = {
        "LOG_DIR": str(tmp_path / "logs"),
        "LOG_LEVEL": "INFO",
        "HR_SEARCH_API_URL": "",
        "HR_SEARCH_API_KEY": "",
        "AI_SEARCH_API_KEY": "",
        "REFERENCE_API_BASE_URL": "",
        "LOCAL_REFERENCES_PATH": str(EXAMPLE_REFERENCES_PATH),
        "FUZZY_AUTO_MATCH_THRESHOLD": 88,
        "FUZZY_CANDIDATE_THRESHOLD": 75,
        "FUZZY_AMBIGUITY_MARGIN": 3,
        "DEFAULT_SEARCH_ROWS": 20,
        "ACADEMIC_TITLE_ID_DEFAULT": 0,
        "MAX_QUERY_LENGTH": 1000,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def settings_factory(tmp_path) -> Callable[..., Settings]:
    def _factory(**overrides) -> Settings:
        return make_settings(tmp_path, **overrides)

    return _factory


@pytest.fixture(scope="session")
def raw_references() -> dict:
    return json.loads(EXAMPLE_REFERENCES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def references(raw_references) -> dict[str, list[ReferenceItem]]:
    return {k: [ReferenceItem.model_validate(i) for i in v] for k, v in raw_references.items() if isinstance(v, list)}


@pytest.fixture(scope="session")
def ref_id(raw_references) -> Callable[[str, str], int]:
    """Look up an ID in the example references by canonical name (so tests never hardcode placeholder IDs)."""

    def _lookup(collection: str, name: str) -> int:
        for item in raw_references[collection]:
            if item["name"] == name:
                return item["id"]
        raise KeyError(f"{name!r} not in {collection}")

    return _lookup


@pytest.fixture
def make_app(tmp_path):
    """Build the FastAPI app with a FakeLLMService; returns (app, fake_llm)."""
    from app.main import create_app
    from app.services.llm_service import FakeLLMService

    def _factory(responses: dict | None = None, default: str = "{}", **overrides):
        fake = FakeLLMService(responses or {}, default=default)
        app = create_app(settings=make_settings(tmp_path, **overrides), llm=fake)
        return app, fake

    return _factory
