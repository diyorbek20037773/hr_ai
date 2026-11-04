"""The resumable model download, written from the downloader spec.

The transfers are real as far as HTTP goes: `tests.unit.fetch_fakes.Hub` cuts bodies to the requested byte range behind
an `httpx.MockTransport`, and an interrupted transfer is a response stream that raises part-way. Nothing here touches
the network (the autouse guard fails any test that tries), and the only other things replaced are `list_files`,
`time.sleep` and, for the two full-disk tests, the file object that receives the bytes.
"""

from __future__ import annotations

import errno
import hashlib
import random
import shutil
from collections import namedtuple
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from huggingface_hub import hf_api

from app.scripts import fetch_model
from tests.unit.fetch_fakes import CDN_HOST, Hub, forbid_network, serve

CHUNK = fetch_model.CHUNK_BYTES
SHARD = "model-00001-of-00002.safetensors"
URL = f"https://hub.test/org/repo/resolve/rev/{SHARD}"
REVISION = "a" * 40
DiskUsage = namedtuple("DiskUsage", "total used free")
PAYLOAD = random.Random(20260704).randbytes(3 * CHUNK + 123_456)
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(fetch_model.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def full_disk(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Writes to any `.part` file fail with ENOSPC once `room` bytes have gone in."""

    def install(room: int) -> None:
        real_open = Path.open

        class Limited:
            def __init__(self, handle) -> None:
                self.handle = handle
                self.left = room

            def __enter__(self) -> Limited:
                return self

            def __exit__(self, *_exc: object) -> None:
                self.handle.close()

            def __getattr__(self, name: str):
                return getattr(self.handle, name)

            def write(self, data: bytes) -> int:
                if len(data) > self.left:
                    raise OSError(errno.ENOSPC, "No space left on device")
                self.left -= len(data)
                return self.handle.write(data)

        def limited_open(path: Path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            return Limited(handle) if path.name.endswith(".part") else handle

        monkeypatch.setattr(Path, "open", limited_open)

    return install


def client_for(hub: Hub) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(hub), follow_redirects=True)


def fetch(hub: Hub, dest: Path, size: int = len(PAYLOAD), sha256: str | None = PAYLOAD_SHA) -> None:
    with client_for(hub) as client:
        fetch_model.fetch_file(client, URL, dest, size, sha256)


def part_of(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def assert_installed(dest: Path, body: bytes = PAYLOAD) -> None:
    assert dest.read_bytes() == body
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == hashlib.sha256(body).hexdigest()
    assert not part_of(dest).exists()


def test_guard_refuses_a_real_httpx_call() -> None:
    with pytest.raises(pytest.fail.Exception, match="real network"):
        httpx.get("https://huggingface.co/")


def test_guard_stops_a_download_whose_listing_was_not_replaced(tmp_path: Path, sleeps: list[float]) -> None:
    with pytest.raises(pytest.fail.Exception, match="real network"):
        fetch_model.download(tmp_path, "org/repo", REVISION)
    assert sleeps == []


# --------------------------------------------------------------------------- list_files


def sibling(name: str, size: int, sha256: str | None = None) -> hf_api.RepoSibling:
    lfs = hf_api.BlobLfsInfo(size=size, sha256=sha256, pointer_size=134) if sha256 else None
    return hf_api.RepoSibling(rfilename=name, size=size, lfs=lfs)


def fake_api(monkeypatch: pytest.MonkeyPatch, siblings: list[hf_api.RepoSibling]) -> list[tuple]:
    calls: list[tuple] = []

    class FakeApi:
        def model_info(self, *args, **kwargs):
            calls.append((args, kwargs))
            return type("Info", (), {"siblings": siblings})()

    monkeypatch.setattr("huggingface_hub.HfApi", FakeApi)
    return calls


def test_list_files_asks_for_the_revision_with_file_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_api(monkeypatch, [])
    fetch_model.list_files("org/repo", REVISION)
    assert calls == [(("org/repo",), {"revision": REVISION, "files_metadata": True})]


def test_list_files_returns_name_size_and_the_lfs_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_api(monkeypatch, [sibling(SHARD, 4_900_000_000, "ab" * 32), sibling("config.json", 700)])
    assert fetch_model.list_files("org/repo", REVISION) == [
        (SHARD, 4_900_000_000, "ab" * 32),
        ("config.json", 700, None),
    ]


@pytest.mark.parametrize(
    ("name", "wanted"),
    [
        ("model-00001-of-00003.safetensors", True),
        ("model.safetensors.index.json", True),
        ("tokenizer.json", True),
        ("merges.txt", True),
        ("LICENSE", True),
        ("README.md", True),
        (".gitattributes", False),
        ("assets/logo.png", False),
        ("model.onnx", False),
        ("pytorch_model.bin", False),
    ],
)
def test_list_files_is_filtered_by_the_allow_patterns(
    monkeypatch: pytest.MonkeyPatch, name: str, wanted: bool
) -> None:
    fake_api(monkeypatch, [sibling(name, 1)])
    assert bool(fetch_model.list_files("org/repo", REVISION)) is wanted


# --------------------------------------------------------------------------- fetch_file: the plain paths


def test_a_file_of_the_right_size_is_not_requested(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    dest.write_bytes(b"\0" * len(PAYLOAD))
    hub = Hub({SHARD: PAYLOAD})
    fetch(hub, dest)
    assert hub.requests == []


def test_a_fresh_file_starts_at_byte_zero_and_lands_verified(tmp_path: Path) -> None:
    hub = Hub({SHARD: PAYLOAD})
    fetch(hub, tmp_path / SHARD)
    assert hub.ranges == ["bytes=0-"]
    assert_installed(tmp_path / SHARD)


def test_a_file_of_the_wrong_size_is_replaced(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    dest.write_bytes(b"stale")
    fetch(Hub({SHARD: PAYLOAD}), dest)
    assert_installed(dest)


def test_a_file_without_a_hash_is_checked_on_size_only(tmp_path: Path) -> None:
    fetch(Hub({SHARD: b"{}"}), tmp_path / SHARD, size=2, sha256=None)
    assert_installed(tmp_path / SHARD, b"{}")


def test_an_empty_file_is_created(tmp_path: Path) -> None:
    """A size of 0 means nothing to transfer, not a failure that retries until the download gives up."""
    fetch(Hub({SHARD: b""}), tmp_path / SHARD, size=0, sha256=None)
    assert_installed(tmp_path / SHARD, b"")


# --------------------------------------------------------------------------- fetch_file: resuming


@pytest.mark.parametrize("existing", [1, CHUNK - 1, CHUNK, 2 * CHUNK + 7, len(PAYLOAD) - 1])
def test_a_part_file_continues_with_a_range_and_a_206(tmp_path: Path, existing: int) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(PAYLOAD[:existing])
    hub = Hub({SHARD: PAYLOAD})
    fetch(hub, dest)
    assert hub.ranges == [f"bytes={existing}-"]
    assert_installed(dest)


def test_a_server_that_ignores_the_range_restarts_the_file_from_byte_zero(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(PAYLOAD[:CHUNK])
    hub = Hub({SHARD: PAYLOAD}, honour_range=False)
    fetch(hub, dest)
    assert hub.ranges == [f"bytes={CHUNK}-"]
    assert_installed(dest)


def test_a_part_file_bigger_than_the_file_is_discarded(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(b"\xff" * (len(PAYLOAD) + 1))
    hub = Hub({SHARD: PAYLOAD})
    fetch(hub, dest)
    assert hub.ranges == ["bytes=0-"]
    assert_installed(dest)


def test_a_finished_part_file_is_verified_and_moved_into_place(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(PAYLOAD)
    fetch(Hub({SHARD: PAYLOAD}), dest)
    assert_installed(dest)


@pytest.mark.parametrize("cut", [CHUNK, 2 * CHUNK, 3 * CHUNK])
def test_an_interrupted_transfer_resumes_at_the_byte_it_stopped_at(tmp_path: Path, cut: int) -> None:
    dest = tmp_path / SHARD
    hub = Hub({SHARD: PAYLOAD}, cuts=[cut])

    with pytest.raises(httpx.TransportError):
        fetch(hub, dest)
    assert part_of(dest).read_bytes() == PAYLOAD[:cut]
    assert not dest.exists()

    fetch(hub, dest)
    assert hub.ranges == ["bytes=0-", f"bytes={cut}-"]
    assert_installed(dest)


@pytest.mark.parametrize(
    "cut",
    [1, 500_000, CHUNK - 1, CHUNK + 1, 2 * CHUNK + 777],
    ids=["1-byte", "half-a-chunk", "just-under-a-chunk", "just-over-a-chunk", "inside-the-third-chunk"],
)
def test_every_byte_received_before_a_cut_is_kept(tmp_path: Path, cut: int) -> None:
    """A line that dies every few hundred KB must still move forward, or the 8 GB model never finishes."""
    dest = tmp_path / SHARD
    hub = Hub({SHARD: PAYLOAD}, cuts=[cut])

    with pytest.raises(httpx.TransportError):
        fetch(hub, dest)
    assert part_of(dest).read_bytes() == PAYLOAD[:cut]

    fetch(hub, dest)
    assert hub.ranges == ["bytes=0-", f"bytes={cut}-"]
    assert_installed(dest)


def test_repeated_interruptions_each_continue_from_the_last_byte(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    hub = Hub({SHARD: PAYLOAD}, cuts=[CHUNK, CHUNK, CHUNK])
    for _ in range(3):
        with pytest.raises(httpx.TransportError):
            fetch(hub, dest)
    fetch(hub, dest)
    assert hub.ranges == ["bytes=0-", f"bytes={CHUNK}-", f"bytes={2 * CHUNK}-", f"bytes={3 * CHUNK}-"]
    assert_installed(dest)


def test_a_cut_after_a_resume_keeps_the_earlier_bytes(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(PAYLOAD[:CHUNK])
    hub = Hub({SHARD: PAYLOAD}, cuts=[CHUNK])
    with pytest.raises(httpx.TransportError):
        fetch(hub, dest)
    assert part_of(dest).read_bytes() == PAYLOAD[:2 * CHUNK]


def test_an_interruption_leaves_an_existing_file_of_the_wrong_size_alone(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    dest.write_bytes(b"stale")
    with pytest.raises(httpx.TransportError):
        fetch(Hub({SHARD: PAYLOAD}, cuts=[CHUNK]), dest)
    assert dest.read_bytes() == b"stale"


def test_a_redirect_to_the_cdn_keeps_the_range(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(PAYLOAD[:CHUNK])
    hub = Hub({SHARD: PAYLOAD}, redirect=True)
    fetch(hub, dest)
    assert hub.ranges == [f"bytes={CHUNK}-"]
    assert hub.served[0].url.host == CDN_HOST
    assert_installed(dest)


# --------------------------------------------------------------------------- fetch_file: verification


def test_a_short_file_raises_and_a_later_run_continues(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    hub = Hub({SHARD: PAYLOAD[:2 * CHUNK]})
    with pytest.raises(OSError, match=SHARD):
        fetch(hub, dest)
    assert part_of(dest).read_bytes() == PAYLOAD[:2 * CHUNK]
    assert not dest.exists()

    hub.files[SHARD] = PAYLOAD
    fetch(hub, dest)
    assert hub.ranges == ["bytes=0-", f"bytes={2 * CHUNK}-"]
    assert_installed(dest)


def test_a_file_longer_than_listed_raises_and_keeps_the_part(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    with pytest.raises(OSError, match=SHARD):
        fetch(Hub({SHARD: PAYLOAD + b"tail"}), dest)
    assert part_of(dest).stat().st_size == len(PAYLOAD) + 4
    assert not dest.exists()


def test_a_wrong_hash_deletes_the_part_and_raises(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    with pytest.raises(OSError, match=SHARD):
        fetch(Hub({SHARD: PAYLOAD}), dest, sha256="0" * 64)
    assert not part_of(dest).exists()
    assert not dest.exists()


def test_a_finished_part_with_a_wrong_hash_is_deleted_too(tmp_path: Path) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(b"\xff" * len(PAYLOAD))
    hub = Hub({SHARD: PAYLOAD})
    with pytest.raises(OSError, match=SHARD):
        fetch(hub, dest)
    assert not part_of(dest).exists()
    assert not dest.exists()


def test_a_resumed_file_is_hashed_as_a_whole(tmp_path: Path) -> None:
    """The bytes from before the interruption are not trusted just because the new ones are right."""
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(b"\xff" * CHUNK)
    with pytest.raises(OSError, match=SHARD):
        fetch(Hub({SHARD: PAYLOAD}), dest)
    assert not part_of(dest).exists()
    assert not dest.exists()


@pytest.mark.parametrize("status", [401, 403, 404, 416, 500, 503])
def test_an_http_error_raises_and_keeps_the_part_as_it_was(tmp_path: Path, status: int) -> None:
    dest = tmp_path / SHARD
    part_of(dest).write_bytes(PAYLOAD[:CHUNK])
    with pytest.raises(httpx.HTTPStatusError):
        fetch(Hub({SHARD: PAYLOAD}, status=status), dest)
    assert part_of(dest).read_bytes() == PAYLOAD[:CHUNK]
    assert not dest.exists()


# --------------------------------------------------------------------------- bytes_on_disk


def test_bytes_on_disk_of_an_empty_directory(tmp_path: Path) -> None:
    assert fetch_model.bytes_on_disk(tmp_path) == 0


def test_bytes_on_disk_sums_weights_and_part_files_only(tmp_path: Path) -> None:
    (tmp_path / "a.safetensors").write_bytes(b"\0" * 1000)
    (tmp_path / "b.safetensors.part").write_bytes(b"\0" * 200)
    (tmp_path / "tokenizer.json.part").write_bytes(b"\0" * 30)
    (tmp_path / "config.json").write_bytes(b"\0" * 4000)
    assert fetch_model.bytes_on_disk(tmp_path) == 1230


# --------------------------------------------------------------------------- download: URL and credentials


def test_download_defaults_to_the_public_hub_and_sends_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = serve(monkeypatch, Hub({"config.json": b"{}"}))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert [str(r.url) for r in hub.requests] == [f"https://huggingface.co/org/repo/resolve/{REVISION}/config.json"]
    assert "Authorization" not in hub.requests[0].headers


@pytest.mark.parametrize("endpoint", ["https://mirror.test", "https://mirror.test/"])
def test_download_honours_hf_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str) -> None:
    monkeypatch.setenv("HF_ENDPOINT", endpoint)
    hub = serve(monkeypatch, Hub({"config.json": b"{}"}))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert [str(r.url) for r in hub.requests] == [f"https://mirror.test/org/repo/resolve/{REVISION}/config.json"]


def test_download_sends_hf_token_as_a_bearer_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_test_value")
    hub = serve(monkeypatch, Hub({"config.json": b"{}"}))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert hub.requests[0].headers["Authorization"] == "Bearer hf_test_value"


def test_download_sends_no_header_for_an_empty_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """docker-compose forwards HF_TOKEN as ${HF_TOKEN:-}, so unset arrives here as the empty string."""
    monkeypatch.setenv("HF_TOKEN", "")
    hub = serve(monkeypatch, Hub({"config.json": b"{}"}))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert "Authorization" not in hub.requests[0].headers


def test_the_token_does_not_follow_a_redirect_to_the_cdn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_test_value")
    hub = serve(monkeypatch, Hub({"config.json": b"{}"}, redirect=True))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert hub.requests[0].headers["Authorization"] == "Bearer hf_test_value"
    assert hub.served[0].url.host == CDN_HOST
    assert "Authorization" not in hub.served[0].headers
    assert (tmp_path / "config.json").read_bytes() == b"{}"


def test_download_creates_the_directory_of_a_nested_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, Hub({"config.json": b"{}"}, listed_sizes={"config.json": 2}))
    monkeypatch.setattr(fetch_model, "list_files", lambda *_args: [("sub/dir/config.json", 2, None)])
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert (tmp_path / "sub" / "dir" / "config.json").read_bytes() == b"{}"


# --------------------------------------------------------------------------- download: retries and resume


def test_a_second_download_call_resumes_with_an_exact_verified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, cuts=[2 * CHUNK], listed_hashes={SHARD: PAYLOAD_SHA}))
    with pytest.raises(httpx.TransportError):
        fetch_model.download(tmp_path, "org/repo", REVISION, attempts=1)
    assert part_of(tmp_path / SHARD).stat().st_size == 2 * CHUNK

    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert hub.ranges == ["bytes=0-", f"bytes={2 * CHUNK}-"]
    assert_installed(tmp_path / SHARD)


def test_a_retry_inside_one_download_call_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, cuts=[CHUNK], listed_hashes={SHARD: PAYLOAD_SHA}))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert hub.ranges == ["bytes=0-", f"bytes={CHUNK}-"]
    assert len(sleeps) == 1
    assert_installed(tmp_path / SHARD)


def test_a_finished_file_is_not_requested_again_on_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub({"config.json": b"{}", SHARD: PAYLOAD}, cuts=[None, CHUNK]))
    fetch_model.download(tmp_path, "org/repo", REVISION)
    names = [r.url.path.rsplit("/", 1)[-1] for r in hub.requests]
    assert names == ["config.json", SHARD, SHARD]
    assert_installed(tmp_path / SHARD)


def test_failures_that_make_progress_never_use_up_the_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """Three cuts in a row with attempts=2: each one brought new bytes, so the download keeps going."""
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, cuts=[CHUNK, CHUNK, CHUNK], listed_hashes={SHARD: PAYLOAD_SHA}))
    fetch_model.download(tmp_path, "org/repo", REVISION, attempts=2)
    assert len(hub.requests) == 4
    assert len(sleeps) == 3
    assert len(set(sleeps)) == 1, "the counter restarts at 1, so the backoff does too"
    assert_installed(tmp_path / SHARD)


def test_failures_without_progress_reach_the_limit_and_reraise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, cuts=[0] * 9))
    with pytest.raises(httpx.ReadError):
        fetch_model.download(tmp_path, "org/repo", REVISION, attempts=3)
    assert len(hub.requests) == 3
    assert len(sleeps) == 2, "no pause after the last attempt"


def test_progress_resets_the_count_of_failures_without_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """With attempts=3 the counter runs 1, 2, 1, 2, 1, 2: never 3."""
    cuts = [0, 0, CHUNK, 0, CHUNK, 0]
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, cuts=cuts, listed_hashes={SHARD: PAYLOAD_SHA}))
    fetch_model.download(tmp_path, "org/repo", REVISION, attempts=3)
    assert len(hub.requests) == 7
    assert_installed(tmp_path / SHARD)


def test_a_reset_counter_still_stops_at_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A failure with progress sets the counter to 1, so two more without progress reach attempts=3."""
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, cuts=[CHUNK, 0, 0, None]))
    with pytest.raises(httpx.ReadError):
        fetch_model.download(tmp_path, "org/repo", REVISION, attempts=3)
    assert len(hub.requests) == 3
    assert part_of(tmp_path / SHARD).stat().st_size == CHUNK


