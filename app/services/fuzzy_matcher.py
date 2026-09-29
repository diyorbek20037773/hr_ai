"""Reference value matching: exact name -> exact alias -> RapidFuzz (CLAUDE.md §3.7).

Below FUZZY_AUTO_MATCH_THRESHOLD a value is never silently accepted; near-ties are reported as ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from app.core.config import Settings, get_settings
from app.models.references import ReferenceItem
from app.models.responses import Candidate
from app.utils.text_normalizer import normalize_text

_SCORERS = {
    "token_sort_ratio": fuzz.token_sort_ratio,
    "ratio": fuzz.ratio,
    "WRatio": fuzz.WRatio,
    "token_set_ratio": fuzz.token_set_ratio,
}

Status = Literal["resolved", "unresolved"]


@dataclass
class MatchResult:
    status: Status
    item: ReferenceItem | None = None
    match_type: Literal["exact", "alias", "fuzzy"] | None = None
    score: float | None = None
    reason: Literal["no_match", "ambiguous", "low_confidence", "no_reference_data"] | None = None
    candidates: list[Candidate] = field(default_factory=list)


class FuzzyMatcher:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._scorer = _SCORERS[self.settings.FUZZY_SCORER]

    def match(self, value: str, items: list[ReferenceItem]) -> MatchResult:
        if not items:
            return MatchResult(status="unresolved", reason="no_reference_data")
        key = normalize_text(value)
        if not key:
            return MatchResult(status="unresolved", reason="no_match")

        # 1. exact name
        by_name = _unique([i for i in items if normalize_text(i.name) == key])
        if len(by_name) == 1:
            return MatchResult(status="resolved", item=by_name[0], match_type="exact", score=100.0)
        # 2. exact alias
        by_alias = _unique(by_name or [i for i in items if key in {normalize_text(a) for a in i.aliases}])
        if len(by_alias) == 1:
            return MatchResult(status="resolved", item=by_alias[0], match_type="alias", score=100.0)
        if len(by_alias) > 1:
            return MatchResult(
                status="unresolved", reason="ambiguous",
                candidates=[Candidate(id=i.id, name=i.name, score=100.0) for i in by_alias],
            )
        # 3. fuzzy
        return self._fuzzy(key, items)

    def _fuzzy(self, key: str, items: list[ReferenceItem]) -> MatchResult:
        s = self.settings
        choices: list[str] = []
        owners: list[ReferenceItem] = []
        for item in items:
            for text in (item.name, *item.aliases):
                norm = normalize_text(text)
                if norm:
                    choices.append(norm)
                    owners.append(item)

        best: dict[int, tuple[float, ReferenceItem]] = {}
        for _choice, score, idx in process.extract(key, choices, scorer=self._scorer, limit=None):
            item = owners[idx]
            if item.id not in best or score > best[item.id][0]:
                best[item.id] = (float(score), item)
        ranked = sorted(best.values(), key=lambda t: t[0], reverse=True)
        if not ranked:
            return MatchResult(status="unresolved", reason="no_match")

        top_score, top_item = ranked[0]
        cands = [
            Candidate(id=it.id, name=it.name, score=round(sc, 1))
            for sc, it in ranked[: s.FUZZY_MAX_CANDIDATES]
            if sc >= s.FUZZY_CANDIDATE_THRESHOLD
        ]
        if top_score >= s.FUZZY_AUTO_MATCH_THRESHOLD:
            if len(ranked) > 1 and ranked[1][0] >= top_score - s.FUZZY_AMBIGUITY_MARGIN:
                close = [c for c in cands if c.score >= top_score - s.FUZZY_AMBIGUITY_MARGIN]
                return MatchResult(status="unresolved", reason="ambiguous", score=round(top_score, 1),
                                   candidates=close)
            return MatchResult(status="resolved", item=top_item, match_type="fuzzy", score=round(top_score, 1),
                               candidates=cands)
        if top_score >= s.FUZZY_CANDIDATE_THRESHOLD:
            return MatchResult(status="unresolved", reason="low_confidence", score=round(top_score, 1),
                               candidates=cands)
        return MatchResult(status="unresolved", reason="no_match", score=round(top_score, 1))


def _unique(items: list[ReferenceItem]) -> list[ReferenceItem]:
    seen: dict[int, ReferenceItem] = {}
    for i in items:
        seen.setdefault(i.id, i)
    return list(seen.values())
