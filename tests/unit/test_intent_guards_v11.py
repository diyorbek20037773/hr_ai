"""Spec tests for the prompt-v11 guards: equal-bounds age fix, fix_generic_title, fix_mixed_script_names, apply_all."""

from __future__ import annotations

import logging

import pytest
from prometheus_client import REGISTRY

from app.models.ai_intent import ParsedSearchIntent
from app.prompts.search_parser import PROMPT_VERSION
from app.services.intent_guards import (
    apply_all,
    fix_age_direction,
    fix_generic_title,
    fix_mixed_script_names,
)
from app.services.intent_parser import IntentParser
from app.services.llm_service import FakeLLMService

GENERIC = "academicTitle->isAcademicTitle"
NAME = "canonical:personName"


def I(**kw) -> ParsedSearchIntent:  # noqa: E743, N802
    return ParsedSearchIntent(**kw)


def compact(intent: ParsedSearchIntent) -> dict:
    return intent.to_compact_dict()


def test_prompt_version_is_11():
    assert PROMPT_VERSION == "11"


# --------------------------------------------------------------------------- fix_age_direction, fromAge == toAge

def test_equal_bounds_spec_example():
    q = "Олий маълумотли, 45 ёшгача бўлган эркаклар"
    out, fixes = fix_age_direction(q, I(fromAge=45, toAge=45, sex="Erkak"))
    assert compact(out) == {"toAge": 45, "sex": "Erkak"}
    assert out.fromAge is None
    assert fixes == ["drop:fromAge"]


@pytest.mark.parametrize(
    "query",
    [
        "45 yoshgacha bo'lgan xodimlar",
        "45 ёшгача ходимлар",
        "45 gacha yoshdagi ayollar",
        "45 yoshdan kichik xodimlar",
        "сотрудники до 45 лет",
        "сотрудники моложе 45",
        "сотрудники не старше 45 лет",
        "СОТРУДНИКИ ДО 45 ЛЕТ",
    ],
)
def test_equal_bounds_max_phrase_drops_fromage(query):
    out, fixes = fix_age_direction(query, I(fromAge=45, toAge=45))
    assert compact(out) == {"toAge": 45}
    assert fixes == ["drop:fromAge"]


@pytest.mark.parametrize(
    "query",
    [
        "45 yoshdan katta ayollar",
        "45 ёшдан катта аёллар",
        "45 dan oshgan erkaklar",
        "45 дан ошган эркаклар",
        "45 yoshdan yuqori xodimlar",
        "сотрудники старше 45 лет",
        "Женщины СТАРШЕ 45",
    ],
)
def test_equal_bounds_min_phrase_drops_toage(query):
    out, fixes = fix_age_direction(query, I(fromAge=45, toAge=45))
    assert compact(out) == {"fromAge": 45}
    assert fixes == ["drop:toAge"]


def test_equal_bounds_range_upper_end_is_max():
    out, fixes = fix_age_direction("от 25 до 40 лет", I(fromAge=40, toAge=40))
    assert compact(out) == {"toAge": 40}
    assert fixes == ["drop:fromAge"]


@pytest.mark.parametrize(
    "query",
    [
        "25 yoshli ayollar",
        "25 ёшли аёллар",
        "ровно 25 лет",
        "сотрудники 25 лет",
        "25 yosh",
    ],
)
def test_equal_bounds_without_direction_unchanged(query):
    out, fixes = fix_age_direction(query, I(fromAge=25, toAge=25))
    assert compact(out) == {"fromAge": 25, "toAge": 25}
    assert fixes == []


@pytest.mark.parametrize(
    "query",
    [
        "25 yoshdan katta va 25 yoshgacha",  # N with both a MIN and a MAX phrase
        "старше 25 лет и до 25 лет",
        "30 yoshgacha xodimlar",  # the phrase carries another number; N is not in it
        "сотрудники старше 30 лет",
        "ayollar",  # N is not in the query at all
    ],
)
def test_equal_bounds_otherwise_unchanged(query):
    out, fixes = fix_age_direction(query, I(fromAge=25, toAge=25))
    assert compact(out) == {"fromAge": 25, "toAge": 25}
    assert fixes == []


