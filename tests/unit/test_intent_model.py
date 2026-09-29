import pytest

from app.models.ai_intent import ParsedSearchIntent


def test_core_example_parses():
    i = ParsedSearchIntent.model_validate({"education": "Oliy", "fromAge": 30, "sex": "Ayol"})
    assert i.to_compact_dict() == {"education": "Oliy", "fromAge": 30, "sex": "Ayol"}


def test_extra_keys_ignored():
    i = ParsedSearchIntent.model_validate({"sex": "Ayol", "regionId": 14, "salary": 1000})
    assert i.to_compact_dict() == {"sex": "Ayol"}


def test_numeric_strings_coerced():
    i = ParsedSearchIntent.model_validate({"fromAge": "30", "toAge": "40", "experienceYear": "5"})
    d = i.to_compact_dict()
    assert d["fromAge"] == 30 and d["toAge"] == 40 and d["experienceYear"] == 5


@pytest.mark.parametrize("value", ["true", "ha", "да", True])
def test_true_booleans(value):
    assert ParsedSearchIntent.model_validate({"isAwarded": value}).to_compact_dict() == {"isAwarded": True}


@pytest.mark.parametrize("value", ["false", False])
def test_false_booleans(value):
    assert ParsedSearchIntent.model_validate({"isReserved": value}).to_compact_dict() == {"isReserved": False}


def test_single_value_becomes_list():
    d = ParsedSearchIntent.model_validate({"specialists": "Iqtisodiyot", "typeOfPosition": "Leader"}).to_compact_dict()
    assert d["specialists"] == ["Iqtisodiyot"]
    assert d["typeOfPosition"] == ["Leader"]


def test_empty_intent():
    i = ParsedSearchIntent.model_validate({})
    assert i.is_empty()
    assert i.to_compact_dict() == {}


def test_non_empty_is_not_empty():
    assert not ParsedSearchIntent.model_validate({"lastName": "Karimov"}).is_empty()


def test_compact_dict_excludes_none():
    d = ParsedSearchIntent.model_validate({"lastName": "Karimov", "firstName": None}).to_compact_dict()
    assert d == {"lastName": "Karimov"}


def test_names_preserved_exactly():
    d = ParsedSearchIntent.model_validate({"lastName": "Karimov", "firstName": "Ali"}).to_compact_dict()
    assert d == {"lastName": "Karimov", "firstName": "Ali"}


def test_pinfl_kept_as_string():
    d = ParsedSearchIntent.model_validate({"pinfl": "01234567890123"}).to_compact_dict()
    assert d["pinfl"] == "01234567890123"
