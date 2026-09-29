import pytest

from app.utils.json_parser import JsonExtractionError, extract_json_object


def test_plain_json():
    assert extract_json_object('{"sex": "Ayol", "fromAge": 30}') == {"sex": "Ayol", "fromAge": 30}


def test_fenced_json():
    raw = '```json\n{"education": "Oliy"}\n```'
    assert extract_json_object(raw) == {"education": "Oliy"}


def test_fenced_without_language():
    assert extract_json_object('```\n{"toAge": 40}\n```') == {"toAge": 40}


def test_prefixed_and_suffixed_text():
    raw = 'Here is the result: {"region": "Toshkent", "sex": "Erkak"} Hope this helps.'
    assert extract_json_object(raw) == {"region": "Toshkent", "sex": "Erkak"}


def test_nested_balanced_object():
    raw = 'Answer -> {"specialists": ["Moliya"], "x": {"y": 1}} end'
    assert extract_json_object(raw) == {"specialists": ["Moliya"], "x": {"y": 1}}


def test_trailing_commas():
    assert extract_json_object('{"fromAge": 30, "specialists": ["Moliya",],}') == {
        "fromAge": 30, "specialists": ["Moliya"]}


def test_single_quotes_repaired():
    assert extract_json_object("{'sex': 'Ayol'}") == {"sex": "Ayol"}


def test_empty_object():
    assert extract_json_object("{}") == {}


@pytest.mark.parametrize("raw", ["", "not json at all", "{ broken", "[1, 2, 3]", "null"])
def test_garbage_raises(raw):
    with pytest.raises(JsonExtractionError):
        extract_json_object(raw)