@pytest.mark.parametrize(
    "query",
    [
        "145 yoshgacha",
        "сотрудники до 145 лет",
        "старше 145 лет",
        "1945 yilgacha tug'ilgan",
        "до 1945 года",
    ],
)
def test_equal_bounds_n_must_match_the_whole_number(query):
    out, fixes = fix_age_direction(query, I(fromAge=45, toAge=45))
    assert compact(out) == {"fromAge": 45, "toAge": 45}
    assert fixes == []


@pytest.mark.parametrize(
    "query",
    [
        "от 45 до 45 лет",
        "45 yoshdan 45 yoshgacha",
        "45 dan 45 gacha",
    ],
)
def test_equal_bounds_degenerate_range_unchanged(query):
    # N is both the lower and the upper bound, so it does not appear "only with a MAX phrase":
    # the user asked for exactly N and {fromAge:N,toAge:N} is the right intent
    out, fixes = fix_age_direction(query, I(fromAge=45, toAge=45))
    assert compact(out) == {"fromAge": 45, "toAge": 45}
    assert fixes == []


def test_different_bounds_not_touched_by_the_equal_rule():
    out, fixes = fix_age_direction("от 25 до 40 лет", I(fromAge=25, toAge=40))
    assert compact(out) == {"fromAge": 25, "toAge": 40}
    assert fixes == []


def test_equal_bounds_does_not_mutate_input():
    intent = I(fromAge=45, toAge=45)
    fix_age_direction("45 yoshgacha", intent)
    assert compact(intent) == {"fromAge": 45, "toAge": 45}


# --------------------------------------------------------------------------- fix_generic_title

@pytest.mark.parametrize(
    "query",
    [
        "Mukofotlangan, ilmiy unvonga ega xodimlar",
        "ILMIY UNVONI bor xodimlar",
        "Илмий унвонга эга ходимлар",
        "ИЛМИЙ УНВОНИ БОР ХОДИМЛАР",
        "Сотрудники с учёным званием",
        "Сотрудники с ученым званием",
        "сотрудники, имеющие учёное звание",
    ],
)
def test_generic_title_becomes_flag(query):
    out, fixes = fix_generic_title(query, I(academicTitle="Professor", region="Toshkent"))
    assert compact(out) == {"isAcademicTitle": True, "region": "Toshkent"}
    assert out.academicTitle is None
    assert fixes == [GENERIC]


@pytest.mark.parametrize(
    "query",
    [
        "Ilmiy unvoni bor professorlar",
        "Ilmiy unvoni dotsent bo'lgan xodimlar",
        "Akademik unvoniga ega olimlar",
        "Katta ilmiy xodim unvoni bor xodimlar",
        "Илмий унвони бор профессорлар",
        "Доцент унвонига эга ходимлар",
        "Академик унвони бор олимлар",
        "Илмий ходим унвонига эга",
        "Сотрудники со званием профессора",
        "Звание доцента",
        "Звание академика",
        "Сотрудники со званием старшего научного сотрудника",
        "Звание старший научный сотрудник",
        "ПРОФЕССОР УНВОНИ",
    ],
)
def test_named_title_kept(query):
    out, fixes = fix_generic_title(query, I(academicTitle="Professor"))
    assert compact(out) == {"academicTitle": "Professor"}
    assert fixes == []


@pytest.mark.parametrize("query", ["professorlar", "профессора", "Toshkentdagi xodimlar", "доценты"])
def test_no_generic_word_unchanged(query):
    out, fixes = fix_generic_title(query, I(academicTitle="Professor"))
    assert compact(out) == {"academicTitle": "Professor"}
    assert fixes == []


@pytest.mark.parametrize("query", ["ilmiy unvonga ega xodimlar", "сотрудники с учёным званием"])
def test_generic_word_without_academic_title_unchanged(query):
    out, fixes = fix_generic_title(query, I(region="Toshkent"))
    assert compact(out) == {"region": "Toshkent"}
    assert out.isAcademicTitle is None
    assert fixes == []


def test_generic_title_flag_already_set_still_clears_title():
    out, fixes = fix_generic_title("ilmiy unvon", I(academicTitle="Professor", isAcademicTitle=True))
    assert compact(out) == {"isAcademicTitle": True}
    assert fixes == [GENERIC]


def test_generic_title_does_not_mutate_input():
    intent = I(academicTitle="Professor")
    fix_generic_title("ilmiy unvon", intent)
    assert compact(intent) == {"academicTitle": "Professor"}


# --------------------------------------------------------------------------- fix_mixed_script_names

