from app.models.references import ReferenceItem
from app.services.fuzzy_matcher import FuzzyMatcher


def _ids(candidates):
    return [c.id for c in candidates]


def test_exact_name_match(settings, references, ref_id):
    r = FuzzyMatcher(settings).match("toshkent SHAHRI", references["regions"])
    assert r.status == "resolved"
    assert r.match_type == "exact"
    assert r.item.id == ref_id("regions", "Toshkent shahri")


def test_exact_match_with_apostrophe_variant(settings, references, ref_id):
    r = FuzzyMatcher(settings).match("Farg‘ona viloyati", references["regions"])
    assert r.status == "resolved"
    assert r.match_type == "exact"
    assert r.item.id == ref_id("regions", "Farg'ona viloyati")


def test_alias_match_latin_and_cyrillic(settings, references, ref_id):
    m = FuzzyMatcher(settings)
    for value in ("Toshkent", "Ташкент", "Тошкент"):
        r = m.match(value, references["regions"])
        assert r.status == "resolved", value
        assert r.match_type == "alias", value
        assert r.item.id == ref_id("regions", "Toshkent shahri")


def test_typo_fuzzy_resolves(settings, references, ref_id):
    r = FuzzyMatcher(settings).match("Toshken", references["regions"])
    assert r.status == "resolved"
    assert r.match_type == "fuzzy"
    assert r.item.id == ref_id("regions", "Toshkent shahri")
    assert r.score >= 88


def test_low_confidence_band_unresolved_with_candidates():
    items = [ReferenceItem(id=8, name="Kiberxavfsizlik"), ReferenceItem(id=9, name="Transport")]
    r = FuzzyMatcher(None).match("Kiberxafsiz", items)  # score ~85: between 75 and 88
    assert r.status == "unresolved"
    assert r.reason == "low_confidence"
    assert r.item is None
    assert 8 in _ids(r.candidates)
    assert all(75 <= c.score < 88 for c in r.candidates)


def test_no_match_below_candidate_threshold(settings, references):
    r = FuzzyMatcher(settings).match("Xyzzy qwerty", references["regions"])
    assert r.status == "unresolved"
    assert r.reason == "no_match"
    assert r.item is None
    assert r.candidates == []


def test_ambiguous_two_close_candidates_never_picked(settings):
    items = [ReferenceItem(id=1, name="Iqtisodiyot A"), ReferenceItem(id=2, name="Iqtisodiyot B")]
    r = FuzzyMatcher(settings).match("Iqtisodiyot", items)
    assert r.status == "unresolved"
    assert r.reason == "ambiguous"
    assert r.item is None
    assert sorted(_ids(r.candidates)) == [1, 2]


def test_threshold_comes_from_settings(settings_factory, references, ref_id):
    strict = settings_factory(FUZZY_AUTO_MATCH_THRESHOLD=99)
    r = FuzzyMatcher(strict).match("Toshken", references["regions"])
    assert r.status == "unresolved"
    assert r.reason == "low_confidence"
    assert r.item is None
    assert ref_id("regions", "Toshkent shahri") in _ids(r.candidates)


def test_candidate_threshold_from_settings(settings_factory, references):
    s = settings_factory(FUZZY_AUTO_MATCH_THRESHOLD=99, FUZZY_CANDIDATE_THRESHOLD=98)
    r = FuzzyMatcher(s).match("Toshken", references["regions"])
    assert r.status == "unresolved"
    assert r.candidates == []


def test_candidates_capped(settings_factory):
    items = [ReferenceItem(id=i, name=f"Bo'lim {i}") for i in range(1, 8)]
    s = settings_factory(FUZZY_MAX_CANDIDATES=3)
    r = FuzzyMatcher(s).match("Bo'lim", items)
    assert r.status == "unresolved"
    assert len(r.candidates) <= 3


def test_empty_reference_list(settings):
    r = FuzzyMatcher(settings).match("Toshkent", [])
    assert r.status == "unresolved"
    assert r.reason == "no_reference_data"
