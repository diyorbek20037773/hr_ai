import pytest

from app.models.ai_intent import ParsedSearchIntent
from app.services.filter_resolver import NO_FILTERS_WARNING, FilterResolver

BASE = {"academicTitleId": 0, "first": 0, "rows": 20}


def _resolve(settings, references, intent: dict, **kw):
    return FilterResolver(settings).resolve(ParsedSearchIntent.model_validate(intent), references, **kw)


def _core_cases(ref_id):
    women_higher = {"educationId": ref_id("educations", "Oliy"), "fromAge": 30, "sexId": ref_id("sexes", "Ayol")}
    return [
        ({"education": "Oliy", "fromAge": 30, "sex": "Ayol"}, women_higher),
        ({"region": "Samarqand viloyati", "fromAge": 25, "toAge": 40, "typeOfService": "CivilServant"},
         {"regionId": ref_id("regions", "Samarqand viloyati"), "fromAge": 25, "toAge": 40, "typeOfService": 1}),
        ({"region": "Toshkent", "specialists": ["Iqtisodiyot"], "experienceYear": 3},
         {"regionId": ref_id("regions", "Toshkent shahri"), "specialistIds": [ref_id("specialists", "Iqtisodiyot")],
          "experienceYear": 3}),
        ({"isAcademicDegree": True, "isAwarded": True}, {"isAcademicDegree": True, "isAwarded": True}),
        ({"education": "Oliy", "fromAge": 30, "sex": "Ayol"}, women_higher),  # uz-Cyrillic query
        ({"education": "Oliy", "fromAge": 30, "sex": "Ayol"}, women_higher),  # Russian query
        ({"lastName": "Karimov", "firstName": "Ali"}, {"lastName": "Karimov", "firstName": "Ali"}),
        ({"district": "Chilonzor", "language": "Ingliz tili", "languageCertificate": "IELTS", "toAge": 35},
         {"districtId": ref_id("districts", "Chilonzor tumani"), "languageId": ref_id("languages", "Ingliz tili"),
          "languageCertificateId": ref_id("languageCertificates", "IELTS"), "toAge": 35}),
        ({"typeOfPosition": ["Leader"]}, {"typeOfPosition": [1]}),
    ]


@pytest.mark.parametrize("idx", range(9))
def test_core_intents_exact_search_model(settings, references, ref_id, idx):
    intent, expected = _core_cases(ref_id)[idx]
    r = _resolve(settings, references, intent)
    assert r.searchModel == {**BASE, **expected}
    assert r.readyToSearch is True
    assert r.unresolvedFilters == []


def test_empty_intent_ready_with_no_filters_warning(settings, references):
    r = _resolve(settings, references, {})
    assert r.searchModel == BASE
    assert r.readyToSearch is True
    assert NO_FILTERS_WARNING in r.warnings
    assert r.recognizedFilters == []


def test_first_rows_passed_through(settings, references):
    r = _resolve(settings, references, {"fromAge": 30}, first=40, rows=10)
    assert r.searchModel["first"] == 40 and r.searchModel["rows"] == 10


def test_academic_title_default_from_settings(settings_factory, references):
    r = _resolve(settings_factory(ACADEMIC_TITLE_ID_DEFAULT=7, DEFAULT_SEARCH_ROWS=50), references, {"fromAge": 30})
    assert r.searchModel["academicTitleId"] == 7
    assert r.searchModel["rows"] == 50


def test_academic_title_requested(settings, references, ref_id):
    r = _resolve(settings, references, {"academicTitle": "Professor"})
    assert r.searchModel["academicTitleId"] == ref_id("academicTitles", "Professor")


def test_single_specialist_goes_to_list(settings, references, ref_id):
    r = _resolve(settings, references, {"specialists": "Moliya"})
    assert r.searchModel["specialistIds"] == [ref_id("specialists", "Moliya")]


def test_multiple_positions_to_ints(settings, references):
    r = _resolve(settings, references, {"typeOfPosition": ["Leader", "Technic", "Expert"]})
    assert sorted(r.searchModel["typeOfPosition"]) == [1, 3, 4]


def test_search_model_is_camel_case_without_none(settings, references):
    r = _resolve(settings, references, {"sex": "Erkak"})
    assert all("_" not in k for k in r.searchModel)
    assert all(v is not None for v in r.searchModel.values())