def test_mixed_name_spec_example():
    q = "Фамилияси Тошматов бўлган ходимлар"
    out, fixes = fix_mixed_script_names(q, I(lastName="Toшматов"))  # Latin T, o
    assert compact(out) == {"lastName": "Тошматов"}
    assert fixes == [NAME]


@pytest.mark.parametrize(
    "field,value,fixed,query",
    [
        ("lastName", "Kaримов", "Каримов", "Фамилияси Каримов"),  # Latin K, a
        ("lastName", "Иванoв", "Иванов", "Сотрудник Иванов"),  # Latin o inside the word
        ("firstName", "Aзиза", "Азиза", "Исми Азиза бўлган"),  # Latin A
        ("firstName", "Мaрьям", "Марьям", "Имя Марьям"),  # Latin a
        ("middleName", "Caидович", "Саидович", "Отчество Саидович"),  # Latin C, a
        ("middleName", "Ахмeдовна", "Ахмедовна", "Отчество Ахмедовна"),  # Latin e
        ("lastName", "Toшпўлатов", "Тошпўлатов", "Фамилияси Тошпўлатов"),  # Uzbek-only letter stays
    ],
)
def test_mixed_name_each_field(field, value, fixed, query):
    out, fixes = fix_mixed_script_names(query, I(**{field: value}))
    assert compact(out) == {field: fixed}
    assert fixes == [NAME]


def test_mixed_names_several_fields_and_others_untouched():
    q = "Азиза Каримова ходимлар"
    out, fixes = fix_mixed_script_names(q, I(firstName="Aзиза", lastName="Kaримова", region="Toshkent"))
    assert compact(out) == {"firstName": "Азиза", "lastName": "Каримова", "region": "Toshkent"}
    assert set(fixes) == {NAME}


def test_only_the_field_with_evidence_is_replaced():
    q = "Азиза ходимлар"
    out, fixes = fix_mixed_script_names(q, I(firstName="Aзиза", lastName="Kaримова"))
    assert compact(out) == {"firstName": "Азиза", "lastName": "Kaримова"}
    assert fixes == [NAME]


@pytest.mark.parametrize(
    "value",
    [
        "Toshmatov",  # pure Latin
        "Тошматов",  # pure Cyrillic
        "Oʻroqov",  # pure Latin, Uzbek modifier letter U+02BB
        "G'ofurov",
        "Müller",  # pure Latin with a non-ASCII Latin letter
    ],
)
def test_pure_script_name_never_changed(value):
    out, fixes = fix_mixed_script_names(f"Фамилияси {value}", I(lastName=value))
    assert compact(out) == {"lastName": value}
    assert fixes == []


def test_pure_latin_name_with_only_lookalike_letters_not_cyrillized():
    # "Oxa" maps entirely to Cyrillic look-alikes and "Оха" is in the query, but the value is pure Latin
    out, fixes = fix_mixed_script_names("Фамилияси Оха", I(lastName="Oxa"))
    assert compact(out) == {"lastName": "Oxa"}
    assert fixes == []


def test_pure_latin_diacritic_name_not_cyrillized():
    # "é" is a non-ASCII Latin letter, so "Océ" is pure Latin even though O and c are look-alikes.
    # The query holds the exact mapped form (Cyrillic О, с + Latin é), so only the purity rule protects the value.
    mapped = "Осé"
    out, fixes = fix_mixed_script_names(f"Фамилияси {mapped}", I(lastName="Océ"))
    assert compact(out) == {"lastName": "Océ"}
    assert fixes == []


@pytest.mark.parametrize(
    "query",
    [
        "Фамилияси Тошмуродов бўлган ходимлар",  # another name
        "xodimlar",
        "ФАМИЛИЯСИ ТОШМАТОВ",  # exact string only, no case folding
        "Фамилияси Toшматов",  # user typed the mixed form: the mapped form does not occur
    ],
)
def test_mixed_name_without_mapped_form_in_query_unchanged(query):
    out, fixes = fix_mixed_script_names(query, I(lastName="Toшматов"))
    assert compact(out) == {"lastName": "Toшматов"}
    assert fixes == []


def test_mixed_name_empty_intent():
    out, fixes = fix_mixed_script_names("Тошматов", I())
    assert compact(out) == {}
    assert fixes == []


