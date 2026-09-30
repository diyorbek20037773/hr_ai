"""View / edit the prompt Qwen receives. Edits are a runtime override (memory only) — see app/prompts/store.py."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import ApiError, get_app_settings, require_api_key
from app.models.ai_intent import ParsedSearchIntent
from app.prompts import store
from app.prompts.search_parser import build_messages
from app.prompts.store import PromptConfig

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["prompt"], dependencies=[Depends(require_api_key)])

_SAMPLE_QUERY = "Toshkentda yashaydigan, 30 yoshdan katta, oliy ma'lumotli ayollar"
_INTENT_KEYS = frozenset(ParsedSearchIntent.model_fields)


class FewShot(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    answer: dict[str, Any]


class PromptUpdate(BaseModel):
    systemPrompt: str = Field(..., min_length=20, max_length=20000)
    fewShots: list[FewShot] = Field(default_factory=list, max_length=40)


class RenderRequest(BaseModel):
    query: str = Field(default=_SAMPLE_QUERY, min_length=1, max_length=1000)


def _render(request: Request, messages: list[dict[str, str]]) -> tuple[str, int | None]:
    """Exact text after Qwen's chat template + token count, when the real tokenizer is loaded."""
    llm = getattr(request.app.state, "llm", None)
    render = getattr(llm, "render_prompt", None)
    if llm is not None and llm.is_loaded and callable(render):
        text = render(messages)
        return text, llm.count_tokens(messages)
    text = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
    return text, None


def _state(request: Request, cfg: PromptConfig, settings: Settings, warnings: list[str] | None = None) -> dict:
    _, tokens = _render(request, build_messages(_SAMPLE_QUERY, cfg))
    return {
        "version": cfg.version,
        "defaultVersion": store.DEFAULT.version,
        "isCustom": cfg.is_custom,
        "editingEnabled": settings.PROMPT_EDITING_ENABLED,
        "systemPrompt": cfg.system_prompt,
        "fewShots": [{"query": q, "answer": a} for q, a in cfg.few_shots],
        "retryMessage": cfg.retry_message,
        "tokenCount": tokens,
        "tokenCountQuery": _SAMPLE_QUERY,
        "tokenLimit": settings.PROMPT_MAX_TOKENS,
        "intentKeys": sorted(_INTENT_KEYS),
        "warnings": warnings or [],
    }


def _validate_shots(shots: list[FewShot]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for i, shot in enumerate(shots, 1):
        unknown = sorted(set(shot.answer) - _INTENT_KEYS)
        if unknown:
            raise ApiError(422, "PROMPT_INVALID", f"few-shot #{i}: unknown keys {unknown} (allowed: intent keys only)")
        ids = [k for k in shot.answer if k.endswith(("Id", "Ids"))]
        if ids:
            raise ApiError(422, "PROMPT_INVALID", f"few-shot #{i}: IDs are not allowed in prompts ({ids})")
        try:
            parsed = ParsedSearchIntent.model_validate(shot.answer).to_compact_dict()
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ApiError(422, "PROMPT_INVALID",
                           f"few-shot #{i}: {'.'.join(map(str, first.get('loc', [])))} {first.get('msg')}") from exc
        if parsed != shot.answer:
            raise ApiError(422, "PROMPT_INVALID",
                           f"few-shot #{i}: answer must already be in canonical form, e.g. {parsed}")
        out.append((shot.query.strip(), shot.answer))
    return out


@router.get("/prompt", summary="The prompt Qwen receives (system prompt + few-shots)")
async def get_prompt(request: Request, settings: Settings = Depends(get_app_settings)) -> dict:
    return _state(request, store.get_active(), settings)


@router.put("/prompt", summary="Replace the prompt for this running process (memory only, not evaluated)")
async def put_prompt(body: PromptUpdate, request: Request, settings: Settings = Depends(get_app_settings)) -> dict:
    if not settings.PROMPT_EDITING_ENABLED:
        raise ApiError(403, "PROMPT_EDITING_DISABLED", "Prompt editing is disabled (PROMPT_EDITING_ENABLED=false)")
    shots = _validate_shots(body.fewShots)
    cfg = store.set_custom(body.systemPrompt.strip(), shots)
    log.warning("prompt_overridden", extra={"version": cfg.version, "fewShots": len(shots),
                                            "systemPromptChars": len(cfg.system_prompt)})
    state = _state(request, cfg, settings)
    if state["tokenCount"] and state["tokenCount"] > settings.PROMPT_MAX_TOKENS:
        state["warnings"].append(f"Prompt is {state['tokenCount']} tokens (> {settings.PROMPT_MAX_TOKENS}): "
                                 "slower answers and weaker instruction following on a 4B model.")
    state["warnings"].append("Custom prompt is active in memory only and has NOT been evaluated; it is lost on "
                             "restart. Make it permanent in app/prompts/search_parser.py and run the eval.")
    return state


@router.delete("/prompt", summary="Restore the committed default prompt")
async def reset_prompt(request: Request, settings: Settings = Depends(get_app_settings)) -> dict:
    if not settings.PROMPT_EDITING_ENABLED:
        raise ApiError(403, "PROMPT_EDITING_DISABLED", "Prompt editing is disabled (PROMPT_EDITING_ENABLED=false)")
    cfg = store.reset()
    log.info("prompt_reset", extra={"version": cfg.version})
    return _state(request, cfg, settings)


@router.post("/prompt/render", summary="Exact text Qwen receives for a query (after the chat template)")
async def render_prompt(body: RenderRequest, request: Request) -> dict:
    cfg = store.get_active()
    messages = build_messages(body.query.strip(), cfg)
    text, tokens = _render(request, messages)
    return {"version": cfg.version, "isCustom": cfg.is_custom, "messages": messages, "rendered": text,
            "tokenCount": tokens, "exact": tokens is not None}
