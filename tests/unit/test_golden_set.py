"""Structural checks of the golden eval set (no model)."""

import json

from app.models.ai_intent import ParsedSearchIntent
from app.prompts.search_parser import FEW_SHOTS
from tests.conftest import GOLDEN_PATH

CORE = {
    "Oliy ma'lumotli, 30 yoshdan katta ayollar": {"education": "Oliy", "fromAge": 30, "sex": "Ayol"},
    "Samarqand viloyatidagi 25-40 yoshli davlat xizmatchilari":
        {"region": "Samarqand viloyati", "fromAge": 25, "toAge": 40, "typeOfService": "CivilServant"},
    "Toshkentda ishlaydigan, 3 yildan ortiq staji bor iqtisodchilar":
        {"region": "Toshkent", "specialists": ["Iqtisodiyot"], "experienceYear": 3},
    "Ilmiy darajasi bor va mukofotlangan xodimlar": {"isAcademicDegree": True, "isAwarded": True},
    "Олий маълумотли 30 ёшдан катта аёллар": {"education": "Oliy", "fromAge": 30, "sex": "Ayol"},
    "Женщины старше 30 лет с высшим образованием": {"education": "Oliy", "fromAge": 30, "sex": "Ayol"},
    "Familiyasi Karimov, ismi Ali": {"lastName": "Karimov", "firstName": "Ali"},
    "Chilonzor tumanida yashaydigan, ingliz tilini biladigan, IELTS sertifikati bor yoshlar 35 yoshgacha":
        {"district": "Chilonzor", "language": "Ingliz tili", "languageCertificate": "IELTS", "toAge": 35},
    "bo'lim boshliqlari va direktorlar": {"typeOfPosition": ["Leader"]},
    "yaxshi kadrlarni top": {},
}


def _load():
    return [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_golden_shape():
    rows = _load()
    assert len(rows) >= 24
    for r in rows:
        assert set(r) == {"query", "expectedIntent", "coreFields", "lang", "tags"}
        assert r["lang"] in {"uz-latin", "uz-cyrl", "ru"}
        assert set(r["coreFields"]) <= set(r["expectedIntent"])
        # expected intents are valid ParsedSearchIntent payloads (no unknown / dropped keys)
        assert ParsedSearchIntent.model_validate(r["expectedIntent"]).to_compact_dict() == r["expectedIntent"]


def test_golden_core_matches_spec():
    core = {r["query"]: r["expectedIntent"] for r in _load() if "core" in r["tags"]}
    assert core == CORE


def test_golden_coverage():
    rows = _load()
    assert {r["lang"] for r in rows} == {"uz-latin", "uz-cyrl", "ru"}
    assert sum(1 for r in rows if r["expectedIntent"] == {}) >= 3
    assert len({r["query"] for r in rows}) == len(rows)


def test_golden_does_not_duplicate_few_shots():
    shots = {q.casefold() for q, _ in FEW_SHOTS}
    assert not [r["query"] for r in _load() if r["query"].casefold() in shots]
