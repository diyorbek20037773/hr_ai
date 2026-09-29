"""Reference (dictionary) data shapes and the field -> collection mapping."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

COLLECTION_KEYS: tuple[str, ...] = (
    "regions", "districts", "sexes", "nations", "languages", "educations", "specialists", "eduTypes",
    "degrees", "academicDegrees", "academicTitles", "institutions", "institutionTypes",
    "institutionGroups", "positionCategories", "languageCertificates",
)


class ReferenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)


ReferenceData = dict[str, list[ReferenceItem]]


class DictionaryField(BaseModel):
    """How one intent key maps to a SearchModel attribute through a reference collection."""

    intent_key: str
    model_attr: str  # snake_case SearchModel attribute
    api_field: str  # camelCase ISearchModel field (used in recognized/unresolved filters)
    collection: str
    is_list: bool = False


# intent key -> resolution rule. Order defines the order of recognizedFilters.
DICTIONARY_FIELDS: tuple[DictionaryField, ...] = (
    DictionaryField(intent_key="region", model_attr="region_id", api_field="regionId", collection="regions"),
    DictionaryField(intent_key="birthRegion", model_attr="birth_region_id", api_field="birthRegionId",
                    collection="regions"),
    DictionaryField(intent_key="district", model_attr="district_id", api_field="districtId",
                    collection="districts"),
    DictionaryField(intent_key="sex", model_attr="sex_id", api_field="sexId", collection="sexes"),
    DictionaryField(intent_key="nation", model_attr="nation_id", api_field="nationId", collection="nations"),
    DictionaryField(intent_key="language", model_attr="language_id", api_field="languageId",
                    collection="languages"),
    DictionaryField(intent_key="education", model_attr="education_id", api_field="educationId",
                    collection="educations"),
    DictionaryField(intent_key="specialists", model_attr="specialist_ids", api_field="specialistIds",
                    collection="specialists", is_list=True),
    DictionaryField(intent_key="eduType", model_attr="edu_type_id", api_field="eduTypeId",
                    collection="eduTypes"),
    DictionaryField(intent_key="degree", model_attr="degree_id", api_field="degreeId", collection="degrees"),
    DictionaryField(intent_key="academicDegree", model_attr="academic_degree_id", api_field="academicDegreeId",
                    collection="academicDegrees"),
    DictionaryField(intent_key="academicTitle", model_attr="academic_title_id", api_field="academicTitleId",
                    collection="academicTitles"),
    DictionaryField(intent_key="institution", model_attr="institution_id", api_field="institutionId",
                    collection="institutions"),
    DictionaryField(intent_key="institutionTypes", model_attr="institution_type_id",
                    api_field="institutionTypeId", collection="institutionTypes", is_list=True),
    DictionaryField(intent_key="institutionGroups", model_attr="institution_group_id",
                    api_field="institutionGroupId", collection="institutionGroups", is_list=True),
    DictionaryField(intent_key="positionCategories", model_attr="sp_category_of_position_id",
                    api_field="spCategoryOfPositionId", collection="positionCategories", is_list=True),
    DictionaryField(intent_key="languageCertificate", model_attr="language_certificate_id",
                    api_field="languageCertificateId", collection="languageCertificates"),
)
