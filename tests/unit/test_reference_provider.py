import httpx
import pytest

from app.models.references import ReferenceItem
from app.services.reference_provider import (
    CompositeReferenceProvider,
    LocalJsonReferenceProvider,
    ReferenceService,
    RemoteReferenceProvider,
    RequestReferenceProvider,
)
from tests.conftest import EXAMPLE_REFERENCES_PATH

BASE_URL = "http://refs.test/api"


class RemoteStub:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        if request.url.path == "/api/regions":
            return httpx.Response(200, json=[{"id": 500, "name": "Remote region"}])
        if request.url.path == "/api/sexes":
            return httpx.Response(200, json={"items": [{"id": 600, "name": "Remote sex", "aliases": ["rs"]}]})
        return httpx.Response(404, json={"detail": "not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _remote(settings_factory, stub, **overrides):
    s = settings_factory(REFERENCE_API_BASE_URL=BASE_URL, **overrides)
    return RemoteReferenceProvider(s, client=stub.client())


def _composite(request_data, remote):
    return CompositeReferenceProvider(
        [RequestReferenceProvider(request_data), remote, LocalJsonReferenceProvider(EXAMPLE_REFERENCES_PATH)])


REQUEST_DATA = {"regions": [ReferenceItem(id=999, name="Request region")]}


async def test_request_beats_remote_and_local(settings_factory):
    stub = RemoteStub()
    items = await _composite(REQUEST_DATA, _remote(settings_factory, stub)).get("regions")
    assert [i.id for i in items] == [999]


async def test_remote_beats_local(settings_factory):
    stub = RemoteStub()
    comp = _composite(REQUEST_DATA, _remote(settings_factory, stub))
    regions_items = await comp.get("sexes")
    assert [i.id for i in regions_items] == [600]  # {"items": [...]} payload accepted
    assert regions_items[0].aliases == ["rs"]


async def test_remote_list_payload(settings_factory):
    stub = RemoteStub()
    items = await _remote(settings_factory, stub).get("regions")
    assert [i.id for i in items] == [500]
    assert stub.calls == ["/api/regions"]


async def test_remote_missing_collection_falls_back_to_local(settings_factory, ref_id):
    stub = RemoteStub()
    items = await _composite(None, _remote(settings_factory, stub)).get("educations")
    assert ref_id("educations", "Oliy") in [i.id for i in items]


async def test_remote_failure_falls_back_to_local(settings_factory, ref_id):
    stub = RemoteStub(fail=True)
    items = await _composite(None, _remote(settings_factory, stub)).get("regions")
    assert ref_id("regions", "Toshkent shahri") in [i.id for i in items]


async def test_remote_ttl_cache(settings_factory):
    stub = RemoteStub()
    remote = _remote(settings_factory, stub, REFERENCE_CACHE_TTL_SECONDS=3600)
    await remote.get("regions")
    await remote.get("regions")
    assert stub.calls == ["/api/regions"]


async def test_remote_disabled_when_no_base_url(settings):
    stub = RemoteStub()
    remote = RemoteReferenceProvider(settings, client=stub.client())
    assert await remote.get("regions") is None
    assert stub.calls == []


async def test_local_provider(ref_id):
    local = LocalJsonReferenceProvider(EXAMPLE_REFERENCES_PATH)
    items = await local.get("specialists")
    assert ref_id("specialists", "Iqtisodiyot") in [i.id for i in items]
    assert await local.get("noSuchCollection") is None


async def test_request_provider_missing_collection():
    assert await RequestReferenceProvider(REQUEST_DATA).get("sexes") is None
    assert await RequestReferenceProvider(None).get("regions") is None


async def test_composite_load(settings_factory):
    stub = RemoteStub()
    data = await _composite(REQUEST_DATA, _remote(settings_factory, stub)).load(["regions", "sexes", "educations"])
    assert [i.id for i in data["regions"]] == [999]
    assert [i.id for i in data["sexes"]] == [600]
    assert data["educations"]


@pytest.mark.parametrize("request_data", [None, {}])
async def test_reference_service_uses_local_without_remote(settings, ref_id, request_data):
    comp = ReferenceService(settings).for_request(request_data)
    items = await comp.get("regions")
    assert ref_id("regions", "Toshkent shahri") in [i.id for i in items]


async def test_reference_service_request_priority(settings):
    comp = ReferenceService(settings).for_request(REQUEST_DATA)
    assert [i.id for i in await comp.get("regions")] == [999]
