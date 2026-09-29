"""Phase 8 hardening contract (unit level), written from the spec."""

import json

import httpx
import pytest
from pydantic import ValidationError

from app.models.ai_intent import ParsedSearchIntent
from app.models.references import ReferenceItem
from app.services.filter_resolver import FilterResolver
from app.services.llm_service import (
    LLMBusyError,
    ModelNotFoundError,
    QwenLLMService,
    detect_model_dir,
    verify_model_files,
)
from app.services.reference_provider import LocalJsonReferenceProvider
from app.services.search_client import HrSearchClient, HrSearchError


def _resolve(settings, references, intent: dict, **kw):
    return FilterResolver(settings).resolve(ParsedSearchIntent.model_validate(intent), references, **kw)


# ---------------------------------------------------------------- range validation (both removed)

@pytest.mark.parametrize("lo,hi,intent", [
    ("fromAge", "toAge", {"fromAge": 40, "toAge": 30}),
    ("fromYear", "toYear", {"fromYear": 2000, "toYear": 1990}),
])
def test_inverted_range_removes_both(settings, references, lo, hi, intent):
    r = _resolve(settings, references, intent)
    assert lo not in r.searchModel and hi not in r.searchModel
    assert not {lo, hi} & {f.field for f in r.recognizedFilters}
    reasons = {u.field: u.reason for u in r.unresolvedFilters}
    assert reasons.get(lo) == "validation" and reasons.get(hi) == "validation", reasons
    assert r.warnings
    assert r.readyToSearch is False


def test_inverted_range_keeps_other_filters(settings, references, ref_id):
    r = _resolve(settings, references, {"fromAge": 40, "toAge": 30, "sex": "Ayol"})
    assert r.searchModel.get("sexId") == ref_id("sexes", "Ayol")
    assert "fromAge" not in r.searchModel and "toAge" not in r.searchModel


@pytest.mark.parametrize("intent,field", [({"fromAge": 150}, "fromAge"), ({"toYear": 3000}, "toYear")])
def test_single_out_of_range_is_validation(settings, references, intent, field):
    r = _resolve(settings, references, intent)
    assert field not in r.searchModel
    assert any(u.field == field and u.reason == "validation" for u in r.unresolvedFilters)
    assert r.readyToSearch is False


def test_valid_equal_range_kept(settings, references):
    r = _resolve(settings, references, {"fromAge": 30, "toAge": 30})
    assert r.searchModel["fromAge"] == 30 and r.searchModel["toAge"] == 30
    assert r.readyToSearch is True


# ---------------------------------------------------------------- strict integer parsing

@pytest.mark.parametrize("value", [30, 30.0, "30", " 30 ", "30 "])
def test_int_fields_accept_exact_integers(value):
    assert ParsedSearchIntent.model_validate({"fromAge": value}).fromAge == 30


@pytest.mark.parametrize("value", ["30-40", "5+", "30 yosh", "30.5", "abc", "3 0", 30.5])
def test_int_fields_reject_non_integers(value):
    with pytest.raises(ValidationError):
        ParsedSearchIntent.model_validate({"experienceYear": value})


# ---------------------------------------------------------------- typeOfPosition "All"

@pytest.mark.parametrize("value", ["All", 0, "0"])
def test_type_of_position_all_is_ignored(settings, references, value):
    r = _resolve(settings, references, {"typeOfPosition": [value], "fromAge": 30})
    assert "typeOfPosition" not in r.searchModel
    assert r.warnings
    assert r.searchModel["fromAge"] == 30
    assert r.readyToSearch is True


def test_type_of_position_all_with_real_value(settings, references):
    r = _resolve(settings, references, {"typeOfPosition": ["All", "Leader"]})
    assert r.searchModel["typeOfPosition"] == [1]


# ---------------------------------------------------------------- term / date / ambiguity

def test_term_duplicating_structured_value_dropped(settings, references):
    r = _resolve(settings, references, {"lastName": "Karimov", "term": "Karimov"})
    assert "term" not in r.searchModel
    assert r.searchModel["lastName"] == "Karimov"
    assert r.warnings


def test_term_kept_when_distinct(settings, references):
    r = _resolve(settings, references, {"lastName": "Karimov", "term": "bosh mutaxassis"})
    assert r.searchModel["term"] == "bosh mutaxassis"


def test_date_of_receipt_valid(settings, references):
    r = _resolve(settings, references, {"dateOfReceipt": "2024-05-01"})
    assert r.searchModel["dateOfReceipt"] == "2024-05-01"
    assert r.readyToSearch is True


