from datetime import date

from app.models.enums import TypeOfPosition, TypeOfService
from app.models.search import ISEARCHMODEL_FIELDS, SearchModel

# Exact field set of the TypeScript ISearchModel (spec §3).
TS_FIELDS = {
    "inn", "firstName", "lastName", "middleName", "regionId", "birthRegionId", "districtId", "sexId", "nationId",
    "languageId", "fromAge", "toAge", "educationId", "fromYear", "toYear", "specialistId", "specialistIds",
    "educationTypes", "eduTypeId", "degreeId", "academicDegreeId", "academicTitleId", "institutionInn",
    "institutionId", "institutionName", "institutionTypeId", "institutionGroupId", "typeOfPosition",
    "spCategoryOfPositionId", "isAwarded", "isReserved", "languageCertificateId", "isAcademicDegree",
    "isAcademicTitle", "isGlobalEducation", "term", "pinfl", "first", "rows", "experienceYear", "dateOfReceipt",
    "typeOfService",
}

FULL = {
    "inn": 123456789, "firstName": "Ali", "lastName": "Karimov", "middleName": "Valiyevich", "regionId": 14,
    "birthRegionId": 8, "districtId": 102, "sexId": 2, "nationId": 1, "languageId": 3, "fromAge": 25, "toAge": 40,
    "educationId": 1, "fromYear": 1980, "toYear": 2000, "specialistId": 1, "specialistIds": [1, 2],
    "educationTypes": 1, "eduTypeId": 1, "degreeId": 2, "academicDegreeId": 1, "academicTitleId": 2,
    "institutionInn": 987654321, "institutionId": 3, "institutionName": "Vazirlik", "institutionTypeId": [1],
    "institutionGroupId": [2], "typeOfPosition": [1, 3], "spCategoryOfPositionId": [4], "isAwarded": True,
    "isReserved": False, "languageCertificateId": 1, "isAcademicDegree": True, "isAcademicTitle": False,
    "isGlobalEducation": True, "term": "kiberxavfsizlik", "pinfl": "01234567890123", "first": 0, "rows": 20,
    "experienceYear": 5, "dateOfReceipt": "2024-01-15", "typeOfService": 1,
}


def test_isearchmodel_fields_constant_matches_ts():
    assert set(ISEARCHMODEL_FIELDS) == TS_FIELDS
    assert len(ISEARCHMODEL_FIELDS) == len(TS_FIELDS)


def test_full_round_trip_exact_field_set():
    out = SearchModel.model_validate(FULL).to_api_json()
    assert set(out) == TS_FIELDS
    back = SearchModel.model_validate(out).to_api_json()
    assert back == out


def test_values_serialised_per_contract():
    out = SearchModel.model_validate(FULL).to_api_json()
    assert out["pinfl"] == "01234567890123"  # string, leading zero kept
    assert out["typeOfPosition"] == [1, 3]
    assert all(type(v) is int for v in out["typeOfPosition"])
    assert out["typeOfService"] == 1 and type(out["typeOfService"]) is int
    assert out["dateOfReceipt"].startswith("2024-01-15")
    assert isinstance(out["dateOfReceipt"], str)
    assert out["specialistIds"] == [1, 2]
    assert out["educationTypes"] == 1  # single number despite plural name


def test_populate_by_snake_case_and_enums():
    m = SearchModel(
        first=0, rows=20, academic_title_id=0, region_id=14, type_of_position=[TypeOfPosition.Leader,
                                                                              TypeOfPosition.Technic],
        type_of_service=TypeOfService.CivilServant, date_of_receipt=date(2023, 5, 1), pinfl="00000000000001",
    )
    out = m.to_api_json()
    assert out["regionId"] == 14
    assert out["typeOfPosition"] == [1, 3]
    assert out["typeOfService"] == 1
    assert out["dateOfReceipt"].startswith("2023-05-01")
    assert out["pinfl"] == "00000000000001"


def test_exclude_none_and_camel_case_only():
    out = SearchModel.model_validate({"first": 0, "rows": 20, "academicTitleId": 0, "sexId": 2}).to_api_json()
    assert out == {"first": 0, "rows": 20, "academicTitleId": 0, "sexId": 2}
    assert all("_" not in k for k in out)


def test_academic_title_id_always_present():
    out = SearchModel.model_validate({"first": 0, "rows": 20, "academicTitleId": 0}).to_api_json()
    assert out["academicTitleId"] == 0
