"""Spec tests (review item 7): prompt hygiene - no IDs in the prompt, valid few-shots, no golden-set leakage."""

from __future__ import annotations

import json
import re

import pytest
from rapidfuzz import fuzz

from app.models.ai_intent import ParsedSearchIntent
from app.models.search import ISEARCHMODEL_FIELDS
from app.prompts.search_parser import FEW_SHOTS, SYSTEM_PROMPT, build_messages
from app.utils.text_normalizer import normalize_text
from tests.conftest import GOLDEN_PATH

HARD_PATH = GOLDEN_PATH.parent / "golden_hard.jsonl"
ID_FIELDS = sorted(f for f in ISEARCHMODEL_FIELDS if f.endswith(("Id", "Ids")))
ID_KEY_RE = re.compile(r"\b[A-Za-z]\w*Ids?\b")
LEAK_THRESHOLD = 80


def _golden() -> list[tuple[str, str]]:
    out = []
    for name, path in (("main", GOLDEN_PATH), ("hard", HARD_PATH)):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append((name, json.loads(line)["query"]))
    return out


def _keys(obj) -> list[str]:
    if isinstance(obj, dict):
        return list(obj) + [k for v in obj.values() for k in _keys(v)]
    if isinstance(obj, list):
        return [k for v in obj for k in _keys(v)]
    return []


def test_id_field_list_is_non_trivial():
    assert {"regionId", "sexId", "specialistIds", "educationId"} <= set(ID_FIELDS)


def test_system_prompt_has_no_id_field_names():
    found = sorted({m.group(0) for m in ID_KEY_RE.finditer(SYSTEM_PROMPT)})
    assert found == [], f"SYSTEM_PROMPT mentions ID-like identifiers: {found}"
    for f in ID_FIELDS:
        assert f not in SYSTEM_PROMPT, f


@pytest.mark.parametrize("idx", range(len(FEW_SHOTS)))
def test_few_shot_has_no_id_keys_or_names(idx):
    query, answer = FEW_SHOTS[idx]
    for k in _keys(answer):
        assert not k.endswith(("Id", "Ids")), k
        assert k not in ID_FIELDS, k
    rendered = query + " " + json.dumps(answer, ensure_ascii=False)
    for f in ID_FIELDS:
        assert f not in rendered, f


def test_rendered_messages_have_no_id_field_names():
    text = "\n".join(m["content"] for m in build_messages("test"))
    for f in ID_FIELDS:
        assert f not in text, f


@pytest.mark.parametrize("idx", range(len(FEW_SHOTS)))
def test_few_shot_answer_is_valid_intent_without_dropped_keys(idx):
    _, answer = FEW_SHOTS[idx]
    intent = ParsedSearchIntent.model_validate(answer)
    compact = intent.to_compact_dict()
    assert set(compact) == set(answer), f"dropped/added keys: {set(answer) ^ set(compact)}"
    assert compact == answer


def test_few_shots_include_a_negative_example():
    assert any(answer == {} for _, answer in FEW_SHOTS)


@pytest.mark.parametrize("name,golden_query", _golden())
def test_no_few_shot_near_duplicates_golden(name, golden_query):
    g = normalize_text(golden_query)
    leaks = []
    for shot, _ in FEW_SHOTS:
        score = fuzz.token_set_ratio(normalize_text(shot), g)
        if score >= LEAK_THRESHOLD:
            leaks.append((shot, round(score, 1)))
    assert not leaks, f"{name} golden query {golden_query!r} near-duplicates few-shot(s): {leaks}"
