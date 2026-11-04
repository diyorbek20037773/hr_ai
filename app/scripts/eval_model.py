"""Golden evaluation of the real local model -> docs/model-eval.md.

    python -m app.scripts.eval_model [--set all|main|hard] [--limit N] [--out docs/model-eval.md]

Sets: `main` = tests/eval/golden_queries.jsonl (CLAUDE.md §7 targets are measured on it);
`hard` = tests/eval/golden_hard.jsonl (challenge set, reported separately, no pass/fail target).

Comparison per field: numbers / booleans / enums exact; strings case-insensitive after normalisation;
dictionary-backed strings (region, specialists, ...) also count as equal when both expected and actual
resolve to the SAME reference ID in references/example_references.json (what matters downstream is the ID).
Any key the model outputs that is not in expectedIntent is an invented field => full FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, get_settings
from app.core.logging import setup_logging
from app.models.enums import parse_type_of_position, parse_type_of_service
from app.models.references import COLLECTION_KEYS, DICTIONARY_FIELDS, ReferenceData
from app.prompts.search_parser import PROMPT_VERSION
from app.services.fuzzy_matcher import FuzzyMatcher
from app.services.intent_parser import IntentParseError, IntentParser
from app.services.llm_service import QwenLLMService
from app.services.reference_provider import LocalJsonReferenceProvider
from app.utils.text_normalizer import normalize_text

GOLDEN = PROJECT_ROOT / "tests" / "eval" / "golden_queries.jsonl"
GOLDEN_SETS = {"main": GOLDEN, "hard": PROJECT_ROOT / "tests" / "eval" / "golden_hard.jsonl"}
_DICT_COLLECTION = {f.intent_key: f.collection for f in DICTIONARY_FIELDS}


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Comparator:
    def __init__(self, refs: ReferenceData):
        self.refs = refs
        self.matcher = FuzzyMatcher(get_settings())

    def _ref_id(self, key: str, value: str) -> int | None:
        items = self.refs.get(_DICT_COLLECTION[key], [])
        m = self.matcher.match(value, items)
        return m.item.id if m.status == "resolved" and m.item else None

    def scalar_equal(self, key: str, exp: Any, got: Any) -> bool:
        if key == "typeOfPosition":
            a = parse_type_of_position(exp)[0]
            return a is not None and a == parse_type_of_position(got)[0]
        if key == "typeOfService":
            a = parse_type_of_service(exp)[0]
            return a is not None and a == parse_type_of_service(got)[0]
        if isinstance(exp, bool) or isinstance(got, bool):
            return exp is got
        if isinstance(exp, int | float):
            return isinstance(got, int | float) and exp == got
        if normalize_text(str(exp)) == normalize_text(str(got)):
            return True
        if key in _DICT_COLLECTION:
            a, b = self._ref_id(key, str(exp)), self._ref_id(key, str(got))
            return a is not None and a == b
        return False

    def equal(self, key: str, exp: Any, got: Any) -> bool:
        if isinstance(exp, list) or isinstance(got, list):
            exp_l = exp if isinstance(exp, list) else [exp]
            got_l = got if isinstance(got, list) else [got]
            if len(exp_l) != len(got_l):
                return False
            remaining = list(got_l)
            for e in exp_l:
                hit = next((g for g in remaining if self.scalar_equal(key, e, g)), None)
                if hit is None:
                    return False
                remaining.remove(hit)
            return True
        return self.scalar_equal(key, exp, got)


def evaluate(entry: dict, got: dict, cmp: Comparator) -> dict:
    expected = entry["expectedIntent"]
    core = entry.get("coreFields") or list(expected)
    mismatched = [k for k in expected if k not in got or not cmp.equal(k, expected[k], got[k])]
    invented = [k for k in got if k not in expected]
    core_ok = all(k in got and cmp.equal(k, expected[k], got[k]) for k in core)
    if not expected:  # negative query: any output field is an invention
        core_ok = not got
    return {"core": core_ok, "full": not mismatched and not invented, "mismatched": mismatched,
            "invented": invented}


async def load_all_references(local: LocalJsonReferenceProvider) -> ReferenceData:
    out: ReferenceData = {}
    for c in COLLECTION_KEYS:
        items = await local.get(c)
        if items:
            out[c] = items
    return out


def _fmt(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False).replace("|", "\\|")


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--set", choices=["all", *GOLDEN_SETS], default="all")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "docs" / "model-eval.md"))
    args = ap.parse_args(argv)

    settings = get_settings()
    setup_logging("WARNING", settings.resolve_path(settings.LOG_DIR))
    entries = []
    for name, path in GOLDEN_SETS.items():
        if args.set in ("all", name) and path.exists():
            entries += [{**e, "set": name} for e in load_golden(path)]
    if args.limit:
        entries = entries[: args.limit]

    local = LocalJsonReferenceProvider(settings.resolve_path(settings.LOCAL_REFERENCES_PATH))
    cmp = Comparator(asyncio.run(load_all_references(local)))

    llm = QwenLLMService(settings)
    llm.load()
    parser = IntentParser(llm)

    rows, latencies, first_try = [], [], 0
    for i, entry in enumerate(entries, 1):
        t0 = time.perf_counter()
        try:
            outcome = parser.parse(entry["query"])
            got = outcome.intent.to_compact_dict()
            first_try += outcome.attempts == 1
            res = evaluate(entry, got, cmp)
        except IntentParseError:
            got = {}
            res = {"core": False, "full": False, "mismatched": [], "invented": [], "error": "LLM_OUTPUT_INVALID"}
        ms = int((time.perf_counter() - t0) * 1000)
        latencies.append(ms)
        rows.append({**entry, "got": got, "latencyMs": ms, **res})
        print(f"[{i}/{len(entries)}] core={'PASS' if res['core'] else 'FAIL'} "
              f"full={'PASS' if res['full'] else 'FAIL'} {ms}ms {entry['query'][:60]} -> {_fmt(got)}", flush=True)

    # newline="\n": keep LF on Windows (the repo is LF, see .gitattributes)
    Path(args.out).write_text(render_report(rows, latencies, first_try, llm), encoding="utf-8", newline="\n")
    log_dir = settings.resolve_path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "model-eval-last.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                                  encoding="utf-8", newline="\n")
    core_rows = [r for r in rows if "core" in r.get("tags", [])]
    summary = [f"core scenarios {sum(r['core'] for r in core_rows)}/{len(core_rows)}"]
    for name in GOLDEN_SETS:
        part = [r for r in rows if r["set"] == name]
        if part:
            summary.append(f"{name} full {sum(r['full'] for r in part)}/{len(part)}")
    print("; ".join(summary) + f" -> {args.out}")
    return 0


def _results_section(title: str, rows: list[dict]) -> list[str]:
    lines = [f"## {title}", "", "| # | Lang | Query | Expected | Got | Core | Full | Notes | ms |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        notes = []
        if r.get("mismatched"):
            notes.append("mismatch: " + ", ".join(r["mismatched"]))
        if r.get("invented"):
            notes.append("invented: " + ", ".join(r["invented"]))
        if r.get("error"):
            notes.append(r["error"])
        lines.append(
            f"| {i} | {r.get('lang', '')} | {r['query'].replace('|', '/')} | `{_fmt(r['expectedIntent'])}` | "
            f"`{_fmt(r['got'])}` | {'PASS' if r['core'] else 'FAIL'} | {'PASS' if r['full'] else 'FAIL'} | "
            f"{'; '.join(notes)} | {r['latencyMs']} |"
        )
    failing = [r for r in rows if not r["full"]]
    lines += ["", "Failing cases:", ""]
    if not failing:
        lines.append("None.")
    for r in failing:
        lines.append(f"- `{r['query']}` - expected `{_fmt(r['expectedIntent'])}`, got `{_fmt(r['got'])}`")
    lines.append("")
    return lines


def render_report(all_rows: list[dict], latencies: list[int], first_try: int, llm: QwenLLMService) -> str:
    rows = [r for r in all_rows if r["set"] == "main"]
    if not rows:
        raise SystemExit("render_report needs the main set: §7 targets are measured on golden_queries.jsonl only; "
                         "use --out <scratch file> together with --set main or --set all")
    hard = [r for r in all_rows if r["set"] == "hard"]
    n = len(rows)
    core_rows = [r for r in rows if "core" in r.get("tags", [])]
    negatives = [r for r in rows if not r["expectedIntent"]]
    invented_total = sum(len(r["invented"]) for r in rows)
    invented_neg = sum(len(r["got"]) for r in negatives)
    full_pct = 100.0 * sum(r["full"] for r in rows) / n if n else 0.0
    core_all_pct = 100.0 * sum(r["core"] for r in rows) / n if n else 0.0
    core_pass = sum(r["core"] for r in core_rows)
    targets_met = core_pass == len(core_rows) and full_pct >= 85.0 and invented_neg == 0
    lines = [
        "# Model evaluation (golden set)",
        "",
        "_Generated by `python -m app.scripts.eval_model` - do not edit by hand._",
        "",
        "## Summary",
        "",
        "| Metric | Value | Target |",
        "|---|---|---|",
        f"| Date | {datetime.now().isoformat(timespec='seconds')} | |",
        f"| Prompt version | {PROMPT_VERSION} | |",
        f"| Model | `{llm.model_path}` | |",
        f"| Device / dtype | {llm.device} / {llm.dtype} | |",
        f"| Queries | {n} | >= 20 |",
        f"| Core scenarios (tag `core`) | **{core_pass}/{len(core_rows)}** | 10/10 |",
        f"| Core-field accuracy (all queries) | {core_all_pct:.1f} % | |",
        f"| Full-match accuracy | **{full_pct:.1f} %** | >= 85 % |",
        f"| Invented fields (all queries) | {invented_total} | |",
        f"| Invented fields on negative queries | {invented_neg} | 0 |",
        f"| Valid JSON on first attempt (all sets) | {first_try}/{len(all_rows)} | |",
        f"| Latency p50 / p95 / max (all sets) | {_pct(latencies, .5)} / {_pct(latencies, .95)} / "
        f"{max(latencies) if latencies else 0} ms | |",
        f"| **Targets met** | **{'YES' if targets_met else 'NO'}** | |",
        "",
        "Comparison rules: numbers/booleans/enums exact; strings case-insensitive after normalisation; "
        "dictionary-backed strings also equal when both resolve to the same reference ID in "
        "`references/example_references.json`; any extra key = invented field = full FAIL.",
        "",
    ]
    lines += _results_section("Results — main set (`golden_queries.jsonl`)", rows)
    if hard:
        h_full = 100.0 * sum(r["full"] for r in hard) / len(hard)
        h_core = 100.0 * sum(r["core"] for r in hard) / len(hard)
        h_inv = sum(len(r["invented"]) for r in hard)
        lines += [
            "## Challenge set (`golden_hard.jsonl`)",
            "",
            "Harder queries (many filters, typos, negations, mixed scripts). Used as the development set for "
            "prompt v4-v11 and the intent guards, so this score is optimistic, not held-out. The §7 "
            "targets are measured on the main set only.",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Queries | {len(hard)} |",
            f"| Core-field accuracy | {h_core:.1f} % |",
            f"| Full-match accuracy | **{h_full:.1f} %** |",
            f"| Invented fields | {h_inv} |",
            "",
        ]
        lines += _results_section("Results — challenge set", hard)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