def test_a_file_finished_before_a_failure_counts_as_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """Each pass brings one whole small file and then fails: new bytes arrived, so the counter stays at 1."""
    files = {"a.json": b"aa", "b.json": b"bb", "c.json": b"cc"}
    hub = serve(monkeypatch, Hub(files, cuts=[None, 0, None, 0, None]))
    fetch_model.download(tmp_path, "org/repo", REVISION, attempts=2)
    assert len(hub.requests) == 5
    assert all((tmp_path / name).read_bytes() == body for name, body in files.items())


def test_a_listing_failure_is_retried_and_counts_as_no_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub({"config.json": b"{}"}, list_errors=[RuntimeError("hub down")] * 2))
    with pytest.raises(RuntimeError):
        fetch_model.download(tmp_path, "org/repo", REVISION, attempts=2)
    assert len(hub.list_calls) == 2
    assert hub.requests == []


def test_a_wrong_hash_downloads_the_file_again_from_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    corrupt = b"\xff" + PAYLOAD[1:]
    hub = serve(monkeypatch, Hub({SHARD: corrupt}, listed_hashes={SHARD: PAYLOAD_SHA}))

    real_fetch_file = fetch_model.fetch_file

    def heal_after_the_first_transfer(*args, **kwargs) -> None:
        try:
            real_fetch_file(*args, **kwargs)
        finally:
            hub.files[SHARD] = PAYLOAD

    monkeypatch.setattr(fetch_model, "fetch_file", heal_after_the_first_transfer)
    fetch_model.download(tmp_path, "org/repo", REVISION)
    assert hub.ranges == ["bytes=0-", "bytes=0-"]
    assert_installed(tmp_path / SHARD)


