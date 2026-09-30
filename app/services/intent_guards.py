"""Deterministic post-checks on the LLM intent, driven only by what the user literally wrote.

The 4B model occasionally puts an age on the wrong side ("30 ёшдан катта" -> toAge 30). When the query contains an
unambiguous direction phrase for a number and the model assigned that exact number to the opposite field, the value
is moved to the right field. Guards never add a value the model did not extract and never touch other fields.
"""

from __future__ import annotations

import re

from app.models.ai_intent import ParsedSearchIntent
from app.models.enums import TYPE_OF_POSITION_PHRASES, TypeOfPosition, parse_type_of_position
from app.utils.text_normalizer import normalize_text

_N = r"(\d{1,3})"
_SEP = r"[\s\-']*"  # "30 yoshdan", "30-yoshdan", "30'dan"
_YOSH = rf"{_SEP}(?:(?:yosh|yoshdan|ёш|ёшдан|dan|дан|лет|года){_SEP})?"

# order matters: "не старше N" must win over "старше N"
_MAX_PATTERNS = [
    re.compile(rf"не\s+старше\s+{_N}"),
    re.compile(rf"(?<!не\s)моложе\s+{_N}"),
    re.compile(rf"(?<!не\s)младше\s+{_N}"),
    re.compile(rf"(?<!\w)до\s+{_N}\s*(?:лет|года|год)"),
    re.compile(rf"{_N}{_YOSH}(?:gacha|гача)\b"),
    re.compile(rf"{_N}{_SEP}(?:yoshgacha|ёшгача)"),
    re.compile(rf"{_N}{_SEP}(?:yoshdan|ёшдан)\s*(?:kichik|кичик)"),
]
_MIN_PATTERNS = [
    re.compile(rf"(?<!не\s)старше\s+{_N}"),
    re.compile(rf"не\s+(?:моложе|младше)\s+{_N}"),
    re.compile(rf"{_N}\s*(?:лет|года|год)\s+и\s+старше"),
    re.compile(rf"{_N}{_SEP}(?:yoshdan|ёшдан|dan|дан)\s*(?:katta|катта|oshgan|ошган|yuqori|юқори)"),
]


def _numbers(patterns: list[re.Pattern], text: str) -> set[int]:
    found: set[int] = set()
    for pat in patterns:
        found |= {int(m.group(1)) for m in pat.finditer(text)}
    return found