def test_mixed_name_does_not_mutate_input():
    intent = I(lastName="Toшматов")
    fix_mixed_script_names("Тошматов", intent)
    assert intent.lastName == "Toшматов"


# --------------------------------------------------------------------------- apply_all, order

def test_apply_all_full_order():
    q = "Фамилияси Тошматов, 45 ёшгача, илмий унвонга эга ходимлар"
    intent = I(
        fromAge=45,
        toAge=45,
        experienceYear=5,
        sex="Erkak",
        dateOfReceipt="2020-01-01",
        academicTitle="Professor",
        lastName="Toшматов",
        specialists=["Iqtisodiyot", "Expert"],
        typeOfPosition=["Director"],
    )
    out, fixes = apply_all(q, intent)
    assert compact(out) == {
        "toAge": 45,
        "isAcademicTitle": True,
        "lastName": "Тошматов",
        "specialists": ["Iqtisodiyot"],
    }
    assert fixes == [
        "drop:fromAge",  # fix_age_direction
        "drop:experienceYear",  # drop_unstated
        "drop:sex",  # drop_unstated_sex
        "drop:dateOfReceipt",  # drop_unstated_receipt
        GENERIC,  # fix_generic_title
        NAME,  # fix_mixed_script_names
        "specialists->typeOfPosition",  # move_position_types
        "canonical:typeOfPosition",  # canonicalize_positions
        "drop:typeOfPosition",  # drop_unstated_positions
    ]


def test_apply_all_equal_bounds_spec_example():
    out, fixes = apply_all("Олий маълумотли, 45 ёшгача бўлган эркаклар",
                           I(education="Oliy", fromAge=45, toAge=45, sex="Erkak"))
    assert compact(out) == {"education": "Oliy", "toAge": 45, "sex": "Erkak"}
    assert fixes == ["drop:fromAge"]


def test_apply_all_generic_title_spec_examples():
    out, fixes = apply_all("Mukofotlangan, ilmiy unvonga ega xodimlar", I(academicTitle="Professor"))
    assert compact(out) == {"isAcademicTitle": True}
    assert fixes == [GENERIC]
    out, fixes = apply_all("Ilmiy unvoni bor professorlar", I(academicTitle="Professor"))
    assert compact(out) == {"academicTitle": "Professor"}
    assert fixes == []


def test_apply_all_clean_equal_and_names_unchanged():
    out, fixes = apply_all("25 yoshli Toshmatov", I(fromAge=25, toAge=25, lastName="Toshmatov"))
    assert compact(out) == {"fromAge": 25, "toAge": 25, "lastName": "Toshmatov"}
    assert fixes == []


def _metric(kind: str) -> float:
    return REGISTRY.get_sample_value("hr_ai_intent_corrections_total", {"kind": kind}) or 0.0


Q_ALL = "Фамилияси Тошматов бўлган, илмий унвонга эга, 45 ёшгача ходимлар"
RAW_ALL = '{"lastName":"Toшматов","academicTitle":"Professor","fromAge":45,"toAge":45}'


def test_parser_runs_new_guards_and_counts_them():
    kinds = ("drop:fromAge", GENERIC, NAME)
    before = {k: _metric(k) for k in kinds}
    outcome = IntentParser(FakeLLMService({Q_ALL: RAW_ALL})).parse(Q_ALL)
    assert compact(outcome.intent) == {"lastName": "Тошматов", "isAcademicTitle": True, "toAge": 45}
    for k in kinds:
        assert _metric(k) == before[k] + 1, k


def test_parser_counts_drop_toage():
    q = "45 yoshdan katta xodimlar"
    before = _metric("drop:toAge")
    outcome = IntentParser(FakeLLMService({q: '{"fromAge":45,"toAge":45}'})).parse(q)
    assert compact(outcome.intent) == {"fromAge": 45}
    assert _metric("drop:toAge") == before + 1


def test_parser_guard_logs_do_not_carry_the_person_name(caplog):
    caplog.set_level(logging.INFO)
    IntentParser(FakeLLMService({Q_ALL: RAW_ALL})).parse(Q_ALL)
    for rec in caplog.records:
        if rec.levelno >= logging.INFO:
            dump = rec.getMessage() + " " + " ".join(str(v) for v in vars(rec).values())
            assert "Тошматов" not in dump, rec.getMessage()
            assert "Toшматов" not in dump, rec.getMessage()
