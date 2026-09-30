"""Enums mirrored from the frontend contract + deterministic phrase mapping.

TypeScript `TypeOfPosition.Техник = 3` is exposed in Python as `Technic`; the numeric value 3 is preserved.
Enum fields are never fuzzy-matched: only enum names, numeric values, or exact (normalised) phrases resolve.
"""

from __future__ import annotations

from enum import IntEnum

from app.utils.text_normalizer import normalize_text


class TypeOfPosition(IntEnum):
    All = 0
    Leader = 1
    Management = 2
    Technic = 3  # TS: Техник
    Expert = 4
    ProductionEmployee = 5
    ServiceEmployee = 6


class TypeOfService(IntEnum):
    NotCivilServant = 0
    CivilServant = 1


TYPE_OF_POSITION_PHRASES: dict[TypeOfPosition, list[str]] = {
    TypeOfPosition.Leader: [
        "rahbar", "raxbar", "boshliq", "direktor", "director", "head", "bo'lim boshlig'i", "bo'lim boshliqlari",
        "rahbar xodim",
        "руководитель", "начальник", "директор", "начальник отдела", "раҳбар", "бошлиқ",
    ],
    TypeOfPosition.Management: [
        "boshqaruv xodimi", "menejer", "менеджер", "управленческий персонал", "бошқарув ходими",
    ],
    TypeOfPosition.Technic: ["texnik", "техник", "technician", "технический персонал"],
    TypeOfPosition.Expert: [
        "mutaxassis", "ekspert", "yetakchi mutaxassis", "bosh mutaxassis", "специалист", "эксперт",
        "ведущий специалист", "мутахассис",
    ],
    TypeOfPosition.ProductionEmployee: [
        "ishlab chiqarish xodimi", "производственный персонал", "ишлаб чиқариш ходими",
    ],
    TypeOfPosition.ServiceEmployee: [
        "xizmat ko'rsatuvchi xodim", "обслуживающий персонал", "хизмат кўрсатувчи ходим",
    ],
}

TYPE_OF_SERVICE_PHRASES: dict[TypeOfService, list[str]] = {
    TypeOfService.CivilServant: [
        "davlat xizmatchisi", "davlat fuqarolik xizmatchisi", "davlat xizmatchilari",
        "госслужащий", "государственный служащий", "госслужащие", "давлат хизматчиси",
    ],
    TypeOfService.NotCivilServant: [
        "davlat xizmatchisi bo'lmagan", "не госслужащий", "не государственный служащий",
        "давлат хизматчиси бўлмаган",
    ],
}

# Uzbek plural / possessive suffixes stripped as a deterministic fallback ("direktorlar" -> "direktor").
_UZ_SUFFIXES = ("lari", "lar", "лари", "лар")


def _build_lookup(phrases: dict) -> dict[str, IntEnum]:
    lookup: dict[str, IntEnum] = {}
    for member, items in phrases.items():
        lookup[normalize_text(member.name)] = member
        for phrase in items:
            lookup[normalize_text(phrase)] = member
    return lookup


_POSITION_LOOKUP = _build_lookup(TYPE_OF_POSITION_PHRASES)
_POSITION_LOOKUP[normalize_text("All")] = TypeOfPosition.All
_POSITION_LOOKUP[normalize_text("Техник")] = TypeOfPosition.Technic
_SERVICE_LOOKUP = _build_lookup(TYPE_OF_SERVICE_PHRASES)


def _lookup(value, enum_cls: type[IntEnum], table: dict[str, IntEnum]) -> tuple[IntEnum | None, str]:
    """Return (member, matchType) where matchType is 'enum' (name/value) or 'alias' (phrase)."""
    if isinstance(value, bool):
        return None, ""
    if isinstance(value, int):
        try:
            return enum_cls(value), "enum"
        except ValueError:
            return None, ""
    key = normalize_text(value)
    if not key:
        return None, ""
    if key.isdigit():
        return _lookup(int(key), enum_cls, table)
    for member in enum_cls:
        if normalize_text(member.name) == key:
            return member, "enum"
    if key in table:
        return table[key], "alias"
    for suffix in _UZ_SUFFIXES:
        if key.endswith(suffix) and key[: -len(suffix)] in table:
            return table[key[: -len(suffix)]], "alias"
    return None, ""


def parse_type_of_position(value) -> tuple[TypeOfPosition | None, str]:
    member, how = _lookup(value, TypeOfPosition, _POSITION_LOOKUP)
    return member, how  # type: ignore[return-value]


def parse_type_of_service(value) -> tuple[TypeOfService | None, str]:
    member, how = _lookup(value, TypeOfService, _SERVICE_LOOKUP)
    return member, how  # type: ignore[return-value]