def fix_age_direction(query: str, intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """Return (possibly corrected intent, list of correction codes)."""
    text = normalize_text(query)
    mins = _numbers(_MIN_PATTERNS, text)
    maxs = _numbers(_MAX_PATTERNS, text)
    only_min, only_max = mins - maxs, maxs - mins
    data = intent.model_dump()
    fixes: list[str] = []
    if data.get("toAge") in only_min and data.get("fromAge") is None:
        data["fromAge"], data["toAge"] = data["toAge"], None
        fixes.append("toAge->fromAge")
    elif data.get("fromAge") in only_max and data.get("toAge") is None:
        data["toAge"], data["fromAge"] = data["fromAge"], None
        fixes.append("fromAge->toAge")
    if not fixes:
        return intent, fixes
    return ParsedSearchIntent.model_validate({k: v for k, v in data.items() if v is not None}), fixes


_NUMERIC_FIELDS = ("fromAge", "toAge", "fromYear", "toYear", "experienceYear")
# stems (after normalize_text) that must appear for an `education` value to count as stated
_EDUCATION_MARKERS = (
    "oliy", "олий", "высш", "o'rta", "ўрта", "урта", "средн", "maxsus", "махсус", "ma'lumotli", "маълумотли",
    "образован", "boshlang'ich", "бошланғич", "начальное образ", "bakalavr", "бакалавр", "magistr", "магистр",
)


def drop_unstated(query: str, intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """Remove values with no evidence in the query text (CLAUDE.md §3.5 — never invent).

    - numeric fields: the number must be written as digits in the query ("тажрибали ёш кадрлар" has no age);
    - education: an education word must be present (a profession says nothing about education).
    Numbers written as words ("o'ttiz") are not supported and are dropped — documented limitation.
    """
    text = normalize_text(query)
    digits = {int(n) for n in re.findall(r"\d+", text)}
    data = intent.model_dump()
    dropped: list[str] = []
    for key in _NUMERIC_FIELDS:
        if data.get(key) is not None and data[key] not in digits:
            data[key] = None
            dropped.append(f"drop:{key}")
    if data.get("education") and not any(m in text for m in _EDUCATION_MARKERS):
        data["education"] = None
        dropped.append("drop:education")
    if not dropped:
        return intent, dropped
    return ParsedSearchIntent.model_validate({k: v for k, v in data.items() if v is not None}), dropped


# stems that must appear for a `sex` value to count as stated
_SEX_MARKERS = (
    "erkak", "эркак", "ayol", "аёл", "мужч", "мужск", "женщ", "женск", "qiz", "қиз", "девуш", "yigit", "йигит",
    "xotin", "хотин", "сотрудниц", "парн",
)


def drop_unstated_sex(query: str, intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """`sex` needs a gender word in the query ("тажрибали ёш кадрлар" says nothing about sex)."""
    if not intent.sex or any(m in normalize_text(query) for m in _SEX_MARKERS):
        return intent, []
    return intent.model_copy(update={"sex": None}), ["drop:sex"]


def move_position_types(intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """A TypeOfPosition name/phrase placed in `specialists` ("mutaxassislar" -> ["Expert"]) is a position type,
    never a speciality: move it to `typeOfPosition` (enum parsing is exact, never fuzzy)."""
    if not intent.specialists:
        return intent, []
    keep, moved = [], []
    for value in intent.specialists:
        member, _ = parse_type_of_position(value)
        (moved if member is not None and member.name != "All" else keep).append(member.name if member else value)
    if not moved:
        return intent, []
    positions = list(dict.fromkeys([*(intent.typeOfPosition or []), *moved]))
    return intent.model_copy(update={"specialists": keep or None, "typeOfPosition": positions}), [
        "specialists->typeOfPosition"
    ]


def canonicalize_positions(intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """Rewrite each typeOfPosition value that parses (exact enum name/phrase, never fuzzy) to its canonical enum
    name and remove duplicates: ["Leader", "Director"] -> ["Leader"]. Unknown values are kept for the resolver."""
    if not intent.typeOfPosition:
        return intent, []
    out: list[str] = []
    for value in intent.typeOfPosition:
        member, _ = parse_type_of_position(value)
        name = member.name if member is not None else value
        if name not in out:
            out.append(name)
    if out == intent.typeOfPosition:
        return intent, []
    return intent.model_copy(update={"typeOfPosition": out}), ["canonical:typeOfPosition"]


# evidence for dateOfReceipt (date of hiring): a hiring word must be present, otherwise a birth year is not a
# receipt date ("1985-yilda tug'ilgan" must never become dateOfReceipt)
_RECEIPT_MARKERS = ("qabul", "қабул", "принят", "приём", "прием", "ishga kir", "ишга кир", "ishga olin", "ишга олин")


def drop_unstated_receipt(query: str, intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    if not intent.dateOfReceipt or any(m in normalize_text(query) for m in _RECEIPT_MARKERS):
        return intent, []
    return intent.model_copy(update={"dateOfReceipt": None}), ["drop:dateOfReceipt"]


# stems that must appear in the query for a typeOfPosition value to count as stated
_POSITION_EVIDENCE: dict[TypeOfPosition, tuple[str, ...]] = {
    TypeOfPosition.Leader: ("rahbar", "raxbar", "раҳбар", "рахбар", "boshli", "бошли", "direktor", "директор",
                            "руковод", "начальн", "leader", "director"),
    TypeOfPosition.Management: ("boshqaruv", "бошқарув", "menejer", "менеджер", "менежер", "управлен", "manage"),
    TypeOfPosition.Technic: ("texni", "техни", "technic"),
    TypeOfPosition.Expert: ("mutaxassis", "мутахассис", "специалист", "ekspert", "эксперт", "expert"),
    TypeOfPosition.ProductionEmployee: ("ishlab chiqarish", "ишлаб чиқариш", "производств", "production"),
    TypeOfPosition.ServiceEmployee: ("xizmat ko'rsat", "хизмат кўрсат", "обслужив", "service"),
}


def drop_unstated_positions(query: str, intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """Each typeOfPosition value needs one of its stems in the query ("кадрлар" is not a position type).
    Unknown values are left for the resolver to report as unresolved."""
    if not intent.typeOfPosition:
        return intent, []
    text = normalize_text(query)
    keep = []
    for value in intent.typeOfPosition:
        member, _ = parse_type_of_position(value)
        if member is None or member not in _POSITION_EVIDENCE:
            keep.append(value)
            continue
        # stems ("boshli" covers boshliq / boshlig'i) or any full phrase of the member from app/models/enums.py
        phrases = (normalize_text(ph) for ph in TYPE_OF_POSITION_PHRASES.get(member, []))
        if any(st in text for st in _POSITION_EVIDENCE[member]) or any(ph in text for ph in phrases):
            keep.append(value)
    if len(keep) == len(intent.typeOfPosition):
        return intent, []
    return intent.model_copy(update={"typeOfPosition": keep or None}), ["drop:typeOfPosition"]


def apply_all(query: str, intent: ParsedSearchIntent) -> tuple[ParsedSearchIntent, list[str]]:
    """All guards in order; returns the corrected intent and every correction code."""
    fixes: list[str] = []
    for guard in (lambda i: fix_age_direction(query, i), lambda i: drop_unstated(query, i),
                  lambda i: drop_unstated_sex(query, i), lambda i: drop_unstated_receipt(query, i),
                  move_position_types, canonicalize_positions, lambda i: drop_unstated_positions(query, i)):
        intent, found = guard(intent)
        fixes += found
    return intent, fixes
