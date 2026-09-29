"""ParsedSearchIntent (text) -> SearchModel (IDs / enums / validated direct values).

Deterministic: reference IDs come only from reference data, never from the LLM (CLAUDE.md §3.6).
Nothing is defaulted except the contract-required `academicTitleId`, `first`, `rows`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.core.metrics import FUZZY_SCORE, RESOLVER_FIELDS
from app.models.ai_intent import ParsedSearchIntent
from app.models.enums import TypeOfPosition, parse_type_of_position, parse_type_of_service
from app.models.labels import BOOL_LABELS, ENUM_LABELS, label_for
from app.models.references import DICTIONARY_FIELDS, ReferenceData
from app.models.responses import RecognizedFilter, ResolveResult, UnresolvedFilter
from app.models.search import SearchModel
from app.services.fuzzy_matcher import FuzzyMatcher
from app.utils.text_normalizer import normalize_text

NO_FILTERS_WARNING = "So'rovda aniq filtr topilmadi (no filters recognized)."

# intent key -> (SearchModel attr, ISearchModel field)
_DIRECT_TEXT = {
    "firstName": ("first_name", "firstName"),
    "lastName": ("last_name", "lastName"),
    "middleName": ("middle_name", "middleName"),
    "institutionName": ("institution_name", "institutionName"),
    "term": ("term", "term"),
}
_DIRECT_BOOL = {
    "isAwarded": ("is_awarded", "isAwarded"),
    "isReserved": ("is_reserved", "isReserved"),
    "isAcademicDegree": ("is_academic_degree", "isAcademicDegree"),
    "isAcademicTitle": ("is_academic_title", "isAcademicTitle"),
    "isGlobalEducation": ("is_global_education", "isGlobalEducation"),
}
# intent key -> (attr, field, min, max or None for "current year")
_DIRECT_INT = {
    "fromAge": ("from_age", "fromAge", 0, 120),
    "toAge": ("to_age", "toAge", 0, 120),
    "fromYear": ("from_year", "fromYear", 1900, None),
    "toYear": ("to_year", "toYear", 1900, None),
    "experienceYear": ("experience_year", "experienceYear", 0, 60),
}
_DIGIT_IDS = {
    "pinfl": ("pinfl", "pinfl", 14),
    "inn": ("inn", "inn", 9),
    "institutionInn": ("institution_inn", "institutionInn", 9),
}


class FilterResolver:
    def __init__(self, settings: Settings | None = None, matcher: FuzzyMatcher | None = None):
        self.settings = settings or get_settings()
        self.matcher = matcher or FuzzyMatcher(self.settings)

    @staticmethod
    def required_collections(intent: ParsedSearchIntent) -> list[str]:
        data = intent.to_compact_dict()
        return [f.collection for f in DICTIONARY_FIELDS if f.intent_key in data]

    def resolve(
        self,
        intent: ParsedSearchIntent,
        references: ReferenceData,
        first: int | None = None,
        rows: int | None = None,
    ) -> ResolveResult:
        s = self.settings
        data = intent.to_compact_dict()
        values: dict[str, Any] = {
            "academic_title_id": s.ACADEMIC_TITLE_ID_DEFAULT,
            "first": first if first is not None else 0,
            "rows": rows if rows is not None else s.DEFAULT_SEARCH_ROWS,
        }
        recognized: list[RecognizedFilter] = []
        unresolved: list[UnresolvedFilter] = []
        warnings: list[str] = []
        validation_errors = 0

        def ok(field: str, value: Any, display: str, match_type: str, inp: Any = None, score=None) -> None:
            uz, ru = label_for(field)
            recognized.append(RecognizedFilter(field=field, label=uz, labelRu=ru, displayValue=display,
                                               value=value, matchType=match_type, inputValue=inp, score=score))
            RESOLVER_FIELDS.labels(result="resolved").inc()

        def bad(field: str, inp: Any, reason: str, candidates=None, message: str | None = None) -> None:
            uz, ru = label_for(field)
            unresolved.append(UnresolvedFilter(field=field, label=uz, labelRu=ru, inputValue=inp, reason=reason,
                                               candidates=candidates or [], message=message))
            RESOLVER_FIELDS.labels(result="candidate" if candidates else "unresolved").inc()
            if message:
                warnings.append(message)

        # 1. dictionary-backed fields
        for rule in DICTIONARY_FIELDS:
            if rule.intent_key not in data:
                continue
            raw = data[rule.intent_key]
            inputs = raw if isinstance(raw, list) else [raw]
            items = references.get(rule.collection) or []
            ids: list[int] = []
            for inp in inputs:
                m = self.matcher.match(str(inp), items)
                if m.match_type == "fuzzy" or (m.status == "unresolved" and m.score is not None):
                    FUZZY_SCORE.observe(m.score or 0)
                if m.status == "resolved" and m.item is not None:
                    ids.append(m.item.id)
                    ok(rule.api_field, m.item.id if not rule.is_list else [m.item.id], m.item.name,
                       m.match_type or "exact", inp, m.score)
                else:
                    msg = _unresolved_message(label_for(rule.api_field)[0], inp, m.reason or "no_match")
                    bad(rule.api_field, inp, m.reason or "no_match", m.candidates, msg)
            if ids:
                if rule.is_list:
                    values[rule.model_attr] = list(dict.fromkeys(ids))
                else:
                    values[rule.model_attr] = ids[0]

        # 2. enums
        if "typeOfPosition" in data:
            members = []
            for inp in data["typeOfPosition"]:
                member, _ = parse_type_of_position(inp)
                if member == TypeOfPosition.All:
                    warnings.append("'Barchasi' lavozim turi filtr emas — e'tiborga olinmadi.")
                    continue
                if member is None:
                    bad("typeOfPosition", inp, "no_match",
                        message=f"Lavozim turi aniqlanmadi: '{inp}'.")
                elif member not in members:
                    members.append(member)
                    ok("typeOfPosition", [int(member)], ENUM_LABELS["typeOfPosition"][int(member)][0], "enum", inp)
            if members:
                values["type_of_position"] = members
        if "typeOfService" in data:
            member, _ = parse_type_of_service(data["typeOfService"])
            if member is None:
                bad("typeOfService", data["typeOfService"], "no_match",
                    message=f"Xizmat turi aniqlanmadi: '{data['typeOfService']}'.")
            else:
                values["type_of_service"] = member
                ok("typeOfService", int(member), ENUM_LABELS["typeOfService"][int(member)][0], "enum",
                   data["typeOfService"])

        # 3. direct values
        for key, (attr, field) in _DIRECT_TEXT.items():
            if key in data and key != "term":
                values[attr] = data[key]
                ok(field, data[key], data[key], "direct")
        for key, (attr, field) in _DIRECT_BOOL.items():
            if key in data:
                values[attr] = data[key]
                ok(field, data[key], BOOL_LABELS[data[key]][0], "direct")

        current_year = date.today().year
        for key, (attr, field, lo, hi) in _DIRECT_INT.items():
            if key not in data:
                continue
            v = data[key]
            hi = current_year if hi is None else hi
            if not lo <= v <= hi:
                validation_errors += 1
                bad(field, v, "validation", message=f"{label_for(field)[0]}: {v} ruxsat etilgan oraliqdan tashqarida "
                                                    f"({lo}..{hi}).")
                continue
            values[attr] = v
            ok(field, v, str(v), "direct")

        for lo_key, hi_key, what in (("fromAge", "toAge", "Yosh"), ("fromYear", "toYear", "Tug'ilgan yil")):
            lo_v, hi_v = values.get(_DIRECT_INT[lo_key][0]), values.get(_DIRECT_INT[hi_key][0])
            if lo_v is not None and hi_v is not None and lo_v > hi_v:
                validation_errors += 1
                msg = f"{what} oralig'i noto'g'ri: {lo_v} > {hi_v}."
                for key in (lo_key, hi_key):
                    attr, field = _DIRECT_INT[key][0], _DIRECT_INT[key][1]
                    values.pop(attr, None)
                    recognized[:] = [r for r in recognized if r.field != field]
                    bad(field, data[key], "validation", message=msg if key == lo_key else None)

        for key, (attr, field, length) in _DIGIT_IDS.items():
            if key not in data:
                continue
            raw = str(data[key]).strip().replace(" ", "")
            if not (raw.isdigit() and len(raw) == length):
                validation_errors += 1
                bad(field, "***", "validation",
                    message=f"{label_for(field)[0]} {length} ta raqamdan iborat bo'lishi kerak.")
                continue
            values[attr] = raw if key == "pinfl" else int(raw)
            ok(field, values[attr], raw, "direct")

        if "dateOfReceipt" in data:
            parsed = _parse_date(data["dateOfReceipt"])
            if parsed is None:
                validation_errors += 1
                bad("dateOfReceipt", data["dateOfReceipt"], "validation",
                    message="Qabul qilingan sana ISO formatda bo'lishi kerak (YYYY-MM-DD).")
            else:
                values["date_of_receipt"] = parsed
                ok("dateOfReceipt", parsed.isoformat(), parsed.isoformat(), "direct")

        # 4. term: free text only when it does not duplicate a structured value
        if "term" in data:
            term = data["term"]
            used = {normalize_text(str(v)) for k, v in data.items() if k != "term" and not isinstance(v, list)}
            used |= {normalize_text(str(i)) for k, v in data.items() if isinstance(v, list) for i in v}
            if normalize_text(term) in used:
                warnings.append("Kalit so'z boshqa filtrni takrorlagani uchun olib tashlandi.")
            else:
                values["term"] = term
                ok("term", term, term, "direct")

        model = SearchModel(**values)
        if not recognized and not unresolved:
            warnings.append(NO_FILTERS_WARNING)
        ready = not unresolved and validation_errors == 0
        return ResolveResult(searchModel=model.to_api_json(), recognizedFilters=recognized,
                             unresolvedFilters=unresolved, warnings=warnings, readyToSearch=ready)


def _unresolved_message(label: str, inp: Any, reason: str) -> str:
    if reason == "ambiguous":
        return f"{label}: '{inp}' bir nechta qiymatga mos keladi — tanlang."
    if reason == "low_confidence":
        return f"{label}: '{inp}' aniq topilmadi — nomzodlardan birini tanlang."
    if reason == "no_reference_data":
        return f"{label}: ma'lumotnoma mavjud emas, '{inp}' aniqlanmadi."
    return f"{label}: '{inp}' ma'lumotnomada topilmadi."


def _parse_date(value: str) -> date | None:
    for parser in (date.fromisoformat, lambda v: datetime.fromisoformat(v).date()):
        try:
            return parser(value.strip())
        except (ValueError, AttributeError):
            continue
    for fmt in ("%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None
