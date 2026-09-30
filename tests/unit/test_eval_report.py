"""Spec tests (review item 6): eval_model.render_report with synthetic rows (no model)."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.scripts.eval_model import render_report

LLM = SimpleNamespace(model_path="fake://model", device="fake", dtype="none")


def _row(set_name: str, query: str, ok: bool, tags=("x",), expected=None, got=None, ms=100) -> dict:
    expected = {"fromAge": 30} if expected is None else expected
    if got is None:
        got = dict(expected) if ok else {"toAge": 30}
    return {"set": set_name, "query": query, "expectedIntent": expected, "coreFields": list(expected),
            "lang": "ru", "tags": list(tags), "got": got, "latencyMs": ms, "core": ok, "full": ok,
            "mismatched": [] if ok else list(expected), "invented": [k for k in got if k not in expected]}


def _targets(report: str) -> str:
    m = re.search(r"\*\*Targets met\*\*\s*\|\s*\*\*(YES|NO)\*\*", report)
    assert m, "Targets met line missing"
    return m.group(1)


def test_no_rows_raises_systemexit():
    with pytest.raises(SystemExit):
        render_report([], [], 0, LLM)


def test_hard_only_raises_systemexit():
    rows = [_row("hard", "h1", True), _row("hard", "h2", True)]
    with pytest.raises(SystemExit):
        render_report(rows, [1, 2], 2, LLM)


def test_main_only_no_challenge_section():
    rows = [_row("main", f"m{i}", True, tags=("core",)) for i in range(3)]
    rep = render_report(rows, [10, 20, 30], 3, LLM)
    assert "Challenge set" not in rep
    assert _targets(rep) == "YES"


def test_main_plus_hard_renders_challenge_section():
    rows = [_row("main", "query-main-1", True, tags=("core",)), _row("hard", "query-hard-1", False)]
    rep = render_report(rows, [10, 20], 2, LLM)
    assert "Challenge set" in rep
    assert "query-hard-1" in rep and "query-main-1" in rep


def test_targets_ignore_failing_hard_rows():
    rows = [_row("main", f"m{i}", True, tags=("core",)) for i in range(5)]
    rows += [_row("hard", f"h{i}", False) for i in range(20)]
    rep = render_report(rows, [10] * len(rows), len(rows), LLM)
    assert _targets(rep) == "YES"


def test_targets_not_rescued_by_passing_hard_rows():
    rows = [_row("main", "m0", True, tags=("core",)), _row("main", "m1", False, tags=("core",))]
    rows += [_row("hard", f"h{i}", True) for i in range(20)]
    rep = render_report(rows, [10] * len(rows), len(rows), LLM)
    assert _targets(rep) == "NO"


def test_targets_full_match_threshold_on_main_only():
    # main: 8/10 full (80 % < 85 %); 40 passing hard rows must not lift it
    rows = [_row("main", f"m{i}", i < 8) for i in range(10)]
    rows += [_row("hard", f"h{i}", True) for i in range(40)]
    rep = render_report(rows, [10] * len(rows), len(rows), LLM)
    assert _targets(rep) == "NO"


def test_queries_count_is_main_only():
    rows = [_row("main", f"m{i}", True) for i in range(3)] + [_row("hard", f"h{i}", True) for i in range(7)]
    rep = render_report(rows, [10] * 10, 10, LLM)
    assert re.search(r"\|\s*Queries\s*\|\s*3\s*\|", rep)


def test_latency_label_all_sets():
    rows = [_row("main", "m1", True), _row("hard", "h1", True)]
    rep = render_report(rows, [100, 300], 2, LLM)
    latency = [ln for ln in rep.splitlines() if ln.startswith("| Latency")]
    assert latency and "(all sets)" in latency[0]


def test_negative_invention_on_hard_does_not_affect_targets():
    rows = [_row("main", "m-neg", True, expected={}, got={})]
    rows += [_row("hard", "h-neg", False, expected={}, got={"sex": "Ayol"})]
    rep = render_report(rows, [1, 2], 2, LLM)
    assert _targets(rep) == "YES"


def test_negative_invention_on_main_fails_targets():
    rows = [_row("main", "m-neg", False, expected={}, got={"sex": "Ayol"})]
    rows += [_row("main", f"m{i}", True) for i in range(1, 20)]
    rep = render_report(rows, [1] * len(rows), len(rows), LLM)
    assert _targets(rep) == "NO"
