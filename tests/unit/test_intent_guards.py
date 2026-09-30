"""Spec tests for app/services/intent_guards.py (deterministic post-checks on LLM intents)."""

import logging

import pytest
from prometheus_client import REGISTRY

from app.models.ai_intent import ParsedSearchIntent
from app.services.intent_guards import (
    apply_all,
    drop_unstated,
    drop_unstated_positions,
    drop_unstated_sex,
    fix_age_direction,
    move_position_types,
)
from app.services.intent_parser import IntentParser
from app.services.llm_service import FakeLLMService


def I(**kw) -> ParsedSearchIntent:  # noqa: E743, N802
    return ParsedSearchIntent(**kw)


def compact(intent: ParsedSearchIntent) -> dict:
    return intent.to_compact_dict()


# --------------------------------------------------------------------------- A. fix_age_direction

MIN_QUERIES = [
    "30 yoshdan katta ayollar",
    "30 ёшдан катта аёллар",
    "Олий маълумотли 30 ёшдан катта аёллар",
    "30 dan oshgan erkaklar",
    "30 дан ошган эркаклар",
    "30 yoshdan yuqori xodimlar",
    "сотрудники старше 30 лет",
    "Женщины СТАРШЕ 30",
    "30 YOSHDAN KATTA ayollar",
]


@pytest.mark.parametrize("query", MIN_QUERIES)
def test_min_phrase_moves_toage_to_fromage(query):
    out, fixes = fix_age_direction(query, I(toAge=30, sex="Ayol"))
    assert out.fromAge == 30
    assert out.toAge is None
    assert out.sex == "Ayol"
    assert fixes == ["toAge->fromAge"]


MAX_QUERIES = [
    "30 yoshgacha bo'lgan xodimlar",
    "30 ёшгача ходимлар",
    "30 gacha yoshdagi ayollar",
    "30 yoshdan kichik xodimlar",
    "сотрудники до 30 лет",
    "сотрудники моложе 30",
    "сотрудники не старше 30 лет",
]


@pytest.mark.parametrize("query", MAX_QUERIES)
def test_max_phrase_moves_fromage_to_toage(query):
    out, fixes = fix_age_direction(query, I(fromAge=30, region="Toshkent"))
    assert out.toAge == 30
    assert out.fromAge is None
    assert out.region == "Toshkent"
    assert fixes == ["fromAge->toAge"]


@pytest.mark.parametrize("query", MIN_QUERIES)
def test_min_phrase_correct_intent_unchanged(query):
    intent = I(fromAge=30, sex="Ayol")
    out, fixes = fix_age_direction(query, intent)
    assert compact(out) == {"fromAge": 30, "sex": "Ayol"}
    assert fixes == []


@pytest.mark.parametrize("query", MAX_QUERIES)
def test_max_phrase_correct_intent_unchanged(query):
    out, fixes = fix_age_direction(query, I(toAge=30))
    assert compact(out) == {"toAge": 30}
    assert fixes == []


def test_ne_starshe_is_not_min():
    # "не старше 30" is a MAX phrase; toAge 30 must stay toAge
    out, fixes = fix_age_direction("не старше 30 лет", I(toAge=30))
    assert compact(out) == {"toAge": 30}
    assert fixes == []


@pytest.mark.parametrize(
    "query,intent",
    [
        ("от 25 до 40 лет", {"fromAge": 25, "toAge": 40}),
        ("20 dan 35 gacha yoshdagi xodimlar", {"fromAge": 20, "toAge": 35}),
        ("25-40 yosh ayollar", {"fromAge": 25, "toAge": 40}),
        ("25 yoshdan 40 yoshgacha", {"fromAge": 25, "toAge": 40}),
    ],
)
def test_ranges_unchanged(query, intent):
    out, fixes = fix_age_direction(query, I(**intent))
    assert compact(out) == intent
    assert fixes == []


def test_range_upper_bound_is_max():
    # the upper bound of "от X до Y лет" is a MAX phrase
    out, fixes = fix_age_direction("от 25 до 40 лет", I(fromAge=40))
    assert compact(out) == {"toAge": 40}
    assert fixes == ["fromAge->toAge"]


def test_both_fields_set_unchanged_even_if_inconsistent():
    out, fixes = fix_age_direction("30 yoshdan katta", I(fromAge=20, toAge=30))
    assert compact(out) == {"fromAge": 20, "toAge": 30}
    assert fixes == []


def test_no_direction_phrase_unchanged():
    out, fixes = fix_age_direction("30 yoshli ayollar", I(toAge=30))
    assert compact(out) == {"toAge": 30}
    assert fixes == []


def test_different_number_unchanged():
    out, fixes = fix_age_direction("30 yoshdan katta", I(toAge=40))
    assert compact(out) == {"toAge": 40}
    assert fixes == []
    out, fixes = fix_age_direction("30 yoshgacha", I(fromAge=40))
    assert compact(out) == {"fromAge": 40}
    assert fixes == []


def test_never_adds_value():
    out, fixes = fix_age_direction("30 yoshdan katta ayollar", I(sex="Ayol"))
    assert compact(out) == {"sex": "Ayol"}
    assert fixes == []
    out, fixes = fix_age_direction("сотрудники до 30 лет", I())
    assert compact(out) == {}
    assert fixes == []


