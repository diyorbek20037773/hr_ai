"""`SearchModel` — the Python twin of the frontend `ISearchModel` (shared contract, CLAUDE.md §3.11).

Python attributes are snake_case; JSON aliases are generated camelCase and must equal the TypeScript field
names exactly. Outgoing JSON excludes `None` fields.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.models.enums import TypeOfPosition, TypeOfService

# Exact field set of the TypeScript ISearchModel (used by tests and contract checks).
ISEARCHMODEL_FIELDS: tuple[str, ...] = (
    "inn", "firstName", "lastName", "middleName", "regionId", "birthRegionId", "districtId", "sexId",
    "nationId", "languageId", "fromAge", "toAge", "educationId", "fromYear", "toYear", "specialistId",
    "specialistIds", "educationTypes", "eduTypeId", "degreeId", "academicDegreeId", "academicTitleId",
    "institutionInn", "institutionId", "institutionName", "institutionTypeId", "institutionGroupId",
    "typeOfPosition", "spCategoryOfPositionId", "isAwarded", "isReserved", "languageCertificateId",
    "isAcademicDegree", "isAcademicTitle", "isGlobalEducation", "term", "pinfl", "first", "rows",
    "experienceYear", "dateOfReceipt", "typeOfService",
)


class SearchModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        use_enum_values=False,
        extra="forbid",
    )

    inn: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    region_id: int | None = None
    birth_region_id: int | None = None
    district_id: int | None = None
    sex_id: int | None = None
    nation_id: int | None = None
    language_id: int | None = None
    from_age: int | None = None
    to_age: int | None = None
    education_id: int | None = None
    from_year: int | None = None
    to_year: int | None = None
    specialist_id: int | None = None
    specialist_ids: list[int] | None = None
    education_types: int | None = None  # single number despite the plural name (as in ISearchModel)
    edu_type_id: int | None = None
    degree_id: int | None = None
    academic_degree_id: int | None = None
    academic_title_id: int = 0  # required upstream; ACADEMIC_TITLE_ID_DEFAULT when not requested
    institution_inn: int | None = None
    institution_id: int | None = None
    institution_name: str | None = None
    institution_type_id: list[int] | None = None
    institution_group_id: list[int] | None = None
    type_of_position: list[TypeOfPosition] | None = None
    sp_category_of_position_id: list[int] | None = None
    is_awarded: bool | None = None
    is_reserved: bool | None = None
    language_certificate_id: int | None = None
    is_academic_degree: bool | None = None
    is_academic_title: bool | None = None
    is_global_education: bool | None = None
    term: str | None = None
    pinfl: str | None = None  # string: leading zeros matter
    first: int = Field(default=0, ge=0)
    rows: int = Field(default=20, ge=1)
    experience_year: int | None = None
    date_of_receipt: date | datetime | None = None  # ISO-8601 string on the wire
    type_of_service: TypeOfService | None = None

    def to_api_json(self) -> dict:
        """JSON-ready dict with camelCase keys, None fields removed, enums as ints, dates as ISO strings."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")