def test_a_hash_that_never_matches_stops_at_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}, listed_hashes={SHARD: "0" * 64}))
    with pytest.raises(OSError, match=SHARD):
        fetch_model.download(tmp_path, "org/repo", REVISION, attempts=3)
    assert len(hub.requests) == 3
    assert not part_of(tmp_path / SHARD).exists()


def test_a_full_disk_is_never_retried_even_after_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], full_disk
) -> None:
    hub = serve(monkeypatch, Hub({SHARD: PAYLOAD}))
    full_disk(CHUNK)
    with pytest.raises(OSError) as caught:
        fetch_model.download(tmp_path, "org/repo", REVISION)
    assert caught.value.errno == errno.ENOSPC
    assert len(hub.requests) == 1
    assert sleeps == []
    assert 0 < part_of(tmp_path / SHARD).stat().st_size <= CHUNK


# --------------------------------------------------------------------------- main


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "models"
    target.mkdir()
    free = fetch_model.MODEL_BYTES * 2
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: DiskUsage(total=free, used=0, free=free))
    monkeypatch.setenv("MODEL_PATH", str(target))
    for name in ("HF_MODEL_REPO", "HF_MODEL_REVISION", "HR_AI_MODEL_READONLY"):
        monkeypatch.delenv(name, raising=False)
    return target