def test_number_with_both_min_and_max_phrases_unchanged():
    # N appears with MIN and MAX phrases -> ambiguous, not "only" one direction
    q = "30 yoshdan katta yoki 30 yoshgacha"
    out, fixes = fix_age_direction(q, I(toAge=30))
    assert compact(out) == {"toAge": 30}
    assert fixes == []
    out, fixes = fix_age_direction(q, I(fromAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == []


def test_fix_age_direction_does_not_mutate_input():
    intent = I(toAge=30, sex="Ayol")
    fix_age_direction("30 yoshdan katta", intent)
    assert intent.toAge == 30
    assert intent.fromAge is None


def test_fix_age_other_fields_preserved():
    intent = I(toAge=30, sex="Ayol", education="Oliy", region="Toshkent", specialists=["Iqtisodiyot"],
               experienceYear=5)
    out, fixes = fix_age_direction("30 yoshdan katta", intent)
    assert fixes == ["toAge->fromAge"]
    assert compact(out) == {"fromAge": 30, "sex": "Ayol", "education": "Oliy", "region": "Toshkent",
                            "specialists": ["Iqtisodiyot"], "experienceYear": 5}


# --------------------------------------------------------------------------- B. drop_unstated

def test_drop_numeric_not_in_query():
    out, fixes = drop_unstated("тажрибали ёш кадрлар", I(fromAge=25, experienceYear=5))
    assert out.fromAge is None
    assert out.experienceYear is None
    assert sorted(fixes) == ["drop:experienceYear", "drop:fromAge"]


@pytest.mark.parametrize("field", ["fromAge", "toAge", "fromYear", "toYear", "experienceYear"])
def test_each_numeric_field_dropped_when_absent(field):
    out, fixes = drop_unstated("ayollar", I(**{field: 7}, sex="Ayol"))
    assert compact(out) == {"sex": "Ayol"}
    assert fixes == [f"drop:{field}"]


def test_numeric_present_kept():
    out, fixes = drop_unstated("1990-yilda tug'ilgan", I(fromYear=1990))
    assert compact(out) == {"fromYear": 1990}
    assert fixes == []


def test_numeric_kept_mixed():
    out, fixes = drop_unstated("30 yoshdan katta, 5 yil tajribali", I(fromAge=30, experienceYear=5, toAge=60))
    assert compact(out) == {"fromAge": 30, "experienceYear": 5}
    assert fixes == ["drop:toAge"]


def test_numeric_substring_is_not_a_match():
    # "3" is not stated as its own number in "30 yoshdan katta"
    out, fixes = drop_unstated("30 yoshdan katta", I(fromAge=30, experienceYear=3))
    assert compact(out) == {"fromAge": 30}
    assert fixes == ["drop:experienceYear"]


def test_education_dropped_without_education_word():
    out, fixes = drop_unstated("Toshkentda ishlaydigan iqtisodchilar", I(education="Oliy", region="Toshkent"))
    assert compact(out) == {"region": "Toshkent"}
    assert fixes == ["drop:education"]


@pytest.mark.parametrize(
    "query",
    [
        "oliy malumotli ayollar",
        "Oliy ma'lumotli ayollar",
        "Oliy ma’lumotli ayollar",
        "Олий маълумотли аёллар",
        "с высшим образованием",
        "o'rta maxsus ma'lumotli",
        "ўрта махсус",
        "среднее специальное",
        "boshlang'ich ta'lim",
        "бошланғич",
        "с начальным образованием",
        "начальное образование",
        "bakalavr darajasi",
        "бакалавры",
        "magistrlar",
        "магистры",
        "ВЫСШЕЕ образование",
    ],
)
def test_education_kept_with_education_word(query):
    out, fixes = drop_unstated(query, I(education="Oliy"))
    assert compact(out) == {"education": "Oliy"}
    assert fixes == []


@pytest.mark.parametrize(
    "query",
    [
        "начальники отделов",  # "начальн" of a job title is not education evidence
        "Начальники отделов в Ташкенте",
        "ma'lumot bering",  # bare "ma'lumot" (information) is not "ma'lumotli"
        "Toshkent bo'yicha ma'lumot bering",
        "начальное",  # bare "начальное" is no longer evidence (spec change)
        "начальная школа",
    ],
)
def test_education_dropped_without_education_evidence(query):
    out, fixes = drop_unstated(query, I(education="Boshlang'ich"))
    assert compact(out) == {}
    assert fixes == ["drop:education"]


@pytest.mark.parametrize(
    "query",
    [
        "oliy", "Олий", "высшее", "высш", "o'rta", "ўрта", "урта", "средне", "maxsus", "махсус",
        "ma'lumotli", "маълумотли", "образованные", "boshlang'ich", "бошланғич", "начальное образование",
        "bakalavr", "бакалавр", "magistr", "магистр",
    ],
)
def test_education_kept_each_marker(query):
    out, fixes = drop_unstated(f"xodimlar {query}", I(education="Oliy", sex="Ayol"))
    assert out.education == "Oliy"
    assert "drop:education" not in fixes


def test_drop_unstated_other_fields_untouched():
    intent = I(region="Toshkent", sex="Ayol", specialists=["Iqtisodiyot"], isAwarded=True,
               typeOfService="CivilServant")
    out, fixes = drop_unstated("ayollar", intent)
    assert compact(out) == compact(intent)
    assert fixes == []


def test_drop_unstated_no_change_empty():
    out, fixes = drop_unstated("yaxshi kadrlarni top", I())
    assert compact(out) == {}
    assert fixes == []


def test_drop_unstated_does_not_mutate_input():
    intent = I(fromAge=25, education="Oliy")
    drop_unstated("ёш кадрлар", intent)
    assert intent.fromAge == 25
    assert intent.education == "Oliy"


# --------------------------------------------------------------------------- C. through IntentParser

def _metric(kind: str) -> float:
    return REGISTRY.get_sample_value("hr_ai_intent_corrections_total", {"kind": kind}) or 0.0


Q_CYR = "Олий маълумотли 30 ёшдан катта аёллар"


def test_parser_applies_age_direction_fix():
    before = _metric("toAge->fromAge")
    fake = FakeLLMService({Q_CYR: '{"toAge":30,"sex":"Ayol","education":"Oliy"}'})
    outcome = IntentParser(fake).parse(Q_CYR)
    assert compact(outcome.intent) == {"fromAge": 30, "sex": "Ayol", "education": "Oliy"}
    assert outcome.attempts == 1
    assert _metric("toAge->fromAge") == before + 1


def test_parser_applies_drop_unstated():
    q = "Toshkentda ishlaydigan iqtisodchilar"
    before_edu = _metric("drop:education")
    before_age = _metric("drop:fromAge")
    fake = FakeLLMService({q: '{"region":"Toshkent","specialists":["Iqtisodiyot"],"education":"Oliy","fromAge":25}'})
    outcome = IntentParser(fake).parse(q)
    assert compact(outcome.intent) == {"region": "Toshkent", "specialists": ["Iqtisodiyot"]}
    assert _metric("drop:education") == before_edu + 1
    assert _metric("drop:fromAge") == before_age + 1


def test_parser_guards_applied_after_retry():
    before = _metric("toAge->fromAge")
    fake = FakeLLMService({Q_CYR: ["not json at all", '{"toAge":30,"sex":"Ayol"}']})
    outcome = IntentParser(fake).parse(Q_CYR)
    assert outcome.attempts == 2
    assert compact(outcome.intent) == {"fromAge": 30, "sex": "Ayol"}
    assert _metric("toAge->fromAge") == before + 1


def test_parser_no_fix_no_metric_change():
    q = "30 yoshdan katta ayollar"
    before = _metric("toAge->fromAge")
    fake = FakeLLMService({q: '{"fromAge":30,"sex":"Ayol"}'})
    outcome = IntentParser(fake).parse(q)
    assert compact(outcome.intent) == {"fromAge": 30, "sex": "Ayol"}
    assert _metric("toAge->fromAge") == before


def test_parser_correction_log_has_no_raw_query(caplog):
    fake = FakeLLMService({Q_CYR: '{"toAge":30,"sex":"Ayol","education":"Oliy","experienceYear":5}'})
    with caplog.at_level(logging.DEBUG):
        IntentParser(fake).parse(Q_CYR)
    info_plus = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert any("intent_corrected" in r.getMessage() for r in info_plus), "correction must be logged at INFO"
    for rec in info_plus:
        rendered = rec.getMessage() + " " + " ".join(str(v) for v in rec.__dict__.values())
        assert Q_CYR not in rendered
        assert Q_CYR.casefold() not in rendered.casefold()


# --------------------------------------------------------------------------- A2. separator extension
# Lead decision: between the number and the Uzbek suffix word, spaces, hyphens and apostrophes are all allowed,
# for both MIN and MAX phrases; they behave exactly like the space-separated forms.

MIN_SEP_QUERIES = [
    "30-yoshdan katta ayollar",
    "30'dan oshgan erkaklar",
    "30’dan oshgan erkaklar",
    "30-dan oshgan erkaklar",
    "30-yoshdan yuqori xodimlar",
    "30-ёшдан катта аёллар",
    "30-дан ошган эркаклар",
    "30 - yoshdan katta",
]

MAX_SEP_QUERIES = [
    ("40-yoshgacha bo'lgan xodimlar", 40),
    ("40'gacha yoshdagi xodimlar", 40),
    ("40-gacha yoshdagi xodimlar", 40),
    ("40-ёшгача ходимлар", 40),
    ("35-yoshdan kichik xodimlar", 35),
    ("35-ёшдан кичик ходимлар", 35),
    ("35'yoshdan kichik xodimlar", 35),
]


@pytest.mark.parametrize("query", MIN_SEP_QUERIES)
def test_sep_min_phrase_moves_toage_to_fromage(query):
    out, fixes = fix_age_direction(query, I(toAge=30, sex="Ayol"))
    assert compact(out) == {"fromAge": 30, "sex": "Ayol"}
    assert fixes == ["toAge->fromAge"]


@pytest.mark.parametrize("query", MIN_SEP_QUERIES)
def test_sep_min_phrase_correct_intent_unchanged(query):
    out, fixes = fix_age_direction(query, I(fromAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == []


@pytest.mark.parametrize("query,n", MAX_SEP_QUERIES)
def test_sep_max_phrase_moves_fromage_to_toage(query, n):
    out, fixes = fix_age_direction(query, I(fromAge=n, region="Toshkent"))
    assert compact(out) == {"toAge": n, "region": "Toshkent"}
    assert fixes == ["fromAge->toAge"]


@pytest.mark.parametrize("query,n", MAX_SEP_QUERIES)
def test_sep_max_phrase_correct_intent_unchanged(query, n):
    out, fixes = fix_age_direction(query, I(toAge=n))
    assert compact(out) == {"toAge": n}
    assert fixes == []


@pytest.mark.parametrize(
    "spaced,hyphenated,before",
    [
        ("30 yoshdan katta", "30-yoshdan katta", {"toAge": 30}),
        ("30 dan oshgan", "30'dan oshgan", {"toAge": 30}),
        ("40 yoshgacha", "40-yoshgacha", {"fromAge": 40}),
        ("35 yoshdan kichik", "35-yoshdan kichik", {"fromAge": 35}),
    ],
)
def test_sep_forms_equivalent_to_spaced(spaced, hyphenated, before):
    a = fix_age_direction(spaced, I(**before))
    b = fix_age_direction(hyphenated, I(**before))
    assert compact(a[0]) == compact(b[0])
    assert a[1] == b[1] != []


@pytest.mark.parametrize(
    "query,intent",
    [
        ("25-yoshdan 40-yoshgacha", {"fromAge": 25, "toAge": 40}),
        ("20-dan 35-gacha yoshdagi xodimlar", {"fromAge": 20, "toAge": 35}),
        ("25-40 yosh ayollar", {"fromAge": 25, "toAge": 40}),
    ],
)
def test_sep_ranges_unchanged(query, intent):
    out, fixes = fix_age_direction(query, I(**intent))
    assert compact(out) == intent
    assert fixes == []


def test_sep_different_number_unchanged():
    out, fixes = fix_age_direction("30-yoshdan katta", I(toAge=40))
    assert compact(out) == {"toAge": 40}
    assert fixes == []
    out, fixes = fix_age_direction("40-yoshgacha", I(fromAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == []


def test_sep_never_adds_value():
    out, fixes = fix_age_direction("30-yoshdan katta ayollar", I(sex="Ayol"))
    assert compact(out) == {"sex": "Ayol"}
    assert fixes == []


def test_sep_parser_end_to_end():
    q = "30-yoshdan katta ayollar"
    before = _metric("toAge->fromAge")
    outcome = IntentParser(FakeLLMService({q: '{"toAge":30,"sex":"Ayol"}'})).parse(q)
    assert compact(outcome.intent) == {"fromAge": 30, "sex": "Ayol"}
    assert _metric("toAge->fromAge") == before + 1


# --------------------------------------------------------------------------- D. drop_unstated_sex

@pytest.mark.parametrize("sex", ["Erkak", "Ayol"])
def test_sex_dropped_without_gender_word(sex):
    out, fixes = drop_unstated_sex("тажрибали ёш кадрлар", I(sex=sex, region="Toshkent"))
    assert compact(out) == {"region": "Toshkent"}
    assert fixes == ["drop:sex"]


@pytest.mark.parametrize(
    "query",
    [
        "Женщины старше 30 лет",
        "ayollar",
        "ЭРКАКЛАР",
        "erkak xodimlar",
        "Олий маълумотли аёллар",
        "мужчины",
        "мужского пола",
        "женского пола",
        "qizlar",
        "қизлар",
        "девушки",
        "yigitlar",
        "йигитлар",
        "xotin-qizlar",
        "хотин-қизлар",
        "сотрудницы",
        "парни",
        "AYOL xodimlar",
    ],
)
def test_sex_kept_with_gender_word(query):
    out, fixes = drop_unstated_sex(query, I(sex="Ayol", fromAge=30))
    assert compact(out) == {"sex": "Ayol", "fromAge": 30}
    assert fixes == []


def test_sex_absent_unchanged():
    out, fixes = drop_unstated_sex("Toshkentdagi xodimlar", I(region="Toshkent", education="Oliy"))
    assert compact(out) == {"region": "Toshkent", "education": "Oliy"}
    assert fixes == []


def test_drop_sex_other_fields_untouched():
    intent = I(sex="Erkak", region="Toshkent", fromAge=25, education="Oliy", specialists=["Iqtisodiyot"],
               typeOfPosition=["Leader"], isAwarded=True)
    out, fixes = drop_unstated_sex("Toshkentdagi iqtisodchilar", intent)
    expected = compact(intent)
    del expected["sex"]
    assert compact(out) == expected
    assert fixes == ["drop:sex"]


def test_drop_sex_does_not_mutate_input():
    intent = I(sex="Erkak")
    drop_unstated_sex("кадрлар", intent)
    assert intent.sex == "Erkak"


# --------------------------------------------------------------------------- E. move_position_types

def test_move_enum_name_appended_after_existing():
    out, fixes = move_position_types(I(specialists=["Expert"], typeOfPosition=["Leader"]))
    assert compact(out) == {"typeOfPosition": ["Leader", "Expert"]}
    assert out.specialists is None
    assert fixes == ["specialists->typeOfPosition"]


def test_move_phrase_keeps_other_specialists():
    out, fixes = move_position_types(I(specialists=["Iqtisodiyot", "mutaxassis"]))
    assert compact(out) == {"specialists": ["Iqtisodiyot"], "typeOfPosition": ["Expert"]}
    assert fixes == ["specialists->typeOfPosition"]


def test_move_no_position_unchanged():
    out, fixes = move_position_types(I(specialists=["Iqtisodiyot"], region="Toshkent"))
    assert compact(out) == {"specialists": ["Iqtisodiyot"], "region": "Toshkent"}
    assert fixes == []


def test_move_without_specialists_unchanged():
    out, fixes = move_position_types(I(typeOfPosition=["Leader"]))
    assert compact(out) == {"typeOfPosition": ["Leader"]}
    assert fixes == []


@pytest.mark.parametrize(
    "phrase,name",
    [
        ("rahbar", "Leader"),
        ("руководитель", "Leader"),
        ("direktor", "Leader"),
        ("menejer", "Management"),
        ("texnik", "Technic"),
        ("техник", "Technic"),
        ("специалист", "Expert"),
        ("мутахассис", "Expert"),
        ("ekspert", "Expert"),
        ("ishlab chiqarish xodimi", "ProductionEmployee"),
        ("обслуживающий персонал", "ServiceEmployee"),
        ("Leader", "Leader"),
        ("Management", "Management"),
        ("Technic", "Technic"),
        ("ProductionEmployee", "ProductionEmployee"),
        ("ServiceEmployee", "ServiceEmployee"),
    ],
)
def test_move_each_phrase_to_enum_name(phrase, name):
    out, fixes = move_position_types(I(specialists=[phrase]))
    assert out.typeOfPosition == [name]
    assert out.specialists is None
    assert fixes == ["specialists->typeOfPosition"]


def test_move_no_duplicates():
    out, fixes = move_position_types(I(specialists=["Expert", "mutaxassis"], typeOfPosition=["Expert"]))
    assert out.typeOfPosition == ["Expert"]
    assert out.specialists is None
    assert fixes == ["specialists->typeOfPosition"]


def test_move_multiple_positions_order_preserved():
    out, fixes = move_position_types(I(specialists=["texnik", "Iqtisodiyot", "rahbar"]))
    assert out.typeOfPosition == ["Technic", "Leader"]
    assert out.specialists == ["Iqtisodiyot"]
    assert fixes == ["specialists->typeOfPosition"]


def test_move_all_excluded():
    out, fixes = move_position_types(I(specialists=["All"]))
    assert compact(out) == {"specialists": ["All"]}
    assert fixes == []


@pytest.mark.parametrize("typo", ["mutaxasis", "rahbarr", "Expertt", "специалистт"])
def test_move_never_fuzzy(typo):
    out, fixes = move_position_types(I(specialists=[typo]))
    assert compact(out) == {"specialists": [typo]}
    assert fixes == []


def test_move_other_fields_untouched():
    out, _ = move_position_types(I(specialists=["Expert"], region="Toshkent", sex="Ayol", fromAge=30))
    assert compact(out) == {"typeOfPosition": ["Expert"], "region": "Toshkent", "sex": "Ayol", "fromAge": 30}


def test_move_does_not_mutate_input():
    intent = I(specialists=["Expert"], typeOfPosition=["Leader"])
    move_position_types(intent)
    assert intent.specialists == ["Expert"]
    assert intent.typeOfPosition == ["Leader"]


# --------------------------------------------------------------------------- F. apply_all + parser

def test_apply_all_order_and_concatenation():
    # Expert stem ("mutaxassis") is in the query, so the moved position survives drop_unstated_positions
    q = "30 yoshdan katta Toshkentdagi mutaxassislar"
    intent = I(toAge=30, experienceYear=5, education="Oliy", sex="Erkak", specialists=["Iqtisodiyot", "Expert"],
               region="Toshkent")
    out, fixes = apply_all(q, intent)
    assert compact(out) == {"fromAge": 30, "region": "Toshkent", "specialists": ["Iqtisodiyot"],
                            "typeOfPosition": ["Expert"]}
    assert fixes[0] == "toAge->fromAge"
    assert sorted(fixes[1:3]) == ["drop:education", "drop:experienceYear"]
    assert fixes[3:] == ["drop:sex", "specialists->typeOfPosition"]


def test_apply_all_moved_position_then_dropped_when_unstated():
    q = "30 yoshdan katta Toshkentdagi xodimlar"
    intent = I(toAge=30, experienceYear=5, education="Oliy", sex="Erkak", specialists=["Iqtisodiyot", "Expert"],
               region="Toshkent")
    out, fixes = apply_all(q, intent)
    assert compact(out) == {"fromAge": 30, "region": "Toshkent", "specialists": ["Iqtisodiyot"]}
    assert out.typeOfPosition is None
    assert fixes[0] == "toAge->fromAge"
    assert sorted(fixes[1:3]) == ["drop:education", "drop:experienceYear"]
    assert fixes[3:] == ["drop:sex", "specialists->typeOfPosition", "drop:typeOfPosition"]


def test_apply_all_positions_dropped_last():
    # an unstated position from the LLM is dropped; the code comes after all other codes
    out, fixes = apply_all("тажрибали ёш кадрлар", I(typeOfPosition=["ProductionEmployee"], sex="Erkak"))
    assert compact(out) == {}
    assert fixes == ["drop:sex", "drop:typeOfPosition"]


def test_apply_all_age_fix_before_drop():
    out, fixes = apply_all("30 ёшдан катта аёллар", I(toAge=30, sex="Ayol"))
    assert compact(out) == {"fromAge": 30, "sex": "Ayol"}
    assert fixes == ["toAge->fromAge"]


def test_apply_all_clean_intent_unchanged():
    intent = I(education="Oliy", fromAge=30, sex="Ayol")
    out, fixes = apply_all("Oliy ma'lumotli 30 yoshdan katta ayollar", intent)
    assert compact(out) == compact(intent)
    assert fixes == []


def test_apply_all_empty():
    out, fixes = apply_all("yaxshi kadrlarni top", I())
    assert compact(out) == {}
    assert fixes == []


def test_parser_drops_sex_and_moves_position():
    q = "Toshkentdagi iqtisodchi mutaxassislar"
    b_sex = _metric("drop:sex")
    b_pos = _metric("specialists->typeOfPosition")
    fake = FakeLLMService({q: '{"region":"Toshkent","sex":"Erkak","specialists":["Iqtisodiyot","Expert"]}'})
    outcome = IntentParser(fake).parse(q)
    assert compact(outcome.intent) == {"region": "Toshkent", "specialists": ["Iqtisodiyot"],
                                       "typeOfPosition": ["Expert"]}
    assert _metric("drop:sex") == b_sex + 1
    assert _metric("specialists->typeOfPosition") == b_pos + 1


def test_parser_keeps_stated_sex():
    q = "Женщины старше 30 лет"
    b_sex = _metric("drop:sex")
    outcome = IntentParser(FakeLLMService({q: '{"sex":"Ayol","fromAge":30}'})).parse(q)
    assert compact(outcome.intent) == {"sex": "Ayol", "fromAge": 30}
    assert _metric("drop:sex") == b_sex


def test_parser_new_guards_log_has_no_raw_query(caplog):
    q = "Toshkentdagi iqtisodchi mutaxassislar"
    fake = FakeLLMService({q: '{"region":"Toshkent","sex":"Erkak","specialists":["Expert"]}'})
    with caplog.at_level(logging.DEBUG):
        IntentParser(fake).parse(q)
    info_plus = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert any("intent_corrected" in r.getMessage() for r in info_plus)
    for rec in info_plus:
        rendered = rec.getMessage() + " " + " ".join(str(v) for v in rec.__dict__.values())
        assert q.casefold() not in rendered.casefold()


# --------------------------------------------------------------------------- G. drop_unstated_positions

def test_positions_spec_example_dropped():
    out, fixes = drop_unstated_positions("тажрибали ёш кадрлар", I(typeOfPosition=["ProductionEmployee"]))
    assert out.typeOfPosition is None
    assert compact(out) == {}
    assert fixes == ["drop:typeOfPosition"]


def test_positions_spec_example_technic_kept():
    out, fixes = drop_unstated_positions("технический персонал", I(typeOfPosition=["Technic"]))
    assert compact(out) == {"typeOfPosition": ["Technic"]}
    assert fixes == []


def test_positions_spec_example_leader_kept():
    out, fixes = drop_unstated_positions("bo'lim boshliqlari va direktorlar", I(typeOfPosition=["Leader"]))
    assert compact(out) == {"typeOfPosition": ["Leader"]}
    assert fixes == []


POSITION_STEM_QUERIES = [
    ("Leader", "rahbar xodimlar"),
    ("Leader", "raxbarlar"),
    ("Leader", "раҳбарлар"),
    ("Leader", "рахбарлар"),
    ("Leader", "bo'lim boshlig'i"),
    ("Leader", "bo'lim boshliqlari"),
    ("Leader", "бўлим бошлиғи"),
    ("Leader", "бошлиқлар"),
    ("Leader", "direktorlar"),
    ("Leader", "директора школ"),
    ("Leader", "руководители"),
    ("Leader", "начальники отделов"),
    ("Leader", "Leader positions"),
    ("Management", "boshqaruv xodimlari"),
    ("Management", "бошқарув ходимлари"),
    ("Management", "menejerlar"),
    ("Management", "менеджеры"),
    ("Management", "менежерлар"),
    ("Management", "управленческий персонал"),
    ("Management", "management staff"),
    ("Technic", "texnik xodimlar"),
    ("Technic", "texnika xodimi"),
    ("Technic", "техник ходимлар"),
    ("Technic", "ТЕХНИЧЕСКИЙ персонал"),
    ("Technic", "technician"),
    ("Expert", "mutaxassislar"),
    ("Expert", "мутахассислар"),
    ("Expert", "ведущие специалисты"),
    ("Expert", "ekspertlar"),
    ("Expert", "эксперты"),
    ("Expert", "expert"),
    ("ProductionEmployee", "ishlab chiqarish xodimlari"),
    ("ProductionEmployee", "ишлаб чиқариш ходимлари"),
    ("ProductionEmployee", "производственный персонал"),
    ("ProductionEmployee", "production staff"),
    ("ServiceEmployee", "xizmat ko'rsatuvchi xodimlar"),
    ("ServiceEmployee", "xizmat ko’rsatuvchi xodimlar"),
    ("ServiceEmployee", "хизмат кўрсатувчи ходимлар"),
    ("ServiceEmployee", "обслуживающий персонал"),
    ("ServiceEmployee", "service staff"),
]


@pytest.mark.parametrize("name,query", POSITION_STEM_QUERIES)
def test_positions_kept_with_stem(name, query):
    out, fixes = drop_unstated_positions(query, I(typeOfPosition=[name], region="Toshkent"))
    assert compact(out) == {"typeOfPosition": [name], "region": "Toshkent"}
    assert fixes == []


@pytest.mark.parametrize(
    "name", ["Leader", "Management", "Technic", "Expert", "ProductionEmployee", "ServiceEmployee"]
)
def test_positions_each_dropped_without_stem(name):
    out, fixes = drop_unstated_positions("Toshkentdagi ayollar", I(typeOfPosition=[name], sex="Ayol"))
    assert compact(out) == {"sex": "Ayol"}
    assert out.typeOfPosition is None
    assert fixes == ["drop:typeOfPosition"]


def test_positions_stem_of_other_member_does_not_count():
    # "direktor" is a Leader stem, not an Expert stem
    out, fixes = drop_unstated_positions("direktorlar", I(typeOfPosition=["Expert"]))
    assert out.typeOfPosition is None
    assert fixes == ["drop:typeOfPosition"]


def test_positions_partial_drop_keeps_stated():
    out, fixes = drop_unstated_positions("rahbarlar", I(typeOfPosition=["Leader", "Expert"]))
    assert out.typeOfPosition == ["Leader"]
    assert fixes == ["drop:typeOfPosition"]


def test_positions_mixed_query_keeps_both():
    out, fixes = drop_unstated_positions("rahbarlar va mutaxassislar", I(typeOfPosition=["Leader", "Expert"]))
    assert out.typeOfPosition == ["Leader", "Expert"]
    assert fixes == []


def test_positions_phrase_value_parsed_and_checked():
    # values given as phrases still parse to a member and are checked against that member's stems
    out, fixes = drop_unstated_positions("rahbarlar", I(typeOfPosition=["mutaxassis"]))
    assert out.typeOfPosition is None
    assert fixes == ["drop:typeOfPosition"]
    out, fixes = drop_unstated_positions("mutaxassislar", I(typeOfPosition=["mutaxassis"]))
    assert out.typeOfPosition == ["mutaxassis"]
    assert fixes == []


def test_positions_unparseable_kept():
    out, fixes = drop_unstated_positions("кадрлар", I(typeOfPosition=["Kosmonavt"]))
    assert compact(out) == {"typeOfPosition": ["Kosmonavt"]}
    assert fixes == []


def test_positions_unparseable_kept_while_parsed_dropped():
    out, fixes = drop_unstated_positions("кадрлар", I(typeOfPosition=["Kosmonavt", "Expert"]))
    assert out.typeOfPosition == ["Kosmonavt"]
    assert fixes == ["drop:typeOfPosition"]


def test_positions_absent_unchanged():
    out, fixes = drop_unstated_positions("кадрлар", I(region="Toshkent"))
    assert compact(out) == {"region": "Toshkent"}
    assert fixes == []


def test_positions_other_fields_untouched():
    intent = I(typeOfPosition=["Expert"], region="Toshkent", sex="Ayol", fromAge=30, specialists=["Iqtisodiyot"],
               education="Oliy")
    out, fixes = drop_unstated_positions("Toshkentdagi ayollar", intent)
    expected = compact(intent)
    del expected["typeOfPosition"]
    assert compact(out) == expected
    assert fixes == ["drop:typeOfPosition"]


def test_positions_does_not_mutate_input():
    intent = I(typeOfPosition=["Expert"])
    drop_unstated_positions("кадрлар", intent)
    assert intent.typeOfPosition == ["Expert"]


def test_parser_drops_unstated_position():
    q = "тажрибали ёш кадрлар"
    before = _metric("drop:typeOfPosition")
    fake = FakeLLMService({q: '{"typeOfPosition":["ProductionEmployee"]}'})
    outcome = IntentParser(fake).parse(q)
    assert compact(outcome.intent) == {}
    assert _metric("drop:typeOfPosition") == before + 1


def test_parser_keeps_stated_position():
    q = "технический персонал"
    before = _metric("drop:typeOfPosition")
    outcome = IntentParser(FakeLLMService({q: '{"typeOfPosition":["Technic"]}'})).parse(q)
    assert compact(outcome.intent) == {"typeOfPosition": ["Technic"]}
    assert _metric("drop:typeOfPosition") == before


# ------------------------------------------------------------ G2. regressions (boshlig'i) + phrase evidence

def test_regression_boshligi_leader_kept():
    out, fixes = apply_all("bo'lim boshlig'i", I(typeOfPosition=["Leader"]))
    assert compact(out) == {"typeOfPosition": ["Leader"]}
    assert fixes == []


def test_regression_boshligi_specialist_moved_and_kept():
    out, fixes = apply_all("bo'lim boshlig'i", I(specialists=["bo'lim boshlig'i"]))
    assert compact(out) == {"typeOfPosition": ["Leader"]}
    assert out.specialists is None
    assert fixes == ["specialists->typeOfPosition"]


def test_regression_boshligi_cyrillic_leader_kept():
    out, fixes = apply_all("бўлим бошлиғи", I(typeOfPosition=["Leader"]))
    assert compact(out) == {"typeOfPosition": ["Leader"]}
    assert fixes == []


@pytest.mark.parametrize("q", ["boshliqlar", "bo'lim boshliqlari", "бошлиқлар", "Bo'lim boshlig'i"])
def test_boshli_stem_variants_kept(q):
    out, fixes = drop_unstated_positions(q, I(typeOfPosition=["Leader"]))
    assert out.typeOfPosition == ["Leader"]
    assert fixes == []


def test_phrase_evidence_keeps_value():
    # the full member phrase "начальник отдела" (TYPE_OF_POSITION_PHRASES[Leader]) occurs in the query
    out, fixes = drop_unstated_positions("Нужны начальник отдела и его заместитель", I(typeOfPosition=["Leader"]))
    assert out.typeOfPosition == ["Leader"]
    assert fixes == []


def _all_position_phrases():
    from app.models.enums import TYPE_OF_POSITION_PHRASES

    return [(member.name, phrase) for member, phrases in TYPE_OF_POSITION_PHRASES.items() for phrase in phrases]


@pytest.mark.parametrize("name,phrase", _all_position_phrases())
def test_every_member_phrase_is_evidence_for_its_member(name, phrase):
    # a query that is exactly one of the member's phrases must keep that member
    out, fixes = drop_unstated_positions(phrase, I(typeOfPosition=[name]))
    assert out.typeOfPosition == [name]
    assert fixes == []


@pytest.mark.parametrize("name,phrase", _all_position_phrases())
def test_every_member_phrase_as_specialist_is_moved_and_kept(name, phrase):
    # E then G must be consistent: a phrase that E moves must not be dropped by G for the same query
    out, fixes = apply_all(phrase, I(specialists=[phrase]))
    assert out.typeOfPosition == [name]
    assert out.specialists is None
    assert fixes == ["specialists->typeOfPosition"]


def test_regression_menejer_uz_cyrillic_with_technic():
    q = "Давлат хизматчиси бўлмаган техник ходимлар ва менежерлар"
    intent = I(typeOfPosition=["Technic", "Management"])
    out, fixes = drop_unstated_positions(q, intent)
    assert out.typeOfPosition == ["Technic", "Management"]
    assert fixes == []
    out, fixes = apply_all(q, intent)
    assert compact(out) == {"typeOfPosition": ["Technic", "Management"]}
    assert fixes == []


# ---------------------------------------------------- A3. review additions (младше / и старше / до)

@pytest.mark.parametrize("query", ["сотрудники младше 30 лет", "женщины младше 30", "МЛАДШЕ 30 лет"])
def test_mladshe_is_max(query):
    out, fixes = fix_age_direction(query, I(fromAge=30, sex="Ayol"))
    assert compact(out) == {"toAge": 30, "sex": "Ayol"}
    assert fixes == ["fromAge->toAge"]


def test_mladshe_correct_intent_unchanged():
    out, fixes = fix_age_direction("сотрудники младше 30 лет", I(toAge=30))
    assert compact(out) == {"toAge": 30}
    assert fixes == []


@pytest.mark.parametrize("query", ["сотрудники 30 лет и старше", "30 лет и старше", "женщины 30 года и старше"])
def test_n_let_i_starshe_is_min(query):
    out, fixes = fix_age_direction(query, I(toAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == ["toAge->fromAge"]


def test_n_let_i_starshe_correct_intent_unchanged():
    out, fixes = fix_age_direction("30 лет и старше", I(fromAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == []


@pytest.mark.parametrize("query", ["сотрудники не младше 30 лет", "сотрудники не моложе 30 лет"])
def test_ne_mladshe_is_not_max(query):
    # "не младше / не моложе 30" = at least 30 (MIN); a correct fromAge must never be flipped to toAge
    out, fixes = fix_age_direction(query, I(fromAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == []


@pytest.mark.parametrize("query", ["Бордо 30 лет", "сотрудники кредо 30 лет", "видео30 лет"])
def test_do_inside_word_is_not_max(query):
    out, fixes = fix_age_direction(query, I(fromAge=30))
    assert compact(out) == {"fromAge": 30}
    assert fixes == []


@pytest.mark.parametrize("query", ["до 40 лет", "сотрудники до 40 лет", "(до 40 лет)", "ДО 40 лет"])
def test_standalone_do_is_max(query):
    out, fixes = fix_age_direction(query, I(fromAge=40))
    assert compact(out) == {"toAge": 40}
    assert fixes == ["fromAge->toAge"]


def test_range_ot_do_still_upper_bound_max():
    out, fixes = fix_age_direction("сотрудники от 25 до 40 лет", I(fromAge=40))
    assert compact(out) == {"toAge": 40}
    assert fixes == ["fromAge->toAge"]
    out, fixes = fix_age_direction("сотрудники от 25 до 40 лет", I(fromAge=25, toAge=40))
    assert compact(out) == {"fromAge": 25, "toAge": 40}
    assert fixes == []
