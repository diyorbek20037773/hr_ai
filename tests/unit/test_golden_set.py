"""Structural checks of the golden eval sets (no model).

`golden_queries.jsonl` = main set (CLAUDE.md §7 targets); `golden_hard.jsonl` = challenge set (no target,
written from the spec, must never carry the "core" tag).
"""

import json
from pathlib import Path

import pytest

from app.models.ai_intent import ParsedSearchIntent
from app.models.enums import parse_type_of_position, parse_type_of_service
from app.models.references import DICTIONARY_FIELDS, ReferenceItem
from app.prompts.search_parser import FEW_SHOTS
from app.services.fuzzy_matcher import FuzzyMatcher
from app.utils.text_normalizer import normalize_text
from tests.conftest import GOLDEN_PATH, make_settings

HARD_PATH = GOLDEN_PATH.parent / "golden_hard.jsonl"
SETS = {"main": GOLDEN_PATH, "hard": HARD_PATH}
LANGS = {"uz-latin", "uz-cyrl", "ru"}
DICT_COLLECTION = {f.intent_key: f.collection for f in DICTIONARY_FIELDS}

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


def _load(path: Path = GOLDEN_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _key(query: str) -> str:
    return normalize_text(query)


# ---------------------------------------------------------------- shared structure (both sets)


@pytest.mark.parametrize("name", list(SETS))
def test_golden_shape(name):
    rows = _load(SETS[name])
    assert len(rows) >= (24 if name == "main" else 30)
    for r in rows:
        assert set(r) == {"query", "expectedIntent", "coreFields", "lang", "tags"}, r
        assert isinstance(r["query"], str) and r["query"].strip(), r
        assert r["lang"] in LANGS, r
        assert isinstance(r["tags"], list) and r["tags"], r
        assert set(r["coreFields"]) <= set(r["expectedIntent"]), r
        assert len(set(r["coreFields"])) == len(r["coreFields"]), r
        # expected intents are valid ParsedSearchIntent payloads (no unknown / dropped keys)
        assert ParsedSearchIntent.model_validate(r["expectedIntent"]).to_compact_dict() == r["expectedIntent"], r


@pytest.mark.parametrize("name", list(SETS))
def test_golden_no_duplicate_queries_within_set(name):
    keys = [_key(r["query"]) for r in _load(SETS[name])]
    assert len(set(keys)) == len(keys)


@pytest.mark.parametrize("name", list(SETS))
def test_golden_does_not_duplicate_few_shots(name):
    shots = {_key(q) for q, _ in FEW_SHOTS}
    assert not [r["query"] for r in _load(SETS[name]) if _key(r["query"]) in shots]


def test_main_and_hard_sets_do_not_overlap():
    main = {_key(r["query"]) for r in _load(GOLDEN_PATH)}
    assert not [r["query"] for r in _load(HARD_PATH) if _key(r["query"]) in main]


@pytest.mark.parametrize("name", list(SETS))
def test_golden_expected_values_resolve_against_references(name, references, tmp_path):
    """Every expected dictionary value must resolve (exact name / alias) so the eval can compare by ID;
    every expected enum value must parse. Otherwise a row could never pass, whatever the model does."""
    matcher = FuzzyMatcher(make_settings(tmp_path))
    problems = []
    for r in _load(SETS[name]):
        for key, value in r["expectedIntent"].items():
            values = value if isinstance(value, list) else [value]
            if key == "typeOfPosition":
                problems += [(r["query"], key, v) for v in values if parse_type_of_position(v)[0] is None]
            elif key == "typeOfService":
                problems += [(r["query"], key, v) for v in values if parse_type_of_service(v)[0] is None]
            elif key in DICT_COLLECTION:
                items: list[ReferenceItem] = references[DICT_COLLECTION[key]]
                for v in values:
                    m = matcher.match(v, items)
                    if m.status != "resolved" or m.match_type not in {"exact", "alias"}:
                        problems.append((r["query"], key, v))
    assert not problems


@pytest.mark.parametrize("name", list(SETS))
def test_golden_canonical_values(name):
    """Spec §4: sex / education / enums in canonical form; list fields have no duplicates."""
    for r in _load(SETS[name]):
        e = r["expectedIntent"]
        if "sex" in e:
            assert e["sex"] in {"Erkak", "Ayol"}, r
        if "typeOfService" in e:
            assert e["typeOfService"] in {"CivilServant", "NotCivilServant"}, r
        if "typeOfPosition" in e:
            assert set(e["typeOfPosition"]) <= {
                "Leader", "Management", "Technic", "Expert", "ProductionEmployee", "ServiceEmployee"}, r
        for k in ("fromAge", "toAge", "fromYear", "toYear", "experienceYear"):
            if k in e:
                assert type(e[k]) is int, r
        if "fromAge" in e and "toAge" in e:
            assert e["fromAge"] <= e["toAge"], r
        if "fromYear" in e and "toYear" in e:
            assert e["fromYear"] <= e["toYear"], r
        for v in e.values():
            if isinstance(v, list):
                assert len(v) == len(set(v)), r


# ---------------------------------------------------------------- main set


def test_golden_core_matches_spec():
    core = {r["query"]: r["expectedIntent"] for r in _load() if "core" in r["tags"]}
    assert core == CORE


def test_golden_coverage():
    rows = _load()
    assert {r["lang"] for r in rows} == LANGS
    assert sum(1 for r in rows if r["expectedIntent"] == {}) >= 3
    assert len({r["query"] for r in rows}) == len(rows)


# ---------------------------------------------------------------- hard (challenge) set


def test_hard_set_has_no_core_tag():
    assert not [r["query"] for r in _load(HARD_PATH) if "core" in r["tags"]]


def test_hard_set_coverage():
    rows = _load(HARD_PATH)
    assert 30 <= len(rows) <= 40
    per_lang = {lang: [r for r in rows if r["lang"] == lang] for lang in LANGS}
    assert all(len(v) >= 9 for v in per_lang.values()), {k: len(v) for k, v in per_lang.items()}
    # >= 5 negatives, at least one per language
    negatives = [r for r in rows if r["expectedIntent"] == {}]
    assert len(negatives) >= 5
    assert {r["lang"] for r in negatives} == LANGS
    assert all("negative" in r["tags"] for r in negatives)
    # a negative-tagged row really is empty
    assert all(r["expectedIntent"] == {} for r in rows if "negative" in r["tags"])
    # long multi-filter queries: several with >= 4 fields in every language
    multi = [r for r in rows if len(r["expectedIntent"]) >= 4]
    assert len(multi) >= 6 and {r["lang"] for r in multi} == LANGS
    tags = {t for r in rows for t in r["tags"]}
    assert {"multi", "typo", "negation", "names", "negative", "trap"} <= tags


def test_hard_set_names_are_not_transliterated():
    """Prompt rule: names are copied in the alphabet the user wrote them in."""
    cyr = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ")
    for r in _load(HARD_PATH):
        for k in ("firstName", "lastName", "middleName"):
            if k in r["expectedIntent"]:
                name = r["expectedIntent"][k]
                assert name in r["query"], r
                is_cyr = any(ch in cyr for ch in name.casefold())
                assert is_cyr == (r["lang"] != "uz-latin"), r
