"""Spec test (review item 8): every golden query renders to a chat prompt < 1800 tokens.

Needs the local tokenizer only (CPU, no model weights loaded, nothing downloaded).
"""

from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.prompts.search_parser import build_messages
from tests.conftest import GOLDEN_PATH

pytestmark = pytest.mark.model

BUDGET = 1800
HARD_PATH = GOLDEN_PATH.parent / "golden_hard.jsonl"


def _queries() -> list[str]:
    out: list[str] = []
    for path in (GOLDEN_PATH, HARD_PATH):
        out += [json.loads(x)["query"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


@pytest.fixture(scope="module")
def tokenizer():
    from transformers import AutoTokenizer

    from app.services.llm_service import detect_model_dir

    return AutoTokenizer.from_pretrained(detect_model_dir(get_settings()), local_files_only=True)


def test_prompt_budget_all_golden_queries(tokenizer):
    over = []
    worst = 0
    for q in _queries():
        text = tokenizer.apply_chat_template(build_messages(q), tokenize=False, add_generation_prompt=True)
        n = len(tokenizer(text)["input_ids"])
        worst = max(worst, n)
        if n >= BUDGET:
            over.append((q, n))
    print(f"max prompt tokens = {worst} over {len(_queries())} queries")
    assert not over, f"prompts at/over {BUDGET} tokens: {over}"
