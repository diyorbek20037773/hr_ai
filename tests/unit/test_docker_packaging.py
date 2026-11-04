"""Docker packaging: the install-time model download and the invariants of the container files.

Written from the packaging spec, not from the implementation. Nothing here loads the model, needs Docker or touches
the network: `huggingface_hub.snapshot_download` and `time.sleep` are replaced, and the container files are parsed
(YAML, JSON, AST, Dockerfile instructions), so a rule mentioned only in a comment does not count.
"""

from __future__ import annotations

import ast
import fnmatch
import inspect
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import huggingface_hub
import pytest
import yaml
from prometheus_client import generate_latest

import app.core.metrics  # noqa: F401 -- registers the hr_ai_* collectors that /metrics exposes
from app.scripts import fetch_model

ROOT = Path(__file__).resolve().parents[2]
REAL_DOWNLOAD = fetch_model.download

SHARD_1 = "model-00001-of-00002.safetensors"
SHARD_2 = "model-00002-of-00002.safetensors"
INDEX = "model.safetensors.index.json"
REQUIRED = ("config.json", "tokenizer.json", "tokenizer_config.json")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_yaml(relative: str) -> dict:
    return yaml.safe_load(read(relative))


def write_model(path: Path) -> Path:
    """A complete two-shard model directory."""
    path.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED:
        (path / name).write_text("{}", encoding="utf-8")
    weight_map = {"layer.a": SHARD_1, "layer.b": SHARD_2, "layer.c": SHARD_2}
    (path / INDEX).write_text(json.dumps({"metadata": {}, "weight_map": weight_map}), encoding="utf-8")
    for name in (SHARD_1, SHARD_2):
        (path / name).write_bytes(b"\0")
    return path


def tree(path: Path) -> dict[str, tuple[int, int]]:
    """Every entry under `path` with size and mtime, to prove that nothing was written."""
    return {str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(path.rglob("*"))}


class FakeDownload:
    """Stands in for fetch_model.download; arguments are bound to the real signature so call style does not matter."""

    def __init__(self, effect: Callable[[Path], None] | None = None):
        self.calls: list[dict] = []
        self.effect = effect

    def __call__(self, *args, **kwargs) -> None:
        bound = inspect.signature(REAL_DOWNLOAD).bind(*args, **kwargs)
        self.calls.append(dict(bound.arguments))
        if self.effect:
            self.effect(Path(bound.arguments["path"]))


