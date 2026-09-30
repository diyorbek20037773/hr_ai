"""Spec tests for guards H (canonicalize_positions), I (drop_unstated_receipt) and the updated apply_all order."""

from __future__ import annotations

import pytest

from app.models.ai_intent import ParsedSearchIntent
from app.models.enums import TYPE_OF_POSITION_PHRASES, TypeOfPosition
from app.services.intent_guards import apply_all, canonicalize_positions, drop_unstated_receipt
from app.services.intent_parser import IntentParser
from app.services.llm_service import FakeLLMService


def I(**kw) -> ParsedSearchIntent:  # noqa: E743, N802
    return ParsedSearchIntent(**kw)


def compact(intent: ParsedSearchIntent) -> dict:
    return intent.to_compact_dict()


CANON = "canonical:typeOfPosition"

# --------------------------------------------------------------------------- H. canonicalize_positions


@pytest.mark.parametrize(
    "given,expected",
    [
        (["Leader", "Director"], ["Leader"]),
        (["rahbar", "Expert"], ["Leader", "Expert"]),
        (["director"], ["Leader"]),
        (["head"], ["Leader"]),
        (["1"], ["Leader"]),
        (["1", "Leader"], ["Leader"]),
        (["Expert", "rahbar", "Leader"], ["Expert", "Leader"]),
        (["руководитель", "специалист"], ["Leader", "Expert"]),
        (["texnik", "Technic"], ["Technic"]),
        (["Kosmonavt", "rahbar"], ["Kosmonavt", "Leader"]),
    ],
)
def test_canonicalize_rewrites_and_dedups(given, expected):
    out, fixes = canonicalize_positions(I(typeOfPosition=given, region="Toshkent"))
    assert out.typeOfPosition == expected
    assert out.region == "Toshkent"
    assert fixes == [CANON]


@pytest.mark.parametrize(
    "given",
    [["Leader"], ["Leader", "Expert"], ["Kosmonavt"], ["Leadr"], ["Expert", "Kosmonavt"]],
)
def test_canonicalize_unchanged_no_fix(given):
    out, fixes = canonicalize_positions(I(typeOfPosition=given))
    assert out.typeOfPosition == given
    assert fixes == []


def test_canonicalize_never_fuzzy():
    # "Leadr"/"Expertt" are typos: not parseable -> kept verbatim for the resolver to report
    out, fixes = canonicalize_positions(I(typeOfPosition=["Leadr", "Expertt"]))
    assert out.typeOfPosition == ["Leadr", "Expertt"]
    assert fixes == []


def test_canonicalize_every_phrase_maps_to_enum_name():
    for member, phrases in TYPE_OF_POSITION_PHRASES.items():
        for phrase in phrases:
            out, fixes = canonicalize_positions(I(typeOfPosition=[phrase]))
            assert out.typeOfPosition == [member.name], phrase
            assert fixes == [CANON], phrase


@pytest.mark.parametrize("member", list(TypeOfPosition))
def test_canonicalize_number_to_name(member):
    out, fixes = canonicalize_positions(I(typeOfPosition=[str(int(member))]))
    assert out.typeOfPosition == [member.name]
    assert fixes == [CANON]


def test_canonicalize_none_and_empty_intent():
    out, fixes = canonicalize_positions(I())
    assert compact(out) == {}
    assert fixes == []
    out, fixes = canonicalize_positions(I(sex="Ayol"))
    assert compact(out) == {"sex": "Ayol"}
    assert fixes == []


def test_canonicalize_does_not_mutate_input():
    intent = I(typeOfPosition=["rahbar", "Leader"])
    canonicalize_positions(intent)
    assert intent.typeOfPosition == ["rahbar", "Leader"]


def test_director_and_head_are_leader_phrases():
    phrases = TYPE_OF_POSITION_PHRASES[TypeOfPosition.Leader]
    assert "director" in phrases and "head" in phrases


# --------------------------------------------------------------------------- I. drop_unstated_receipt

RECEIPT = "2020-01-01"


@pytest.mark.parametrize(
    "query",
    [
        "Родившиеся в Бухарской области в 1985 году",
        "2020 yilda tug'ilganlar",
        "Toshkentdagi ayollar",
        "ишга ёш кадрлар",  # "ишга" alone is not "ишга кир/олин"
        "сотрудники 2020 года",
    ],
)
def test_receipt_dropped_without_evidence(query):
    out, fixes = drop_unstated_receipt(query, I(dateOfReceipt=RECEIPT, region="Buxoro"))
    assert compact(out) == {"region": "Buxoro"}
    assert fixes == ["drop:dateOfReceipt"]