def small_model() -> dict[str, bytes]:
    required = dict.fromkeys(("config.json", "tokenizer.json", "tokenizer_config.json"), b"{}")
    return {**required, "model.safetensors": PAYLOAD}


def test_main_survives_an_interruption_and_ends_with_a_complete_model(
    model_dir: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    hub = serve(monkeypatch, Hub(small_model(), cuts=[None, None, None, CHUNK]))
    assert fetch_model.main() == 0
    assert hub.ranges[-2:] == ["bytes=0-", f"bytes={CHUNK}-"]
    assert_installed(model_dir / "model.safetensors")
    assert fetch_model.is_complete(model_dir)[0] is True


def test_main_continues_a_part_file_left_by_an_earlier_run(
    model_dir: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    part_of(model_dir / "model.safetensors").write_bytes(PAYLOAD[:CHUNK])
    hub = serve(monkeypatch, Hub(small_model()))
    assert fetch_model.main() == 0
    assert hub.ranges[-1] == f"bytes={CHUNK}-"
    assert_installed(model_dir / "model.safetensors")


def test_main_gives_up_with_1_and_keeps_the_part_for_the_next_run(
    model_dir: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], capsys: pytest.CaptureFixture[str]
) -> None:
    (model_dir / "model.safetensors.part").write_bytes(PAYLOAD[:CHUNK])
    serve(monkeypatch, Hub({"model.safetensors": PAYLOAD}, status=503))
    assert fetch_model.main() == 1
    assert (model_dir / "model.safetensors.part").read_bytes() == PAYLOAD[:CHUNK]
    assert "resume" in capsys.readouterr().err.lower()


def test_main_stops_on_a_full_disk_during_the_transfer_and_says_so(
    model_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    full_disk,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = serve(monkeypatch, Hub({"model.safetensors": PAYLOAD}))
    full_disk(CHUNK)
    assert fetch_model.main() == 1
    assert len(hub.requests) == 1
    assert sleeps == []
    assert "disk space" in capsys.readouterr().err.lower()