def test_recognized_filters_have_labels(settings, references):
    r = _resolve(settings, references, {"region": "Toshkent", "fromAge": 30})
    fields = {f.field: f for f in r.recognizedFilters}
    assert {"regionId", "fromAge"} <= set(fields)
    assert fields["regionId"].label and fields["regionId"].labelRu
    assert fields["regionId"].matchType == "alias"
    assert fields["fromAge"].matchType == "direct"


def test_from_age_greater_than_to_age(settings, references):
    r = _resolve(settings, references, {"fromAge": 40, "toAge": 30})
    assert r.readyToSearch is False
    assert r.warnings


@pytest.mark.parametrize(
    "intent",
    [{"fromAge": 150}, {"toAge": 130}, {"fromAge": -1}, {"experienceYear": 70}, {"fromYear": 1800},
     {"fromYear": 2000, "toYear": 1990}, {"toYear": 3000}],
)
def test_out_of_range_values_block_search(settings, references, intent):
    r = _resolve(settings, references, intent)
    assert r.readyToSearch is False
    assert r.warnings or any(u.reason == "validation" for u in r.unresolvedFilters)


@pytest.mark.parametrize("intent", [{"pinfl": "123"}, {"pinfl": "1234567890123A"}, {"inn": 12345}])
def test_bad_identifiers_block_search(settings, references, intent):
    r = _resolve(settings, references, intent)
    assert r.readyToSearch is False
    assert r.warnings or any(u.reason == "validation" for u in r.unresolvedFilters)


def test_valid_identifiers(settings, references):
    r = _resolve(settings, references, {"pinfl": "01234567890123", "inn": 123456789})
    assert r.readyToSearch is True
    assert r.searchModel["pinfl"] == "01234567890123"
    assert r.searchModel["inn"] == 123456789


def test_typo_resolved_fuzzy(settings, references, ref_id):
    r = _resolve(settings, references, {"region": "Toshken"})
    assert r.searchModel["regionId"] == ref_id("regions", "Toshkent shahri")
    assert r.readyToSearch is True
    rec = next(f for f in r.recognizedFilters if f.field == "regionId")
    assert rec.matchType == "fuzzy"


def test_low_confidence_never_silently_resolved(settings, references, ref_id):
    # "Kiberxafsiz" scores ~85 against "Kiberxavfsizlik": candidate band, not auto-match.
    r = _resolve(settings, references, {"specialists": ["Kiberxafsiz"]})
    assert "specialistIds" not in r.searchModel
    assert r.readyToSearch is False
    u = next(u for u in r.unresolvedFilters if u.field in ("specialistIds", "specialistId", "specialists"))
    assert u.reason == "low_confidence"
    assert ref_id("specialists", "Kiberxavfsizlik") in [c.id for c in u.candidates]


def test_strict_threshold_turns_fuzzy_into_unresolved(settings_factory, references, ref_id):
    r = _resolve(settings_factory(FUZZY_AUTO_MATCH_THRESHOLD=99), references, {"region": "Toshken"})
    assert "regionId" not in r.searchModel
    assert r.readyToSearch is False
    u = next(u for u in r.unresolvedFilters if u.field == "regionId")
    assert u.reason == "low_confidence"
    assert ref_id("regions", "Toshkent shahri") in [c.id for c in u.candidates]


def test_no_match_unresolved(settings, references):
    r = _resolve(settings, references, {"region": "Xyzzy qwerty"})
    assert "regionId" not in r.searchModel
    assert r.readyToSearch is False
    u = next(u for u in r.unresolvedFilters if u.field == "regionId")
    assert u.reason == "no_match"
    assert u.candidates == []


def test_unknown_position_phrase_not_guessed(settings, references):
    r = _resolve(settings, references, {"typeOfPosition": ["kosmonavt"]})
    assert "typeOfPosition" not in r.searchModel
    assert r.readyToSearch is False


def test_missing_collection_is_unresolved(settings, references):
    refs = {k: v for k, v in references.items() if k != "regions"}
    r = _resolve(settings, refs, {"region": "Toshkent"})
    assert "regionId" not in r.searchModel
    assert r.readyToSearch is False


def test_required_collections():
    intent = ParsedSearchIntent.model_validate(
        {"region": "Toshkent", "sex": "Ayol", "specialists": ["Moliya"], "fromAge": 30})
    cols = set(FilterResolver.required_collections(intent))
    assert {"regions", "sexes", "specialists"} <= cols
    assert "educations" not in cols
