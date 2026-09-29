"""Manual check: load the local model once and parse + resolve one or more queries.

    python -m app.scripts.test_model "Oliy ma'lumotli 30 yoshdan katta ayollar"
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.prompts.search_parser import PROMPT_VERSION, build_messages
from app.services.filter_resolver import FilterResolver
from app.services.intent_parser import IntentParser
from app.services.llm_service import QwenLLMService
from app.services.reference_provider import ReferenceService


def main(argv: list[str]) -> int:
    queries = argv or ["Oliy ma'lumotli 30 yoshdan katta ayollar"]
    settings = get_settings()
    setup_logging("WARNING", settings.resolve_path(settings.LOG_DIR))
    llm = QwenLLMService(settings)
    llm.load()
    print(f"model={llm.model_path} device={llm.device} dtype={llm.dtype} load={llm.load_seconds}s "
          f"prompt=v{PROMPT_VERSION} promptTokens={llm.count_tokens(build_messages(queries[0]))}")
    parser = IntentParser(llm)
    resolver = FilterResolver(settings)
    refs = ReferenceService(settings).for_request(None)
    for q in queries:
        t0 = time.perf_counter()
        outcome = parser.parse(q)
        ms = int((time.perf_counter() - t0) * 1000)
        references = asyncio.run(refs.load(resolver.required_collections(outcome.intent)))
        result = resolver.resolve(outcome.intent, references)
        print(json.dumps({"query": q, "latencyMs": ms, "attempts": outcome.attempts,
                          "intent": outcome.intent.to_compact_dict(), "searchModel": result.searchModel,
                          "unresolved": [u.model_dump() for u in result.unresolvedFilters],
                          "readyToSearch": result.readyToSearch}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
