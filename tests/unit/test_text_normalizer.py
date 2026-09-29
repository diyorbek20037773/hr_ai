import pytest

from app.utils.text_normalizer import normalize_text


@pytest.mark.parametrize(
    "raw",
    ["O'zbek", "O`zbek", "O’zbek", "Oʻzbek", "Oʼzbek", "O‘zbek"],
)
def test_apostrophes_unified(raw):
    assert normalize_text(raw) == "o'zbek"


def test_trim_and_collapse_spaces():
    assert normalize_text("   Toshkent    \t  shahri  ") == "toshkent shahri"


def test_casefold_latin_and_cyrillic():
    assert normalize_text("TOSHKENT") == "toshkent"
    assert normalize_text("ТОШКЕНТ") == "тошкент"


def test_trailing_punctuation_stripped():
    assert normalize_text("Toshkent shahri.") == "toshkent shahri"
    assert normalize_text("Chilonzor,") == "chilonzor"


def test_no_transliteration():
    # Cyrillic stays Cyrillic: variants are handled by aliases, not by destructive transliteration.
    assert normalize_text("Тошкент") != normalize_text("Toshkent")


def test_empty_and_none():
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""
    assert normalize_text(None) == ""