def test_receipt_example_from_spec():
    out, fixes = drop_unstated_receipt("Родившиеся в Бухарской области в 1985 году", I(dateOfReceipt="1985-01-01"))
    assert out.dateOfReceipt is None
    assert fixes == ["drop:dateOfReceipt"]


@pytest.mark.parametrize(
    "query",
    [
        "2020 yilda ishga qabul qilinganlar",
        "2020 йилда ишга қабул қилинганлар",
        "QABUL qilingan xodimlar",
        "принятые на работу в 2020 году",
        "ПРИНЯТЫЕ в 2020",
        "дата приёма 2020",
        "дата приема 2020",
        "приём на работу",
        "2020 yilda ishga kirganlar",
        "2020 йилда ишга кирганлар",
        "2020 yilda ishga olinganlar",
        "2020 йилда ишга олинганлар",
    ],
)
def test_receipt_kept_with_evidence(query):
    out, fixes = drop_unstated_receipt(query, I(dateOfReceipt=RECEIPT))
    assert out.dateOfReceipt == RECEIPT
    assert fixes == []


def test_receipt_absent_unchanged():
    out, fixes = drop_unstated_receipt("Родившиеся в 1985 году", I(fromYear=1985))
    assert compact(out) == {"fromYear": 1985}
    assert fixes == []


def test_receipt_does_not_mutate_input():
    intent = I(dateOfReceipt=RECEIPT)
    drop_unstated_receipt("xodimlar", intent)
    assert intent.dateOfReceipt == RECEIPT


# --------------------------------------------------------------------------- F. updated apply_all order


def test_apply_all_receipt_after_sex_before_positions():
    q = "Toshkentdagi xodimlar"
    intent = I(region="Toshkent", education="Oliy", sex="Ayol", dateOfReceipt=RECEIPT, typeOfPosition=["Leader"])
    out, fixes = apply_all(q, intent)
    assert compact(out) == {"region": "Toshkent"}
    assert fixes == ["drop:education", "drop:sex", "drop:dateOfReceipt", "drop:typeOfPosition"]


def test_apply_all_move_then_canonical():
    q = "rahbarlar va mutaxassislar"
    out, fixes = apply_all(q, I(typeOfPosition=["rahbar"], specialists=["Iqtisodiyot", "Expert"]))
    assert out.typeOfPosition == ["Leader", "Expert"]
    assert out.specialists == ["Iqtisodiyot"]
    assert fixes == ["specialists->typeOfPosition", CANON]


def test_apply_all_canonical_then_drop_unstated_positions():
    out, fixes = apply_all("Toshkentdagi xodimlar", I(region="Toshkent", typeOfPosition=["Director"]))
    assert compact(out) == {"region": "Toshkent"}
    assert fixes == [CANON, "drop:typeOfPosition"]


@pytest.mark.parametrize("query", ["department director", "Director of finance", "heads of departments"])
def test_apply_all_leader_kept_with_english_evidence(query):
    out, fixes = apply_all(query, I(typeOfPosition=["Director"]))
    assert out.typeOfPosition == ["Leader"]
    assert fixes == [CANON]


def test_apply_all_receipt_kept_and_canonical_positions():
    q = "2020 yilda ishga qabul qilingan rahbarlar"
    out, fixes = apply_all(q, I(dateOfReceipt=RECEIPT, typeOfPosition=["rahbar", "Leader"]))
    assert compact(out) == {"dateOfReceipt": RECEIPT, "typeOfPosition": ["Leader"]}
    assert fixes == [CANON]


def test_apply_all_unparseable_position_left_for_resolver():
    # canonical keeps "Kosmonavt"; drop_unstated_positions only judges known enum members
    out, _ = apply_all("kosmonavtlar", I(typeOfPosition=["Kosmonavt"]))
    assert out.typeOfPosition == ["Kosmonavt"]


def test_parser_reports_receipt_and_canonical_corrections():
    q = "Родившиеся в Бухарской области в 1985 году, руководители"
    fake = FakeLLMService({q: '{"birthRegion":"Buxoro viloyati","fromYear":1985,"toYear":1985,'
                              '"dateOfReceipt":"1985-01-01","typeOfPosition":["руководитель"]}'})
    outcome = IntentParser(fake).parse(q)
    assert outcome.intent.dateOfReceipt is None
    assert outcome.intent.typeOfPosition == ["Leader"]
    assert "drop:dateOfReceipt" in outcome.corrections
    assert CANON in outcome.corrections
    assert outcome.corrections.index("drop:dateOfReceipt") < outcome.corrections.index(CANON)