@pytest.mark.parametrize("value", ["2024-13-45", "not a date", "2024-02-30"])
def test_date_of_receipt_invalid(settings, references, value):
    r = _resolve(settings, references, {"dateOfReceipt": value})
    assert "dateOfReceipt" not in r.searchModel
    assert any(u.field == "dateOfReceipt" and u.reason == "validation" for u in r.unresolvedFilters)
    assert r.readyToSearch is False


def test_shared_alias_is_ambiguous(settings):
    refs = {"regions": [ReferenceItem(id=101, name="Alfa viloyati", aliases=["Umumiy"]),
                        ReferenceItem(id=102, name="Beta viloyati", aliases=["Umumiy"])]}
    r = _resolve(settings, refs, {"region": "Umumiy"})
    assert "regionId" not in r.searchModel
    assert r.readyToSearch is False
    u = next(u for u in r.unresolvedFilters if u.field == "regionId")
    assert u.reason == "ambiguous"
    assert {101, 102} <= {c.id for c in u.candidates}


# ---------------------------------------------------------------- LLM queue limit

def test_qwen_generate_busy_when_queue_full(settings_factory):
    svc = QwenLLMService(settings_factory(LLM_MAX_QUEUE=2))
    svc._model = object()  # pretend loaded; generate must refuse before touching the model
    svc._waiting = 2
    with pytest.raises(LLMBusyError):
        svc.generate([{"role": "user", "content": "x"}])
    assert svc._waiting == 2


def test_llm_max_queue_default():
    from app.core.config import Settings
    assert Settings(_env_file=None).LLM_MAX_QUEUE == 8


# ---------------------------------------------------------------- model files

def _fake_model_dir(root, shards_present):
    root.mkdir(parents=True, exist_ok=True)
    for f in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (root / f).write_text("{}", encoding="utf-8")
    weight_map = {"a": "model-00001-of-00002.safetensors", "b": "model-00002-of-00002.safetensors"}
    (root / "model.safetensors.index.json").write_text(json.dumps({"weight_map": weight_map}), encoding="utf-8")
    for s in shards_present:
        (root / s).write_bytes(b"")
    return root


def test_verify_model_files_missing_shard(tmp_path):
    d = _fake_model_dir(tmp_path / "m", ["model-00001-of-00002.safetensors"])
    with pytest.raises(ModelNotFoundError, match="model-00002-of-00002.safetensors"):
        verify_model_files(d)


def test_detect_model_dir_missing_shard(tmp_path, settings_factory):
    d = _fake_model_dir(tmp_path / "m", ["model-00001-of-00002.safetensors"])
    with pytest.raises(ModelNotFoundError, match="model-00002-of-00002.safetensors"):
        detect_model_dir(settings_factory(MODEL_PATH=str(d)))


def test_detect_model_dir_complete(tmp_path, settings_factory):
    d = _fake_model_dir(tmp_path / "m", ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"])
    assert detect_model_dir(settings_factory(MODEL_PATH=str(d))).resolve() == d.resolve()


# ---------------------------------------------------------------- local references robustness

@pytest.mark.parametrize("content", ["{not json", "[1, 2, 3]", '{"regions": "x"}', '{"regions": [{"foo": 1}]}'])
async def test_malformed_local_reference_file_returns_none(tmp_path, content):
    path = tmp_path / "refs.json"
    path.write_text(content, encoding="utf-8")
    assert await LocalJsonReferenceProvider(path).get("regions") is None


async def test_missing_local_reference_file_returns_none(tmp_path):
    assert await LocalJsonReferenceProvider(tmp_path / "nope.json").get("regions") is None


# ---------------------------------------------------------------- HR client result model

def _hr(settings_factory, payload, status=200):
    s = settings_factory(HR_SEARCH_API_URL="http://hr.test")
    return HrSearchClient(s, transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)))


async def test_hr_client_returns_search_results(settings_factory):
    res = await _hr(settings_factory, {"items": [{"id": 1, "x": {"y": 2}}], "totalItems": 5}).search({"fromAge": 30})
    assert res.items == [{"id": 1, "x": {"y": 2}}]
    assert res.totalItems == 5


@pytest.mark.parametrize("payload", [{"items": "x"}, {"items": [1, 2]}, [1, 2], "text"])
async def test_hr_client_malformed_body(settings_factory, payload):
    with pytest.raises(HrSearchError):
        await _hr(settings_factory, payload).search({"fromAge": 30})
