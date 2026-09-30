"""Process-wide active prompt: the committed default from `search_parser.py`, or a runtime override.

A runtime override (edited from the test page / `PUT /api/v1/prompt`) lives in memory only, is labelled
"<default>+custom" everywhere a prompt version is reported, and disappears on restart. To make a prompt permanent,
put it into `app/prompts/search_parser.py`, bump PROMPT_VERSION and re-run the eval (CLAUDE.md §7).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.prompts import search_parser as default


@dataclass(frozen=True)
class PromptConfig:
    system_prompt: str
    few_shots: tuple[tuple[str, dict], ...]
    version: str
    is_custom: bool = False
    retry_message: str = field(default=default.RETRY_MESSAGE)


DEFAULT = PromptConfig(
    system_prompt=default.SYSTEM_PROMPT,
    few_shots=tuple((q, dict(a)) for q, a in default.FEW_SHOTS),
    version=default.PROMPT_VERSION,
)

_lock = threading.Lock()
_active: PromptConfig = DEFAULT


def get_active() -> PromptConfig:
    return _active


def set_custom(system_prompt: str, few_shots: list[tuple[str, dict]]) -> PromptConfig:
    global _active
    cfg = PromptConfig(system_prompt=system_prompt, few_shots=tuple(few_shots),
                       version=f"{DEFAULT.version}+custom", is_custom=True)
    with _lock:
        _active = cfg
    return cfg


def reset() -> PromptConfig:
    global _active
    with _lock:
        _active = DEFAULT
    return DEFAULT