class FakeHub:
    """Stands in for huggingface_hub.snapshot_download: raises the queued errors, then succeeds."""

    def __init__(self, errors: list[BaseException] | None = None, effect: Callable[[Path], None] | None = None):
        self.calls: list[dict] = []
        self.errors = list(errors or [])
        self.effect = effect

    def __call__(self, *args, **kwargs) -> str:
        assert not args, "snapshot_download must be called with keyword arguments"
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        if self.effect:
            self.effect(Path(kwargs["local_dir"]))
        return str(kwargs["local_dir"])


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeHub]:
    def install(errors: list[BaseException] | None = None, effect: Callable[[Path], None] | None = None) -> FakeHub:
        fake = FakeHub(errors, effect)
        monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
        return fake

    return install


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty MODEL_PATH with the other variables main() reads unset."""
    target = tmp_path / "models"
    target.mkdir()
    monkeypatch.setenv("MODEL_PATH", str(target))
    for name in ("HF_MODEL_REPO", "HF_MODEL_REVISION", "HR_AI_MODEL_READONLY"):
        monkeypatch.delenv(name, raising=False)
    return target


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeDownload]:
    def install(effect: Callable[[Path], None] | None = None) -> FakeDownload:
        fake = FakeDownload(effect)
        monkeypatch.setattr(fetch_model, "download", fake)
        return fake

    return install


# --------------------------------------------------------------------------- A. constants and import


def test_default_repo_is_the_project_model() -> None:
    assert fetch_model.DEFAULT_REPO == "Qwen/Qwen3-4B-Instruct-2507"


def test_default_revision_is_a_full_commit_sha() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", fetch_model.DEFAULT_REVISION)


def test_import_does_not_pull_in_torch_or_transformers() -> None:
    code = (
        "import json, sys\n"
        "import app.scripts.fetch_model\n"
        "print(json.dumps({name: name in sys.modules for name in ('torch', 'transformers')}))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"torch": False, "transformers": False}


def test_only_the_install_step_talks_to_the_hub() -> None:
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if path.name == "fetch_model.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"huggingface_hub|snapshot_download|hf_hub_download|fetch_model", text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


# --------------------------------------------------------------------------- A. is_complete


def test_is_complete_true_for_a_complete_directory(tmp_path: Path) -> None:
    complete, detail = fetch_model.is_complete(write_model(tmp_path / "m"))
    assert complete is True
    assert isinstance(detail, str)


@pytest.mark.parametrize("name", REQUIRED)
def test_is_complete_false_and_names_a_missing_required_file(tmp_path: Path, name: str) -> None:
    path = write_model(tmp_path / "m")
    (path / name).unlink()
    complete, detail = fetch_model.is_complete(path)
    assert complete is False
    assert name in detail


def test_is_complete_names_every_missing_required_file(tmp_path: Path) -> None:
    path = write_model(tmp_path / "m")
    for name in REQUIRED:
        (path / name).unlink()
    complete, detail = fetch_model.is_complete(path)
    assert complete is False
    assert all(name in detail for name in REQUIRED)


def test_is_complete_false_and_names_only_the_missing_shard(tmp_path: Path) -> None:
    path = write_model(tmp_path / "m")
    (path / SHARD_2).unlink()
    complete, detail = fetch_model.is_complete(path)
    assert complete is False
    assert SHARD_2 in detail
    assert SHARD_1 not in detail


def test_is_complete_names_every_missing_shard(tmp_path: Path) -> None:
    path = write_model(tmp_path / "m")
    (path / SHARD_1).unlink()
    (path / SHARD_2).unlink()
    complete, detail = fetch_model.is_complete(path)
    assert complete is False
    assert SHARD_1 in detail
    assert SHARD_2 in detail


def test_is_complete_false_when_the_shard_index_is_gone(tmp_path: Path) -> None:
    path = write_model(tmp_path / "m")
    (path / INDEX).unlink()
    assert fetch_model.is_complete(path)[0] is False


def test_is_complete_false_for_an_empty_directory(tmp_path: Path) -> None:
    complete, detail = fetch_model.is_complete(tmp_path)
    assert complete is False
    assert detail


def test_is_complete_false_for_a_directory_that_does_not_exist(tmp_path: Path) -> None:
    assert fetch_model.is_complete(tmp_path / "absent")[0] is False


@pytest.mark.parametrize(
    "content",
    ['{"weight_map": {"layer.a": "model-0', "", "[]", "{}", '{"weight_map": {}}'],
    ids=["truncated-json", "empty-file", "json-array", "no-weight-map", "empty-weight-map"],
)
def test_is_complete_reports_an_unusable_index_instead_of_raising(tmp_path: Path, content: str) -> None:
    """The contract is a (bool, detail) tuple; an index that lists no shard cannot prove the weights are there."""
    path = write_model(tmp_path / "m")
    (path / INDEX).write_text(content, encoding="utf-8")
    complete, detail = fetch_model.is_complete(path)
    assert complete is False
    assert detail


# --------------------------------------------------------------------------- A. download


def test_download_calls_the_hub_with_the_requested_snapshot(tmp_path: Path, hub, sleeps: list[float]) -> None:
    fake = hub()
    fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["repo_id"] == "org/repo"
    assert call["revision"] == "a" * 40
    assert Path(call["local_dir"]) == tmp_path
    assert sleeps == []


@pytest.mark.parametrize(
    "filename",
    [
        "model-00001-of-00003.safetensors",
        INDEX,
        *REQUIRED,
        "generation_config.json",
        "vocab.json",
        "merges.txt",
    ],
)
def test_allow_patterns_fetch_what_a_complete_model_needs(tmp_path: Path, hub, filename: str) -> None:
    fake = hub()
    fetch_model.download(tmp_path, "org/repo", "a" * 40)
    patterns = fake.calls[0]["allow_patterns"]
    assert "*.safetensors" in patterns
    assert any(fnmatch.fnmatch(filename, pattern) for pattern in patterns)


def test_download_retries_until_it_succeeds(tmp_path: Path, hub, sleeps: list[float]) -> None:
    fake = hub(errors=[OSError("reset"), TimeoutError("slow")])
    fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert len(fake.calls) == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize("error", [OSError("x"), ValueError("x"), RuntimeError("x"), KeyError("x"), Exception("x")])
def test_download_retries_on_any_exception_type(tmp_path: Path, hub, sleeps: list[float], error: Exception) -> None:
    fake = hub(errors=[error])
    fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert len(fake.calls) == 2


def test_download_backs_off(tmp_path: Path, hub, sleeps: list[float]) -> None:
    hub(errors=[OSError(str(i)) for i in range(4)])
    fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert len(sleeps) == 4
    assert all(delay > 0 for delay in sleeps)
    assert sleeps == sorted(sleeps)
    assert sleeps[-1] > sleeps[0]


def test_download_reraises_the_last_error_after_five_attempts(tmp_path: Path, hub, sleeps: list[float]) -> None:
    errors = [OSError(f"failure {i}") for i in range(5)]
    fake = hub(errors=list(errors))
    with pytest.raises(OSError) as caught:
        fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert caught.value is errors[-1]
    assert len(fake.calls) == 5
    assert len(sleeps) == 4, "no pause after the last attempt"


def test_download_honours_a_custom_attempt_count(tmp_path: Path, hub, sleeps: list[float]) -> None:
    fake = hub(errors=[OSError(str(i)) for i in range(3)])
    with pytest.raises(OSError):
        fetch_model.download(tmp_path, "org/repo", "a" * 40, attempts=2)
    assert len(fake.calls) == 2
    assert len(sleeps) == 1


def test_download_does_not_retry_a_keyboard_interrupt(tmp_path: Path, hub, sleeps: list[float]) -> None:
    fake = hub(errors=[KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert len(fake.calls) == 1
    assert sleeps == []


# --------------------------------------------------------------------------- A. main


@pytest.mark.parametrize("readonly", [None, "0", "1"])
def test_main_leaves_a_complete_directory_alone(
    model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch, readonly: str | None
) -> None:
    write_model(model_dir)
    before = tree(model_dir)
    if readonly is not None:
        monkeypatch.setenv("HR_AI_MODEL_READONLY", readonly)
    fake = fake_download(write_model)
    assert fetch_model.main() == 0
    assert fake.calls == []
    assert tree(model_dir) == before


def test_main_read_only_and_empty_returns_2(model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HR_AI_MODEL_READONLY", "1")
    fake = fake_download(write_model)
    assert fetch_model.main() == 2
    assert fake.calls == []
    assert tree(model_dir) == {}


def test_main_read_only_and_partial_writes_nothing(
    model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_model(model_dir)
    (model_dir / SHARD_2).unlink()
    before = tree(model_dir)
    monkeypatch.setenv("HR_AI_MODEL_READONLY", "1")
    fake = fake_download(write_model)
    assert fetch_model.main() == 2
    assert fake.calls == []
    assert tree(model_dir) == before


def test_main_read_only_and_absent_directory_does_not_create_it(
    model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = model_dir / "nested" / "absent"
    monkeypatch.setenv("MODEL_PATH", str(missing))
    monkeypatch.setenv("HR_AI_MODEL_READONLY", "1")
    fake = fake_download(write_model)
    assert fetch_model.main() == 2
    assert fake.calls == []
    assert not missing.parent.exists()


def test_main_read_only_with_a_corrupt_index_returns_2(
    model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_model(model_dir)
    (model_dir / INDEX).write_text('{"weight_map": {"layer.a": "model-0', encoding="utf-8")
    before = tree(model_dir)
    monkeypatch.setenv("HR_AI_MODEL_READONLY", "1")
    fake = fake_download(write_model)
    assert fetch_model.main() == 2
    assert fake.calls == []
    assert tree(model_dir) == before


def test_main_writable_with_a_corrupt_index_downloads_again(model_dir: Path, fake_download) -> None:
    write_model(model_dir)
    (model_dir / INDEX).write_text('{"weight_map": {"layer.a": "model-0', encoding="utf-8")
    fake = fake_download(write_model)
    assert fetch_model.main() == 0
    assert len(fake.calls) == 1


@pytest.mark.parametrize("readonly", [None, "0", ""])
def test_main_writable_downloads_the_pinned_revision(
    model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch, readonly: str | None
) -> None:
    if readonly is not None:
        monkeypatch.setenv("HR_AI_MODEL_READONLY", readonly)
    fake = fake_download(write_model)
    assert fetch_model.main() == 0
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert Path(call["path"]) == model_dir
    assert call["repo"] == fetch_model.DEFAULT_REPO
    assert call["revision"] == fetch_model.DEFAULT_REVISION


def test_main_returns_1_when_still_incomplete_after_download(model_dir: Path, fake_download) -> None:
    def leaves_a_shard_out(path: Path) -> None:
        write_model(path)
        (path / SHARD_2).unlink()

    fake = fake_download(leaves_a_shard_out)
    assert fetch_model.main() == 1
    assert len(fake.calls) == 1


def test_main_returns_1_when_download_writes_nothing(model_dir: Path, fake_download) -> None:
    fake = fake_download()
    assert fetch_model.main() == 1
    assert len(fake.calls) == 1


def test_main_honours_repo_and_revision_from_the_environment(
    model_dir: Path, fake_download, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_MODEL_REPO", "org/other")
    monkeypatch.setenv("HF_MODEL_REVISION", "b" * 40)
    fake = fake_download(write_model)
    assert fetch_model.main() == 0
    assert fake.calls[0]["repo"] == "org/other"
    assert fake.calls[0]["revision"] == "b" * 40


def test_main_end_to_end_reaches_the_hub_with_the_pin(model_dir: Path, hub, sleeps: list[float]) -> None:
    """Nothing of fetch_model is replaced: a retry, then a snapshot that really fills the directory."""
    fake = hub(errors=[OSError("reset")], effect=write_model)
    assert fetch_model.main() == 0
    assert len(fake.calls) == 2
    assert all(call["revision"] == fetch_model.DEFAULT_REVISION for call in fake.calls)
    assert all(call["repo_id"] == fetch_model.DEFAULT_REPO for call in fake.calls)
    assert fetch_model.is_complete(model_dir)[0] is True


def test_main_never_reports_success_when_every_download_attempt_fails(
    model_dir: Path, hub, sleeps: list[float]
) -> None:
    """Raising and returning non-zero both fail the container, which is what keeps `api` from starting."""
    hub(errors=[OSError(str(i)) for i in range(5)])
    try:
        code = fetch_model.main()
    except OSError:
        return
    assert code != 0


# --------------------------------------------------------------------------- B. docker-compose.yml


@pytest.fixture(scope="module")
def services() -> dict:
    return load_yaml("docker-compose.yml")["services"]


def is_loopback(port: str | dict) -> bool:
    if isinstance(port, dict):
        return port.get("host_ip") == "127.0.0.1"
    return str(port).startswith("127.0.0.1:")


def model_mounts(service: dict) -> list[str]:
    """Mounts whose target is /models; the mode may be a compose variable such as ${HR_AI_MODEL_MODE:-rw}."""
    return [v for v in service.get("volumes", []) if re.search(r":/models(:\S+)?$", str(v))]


@pytest.mark.parametrize("name", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
def test_api_is_forced_offline(services: dict, name: str) -> None:
    assert services["api"]["environment"][name] == "1"


def test_api_does_not_receive_a_hub_token(services: dict) -> None:
    assert not any("TOKEN" in name for name in services["api"]["environment"])


def test_api_mounts_the_model_read_only_at_models(services: dict) -> None:
    mounts = model_mounts(services["api"])
    assert len(mounts) == 1
    assert mounts[0].endswith(":/models:ro")


def test_api_reads_the_volume_model_init_fills(services: dict) -> None:
    source = model_mounts(services["api"])[0].rsplit(":/models", 1)[0]
    assert model_mounts(services["model-init"])[0].rsplit(":/models", 1)[0] == source


@pytest.mark.parametrize("name", ["api", "grafana"])
def test_published_ports_are_loopback_only(services: dict, name: str) -> None:
    ports = services[name].get("ports")
    assert ports, f"{name} is expected to publish a port"
    assert all(is_loopback(port) for port in ports), ports


def test_no_service_uses_host_networking(services: dict) -> None:
    assert not any(service.get("network_mode") == "host" for service in services.values())


@pytest.mark.parametrize("name", ["prometheus", "model-init"])
def test_internal_services_publish_nothing(services: dict, name: str) -> None:
    assert not services[name].get("ports")


def test_model_init_is_one_shot(services: dict) -> None:
    assert services["model-init"]["restart"] == "no"


def test_model_init_runs_the_fetch_script(services: dict) -> None:
    assert "app.scripts.fetch_model" in services["model-init"]["command"]


def test_api_waits_for_model_init_to_succeed(services: dict) -> None:
    assert services["api"]["depends_on"]["model-init"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize("overlay", sorted(p.name for p in (ROOT / "docker").glob("compose.*.yml")))
def test_accelerator_overlays_do_not_widen_the_stack(overlay: str) -> None:
    """start.sh merges one of these over docker-compose.yml, so they could silently undo the rules above."""
    overlay_services = load_yaml(f"docker/{overlay}").get("services") or {}
    for service in overlay_services.values():
        assert not {"ports", "volumes", "environment", "network_mode", "command", "env_file"} & set(service)


# --------------------------------------------------------------------------- B. Dockerfile


@pytest.fixture(scope="module")
def instructions() -> list[tuple[str, str]]:
    """(KEYWORD, arguments) per instruction: comments removed, line continuations joined."""
    code = "\n".join(line for line in read("Dockerfile").splitlines() if not line.lstrip().startswith("#"))
    joined = re.sub(r"\\\n\s*", " ", code)
    parsed = []
    for line in joined.splitlines():
        if line.strip():
            keyword, _, arguments = line.strip().partition(" ")
            parsed.append((keyword.upper(), arguments.strip()))
    return parsed


def arguments_of(instructions: list[tuple[str, str]], keyword: str) -> list[str]:
    return [arguments for name, arguments in instructions if name == keyword]


def code_text(instructions: list[tuple[str, str]]) -> str:
    return "\n".join(f"{keyword} {arguments}" for keyword, arguments in instructions)


def test_dockerfile_cmd_runs_exactly_one_worker(instructions: list[tuple[str, str]]) -> None:
    commands = arguments_of(instructions, "CMD")
    assert len(commands) == 1
    argv = json.loads(commands[0])
    assert "uvicorn" in argv
    assert argv.count("--workers") == 1
    assert argv[argv.index("--workers") + 1] == "1"


def test_dockerfile_never_uses_reload(instructions: list[tuple[str, str]]) -> None:
    assert "--reload" not in code_text(instructions)


def test_dockerfile_runs_as_a_non_root_user(instructions: list[tuple[str, str]]) -> None:
    users = arguments_of(instructions, "USER")
    assert users, "no USER instruction"
    assert users[-1].split(":")[0] not in {"root", "0"}
    keywords = [keyword for keyword, _ in instructions]
    last_user = len(keywords) - 1 - keywords[::-1].index("USER")
    assert last_user < keywords.index("CMD")


def test_dockerfile_holds_no_token_or_secret(instructions: list[tuple[str, str]]) -> None:
    text = code_text(instructions)
    assert not re.search(r"HF_TOKEN|HUGGING_?FACE_?(HUB_)?TOKEN|\bhf_[A-Za-z0-9]{20,}", text)
    assert not re.search(r"\.env\b", text)
    for declared in arguments_of(instructions, "ENV") + arguments_of(instructions, "ARG"):
        assert not re.search(r"TOKEN|SECRET|PASSWORD|API_?KEY", declared, re.IGNORECASE), declared


def test_dockerfile_never_downloads_the_model(instructions: list[tuple[str, str]]) -> None:
    text = code_text(instructions)
    forbidden = (
        r"snapshot_download|hf_hub_download|fetch_model|huggingface-cli|\bhf download\b|huggingface\.co|\bhf\.co\b"
        r"|git[ -]lfs|git clone|\bwget\b|\bcurl\b"
    )
    assert not re.search(forbidden, text, re.IGNORECASE)
    copied = " ".join(arguments_of(instructions, "COPY") + arguments_of(instructions, "ADD"))
    assert not re.search(r"(^|\s)\.?/?model\b", copied)


def test_dockerfile_installs_torch_from_the_selected_index(instructions: list[tuple[str, str]]) -> None:
    runs = [run for run in arguments_of(instructions, "RUN") if re.search(r"pip install\s+\S*torch", run)]
    assert len(runs) == 1
    assert re.search(r'--index-url\s+"?https://download\.pytorch\.org/whl/\$\{TORCH_INDEX\}"?(\s|$)', runs[0])
    assert any(arg.split("=")[0] == "TORCH_INDEX" for arg in arguments_of(instructions, "ARG"))


def test_runtime_requirements_include_the_hub_client() -> None:
    """model-init imports huggingface_hub, and the image installs only what precedes the `# dev` section."""
    runtime = read("requirements.txt").split("# dev", 1)[0]
    assert re.search(r"^huggingface_hub==", runtime, re.MULTILINE)


# --------------------------------------------------------------------------- B. .dockerignore / .gitignore


def dockerignore_rules() -> list[str]:
    lines = (line.strip() for line in read(".dockerignore").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_dockerignore_starts_by_excluding_everything() -> None:
    assert dockerignore_rules()[0] == "*"


@pytest.mark.parametrize(
    "path",
    [
        "model",
        "model/config.json",
        "model/model-00001-of-00003.safetensors",
        ".env",
        ".env.docker",
        "logs",
        "logs/app.log",
        "tests",
        "tests/unit/test_enums.py",
    ],
)
def test_dockerignore_does_not_reinclude(path: str) -> None:
    reincluded = [rule[1:].rstrip("/") for rule in dockerignore_rules() if rule.startswith("!")]
    assert reincluded, "an allow-list needs at least one exception"
    assert not [pattern for pattern in reincluded if fnmatch.fnmatch(path, pattern)]


def test_dockerignore_allows_everything_the_image_copies(instructions: list[tuple[str, str]]) -> None:
    allowed = {rule[1:].rstrip("/") for rule in dockerignore_rules() if rule.startswith("!")}
    sources = [arguments.split()[0].rstrip("/") for arguments in arguments_of(instructions, "COPY")]
    assert sources
    assert set(sources) <= allowed


def test_gitignore_ignores_the_generated_docker_env() -> None:
    assert "/.env.docker" in [line.strip() for line in read(".gitignore").splitlines()]


# --------------------------------------------------------------------------- B. start.sh / start.ps1


CUDA_TABLE = [(1208, "cu128", "2.11.0"), (1206, "cu126", "2.11.0"), (1108, "cu118", "2.7.1")]


def sh_cuda_table() -> list[tuple[int, str, str]]:
    pattern = r'"\$num" -ge (\d+) \]; then TORCH_INDEX=(\S+) TORCH_VERSION=(\S+)'
    return [(int(num), index, version) for num, index, version in re.findall(pattern, read("start.sh"))]


def ps1_cuda_table() -> list[tuple[int, str, str]]:
    pattern = r'\$num -ge (\d+)\) \{ \$hw\.Index = "([^"]+)"; \$hw\.Version = "([^"]+)"'
    return [(int(num), index, version) for num, index, version in re.findall(pattern, read("start.ps1"))]


def test_start_sh_version_table() -> None:
    assert sh_cuda_table() == CUDA_TABLE


def test_start_ps1_version_table() -> None:
    assert ps1_cuda_table() == CUDA_TABLE


def test_start_scripts_compare_cuda_on_the_same_scale() -> None:
    """1208 means 12.8 only if both scripts compute major * 100 + minor."""
    assert "num=$(( major * 100 + minor ))" in read("start.sh")
    assert "$num = [int]$Matches[1] * 100 + [int]$Matches[2]" in read("start.ps1")


def test_start_scripts_share_min_vram() -> None:
    sh = re.findall(r"^MIN_VRAM_MB=(\d+)", read("start.sh"), re.MULTILINE)
    ps1 = re.findall(r"^\$MinVramMb = (\d+)", read("start.ps1"), re.MULTILINE)
    assert sh == ["4500"]
    assert ps1 == ["4500"]


def test_start_sh_maps_amd_to_rocm() -> None:
    assert re.search(r"TORCH_INDEX=rocm7\.0 TORCH_VERSION=2\.10\.0\b", read("start.sh"))


# --------------------------------------------------------------------------- B. llm_service stays offline


def test_every_from_pretrained_is_local_files_only() -> None:
    source = read("app/services/llm_service.py")
    tree_ = ast.parse(source)

    dict_literals: dict[str, ast.Dict] = {}
    for node in ast.walk(tree_):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            dict_literals.update({t.id: node.value for t in node.targets if isinstance(t, ast.Name)})
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict) and isinstance(node.target, ast.Name):
            dict_literals[node.target.id] = node.value

    def literal_flag(mapping: ast.Dict) -> object:
        for key, value in zip(mapping.keys, mapping.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "local_files_only":
                return getattr(value, "value", None)
        return None

    calls = [
        node
        for node in ast.walk(tree_)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "from_pretrained"
    ]
    assert len(calls) >= 2, "expected the tokenizer and the model loads"
    for call in calls:
        flags = []
        for keyword in call.keywords:
            if keyword.arg == "local_files_only":
                flags.append(getattr(keyword.value, "value", None))
            elif keyword.arg is None and isinstance(keyword.value, ast.Name):
                flags.append(literal_flag(dict_literals[keyword.value.id]))
        assert True in flags, f"from_pretrained at line {call.lineno} is not local_files_only=True"

    assert not re.search(r"local_files_only[\"']?\s*[:=]\s*False", source)
    assert not re.search(r"\[[\"']local_files_only[\"']\]\s*=", source), "the flag must not be reassigned"


def test_llm_service_has_no_download_call() -> None:
    source = read("app/services/llm_service.py")
    assert "snapshot_download" not in source
    assert "hf_hub_download" not in source


# --------------------------------------------------------------------------- B. monitoring


def walk_values(node: object, key: str) -> Iterator[object]:
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                yield value
            yield from walk_values(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from walk_values(item, key)


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(read("ops/grafana/dashboards/hr_ai.json"))


def exposed_metrics() -> dict[str, str]:
    """Name -> type exactly as a scrape of /metrics reports them, so Counter suffixes are already right."""
    return dict(re.findall(r"^# TYPE (hr_ai_\w+) (\w+)$", generate_latest().decode(), re.MULTILINE))


def test_dashboard_queries_only_metrics_that_exist(dashboard: dict) -> None:
    expressions = [expr for expr in walk_values(dashboard, "expr") if isinstance(expr, str)]
    assert expressions, "the dashboard has no queries"
    queried = {name for expr in expressions for name in re.findall(r"hr_ai_[a-z_]+", expr)}
    assert queried, "no hr_ai_ metric is queried"

    exposed = exposed_metrics()
    unknown = sorted(name for name in queried if name.removesuffix("_bucket") not in exposed)
    assert unknown == []
    not_histograms = sorted(
        name for name in queried if name.endswith("_bucket") and exposed[name.removesuffix("_bucket")] != "histogram"
    )
    assert not_histograms == []


def test_counter_names_are_not_double_suffixed() -> None:
    exposed = exposed_metrics()
    assert exposed.get("hr_ai_http_requests_total") == "counter"
    assert not [name for name in exposed if name.endswith("_total_total")]


def test_dashboard_datasource_is_the_provisioned_one(dashboard: dict) -> None:
    provisioned = load_yaml("ops/grafana/provisioning/datasources/prometheus.yml")["datasources"]
    uids = {source["uid"] for source in provisioned}
    assert provisioned[0]["url"] == "http://prometheus:9090"
    used = {value["uid"] for value in walk_values(dashboard, "datasource") if isinstance(value, dict)}
    assert used
    assert used <= uids


def test_prometheus_scrapes_the_api_inside_the_compose_network() -> None:
    config = load_yaml("ops/prometheus.docker.yml")
    targets = [target for group in walk_values(config, "targets") for target in group]
    assert "api:8001" in targets
