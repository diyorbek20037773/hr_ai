"""Hardening found in review: title/evidence/age guards, install-time download, compose and start scripts, test page.

Written from the review spec, not from the implementation. Nothing here loads the model, runs Docker or touches the
network: `shutil.disk_usage`, `snapshot_download` and `time.sleep` are replaced, compose files are parsed as YAML, and
the start scripts are read with comments removed, so a rule that exists only in a comment does not count. The one
behavioural script check runs a single extracted bash function with `docker` stubbed as a shell function.
"""

from __future__ import annotations

import errno
import re
import shutil
import subprocess
import time
from collections import namedtuple
from collections.abc import Callable
from pathlib import Path

import huggingface_hub
import pytest
import yaml
from fastapi.testclient import TestClient

from app.models.ai_intent import ParsedSearchIntent
from app.scripts import fetch_model
from app.services.intent_guards import (
    apply_all,
    drop_unstated,
    drop_unstated_positions,
    fix_age_direction,
    fix_generic_title,
)

ROOT = Path(__file__).resolve().parents[2]
TITLE_FIX = "academicTitle->isAcademicTitle"
DiskUsage = namedtuple("DiskUsage", "total used free")


def intent_of(**fields) -> ParsedSearchIntent:
    return ParsedSearchIntent(**fields)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- 1. fix_generic_title


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("Mukofotlangan, ilmiy unvonga ega xodimlar", "Professor"),
        ("ilmiy unvonga ega xodimlar", "Dotsent"),
        ("ILMIY UNVONGA EGA xodimlar", "Professor"),
        ("ilmiy unvoni bor ayollar", "Professor"),
        ("илмий унвонга эга ходимлар", "Профессор"),
        ("сотрудники с учёным званием", "Профессор"),
        ("сотрудники с ученым званием", "Доцент"),
    ],
)
def test_generic_title_becomes_the_flag(query: str, title: str) -> None:
    out, fixes = fix_generic_title(query, intent_of(academicTitle=title))
    assert out.academicTitle is None
    assert out.isAcademicTitle is True
    assert fixes == [TITLE_FIX]


def test_generic_title_keeps_the_other_filters() -> None:
    query = "Mukofotlangan, ilmiy unvonga ega xodimlar"
    out, _ = fix_generic_title(query, intent_of(academicTitle="Professor", sex="Ayol", fromAge=30))
    assert out.sex == "Ayol"
    assert out.fromAge == 30


def test_generic_title_fix_is_reported_by_apply_all() -> None:
    out, fixes = apply_all("Mukofotlangan, ilmiy unvonga ega xodimlar", intent_of(academicTitle="Professor"))
    assert out.academicTitle is None
    assert out.isAcademicTitle is True
    assert TITLE_FIX in fixes


def assert_title_unchanged(query: str, title: str) -> None:
    intent = intent_of(academicTitle=title)
    out, fixes = fix_generic_title(query, intent)
    assert fixes == []
    assert out.academicTitle == title
    assert out.isAcademicTitle is None
    assert out == intent


@pytest.mark.parametrize(
    "query",
    ["harbiy unvon", "harbiy unvonga ega xodimlar", "воинское звание", "унвонга эга ходимлар", "Mukofotlangan"],
)
def test_title_stays_without_an_academic_qualifier(query: str) -> None:
    assert_title_unchanged(query, "Professor")


@pytest.mark.parametrize("query", ["ilmiy bo'lim xodimlari", "илмий бўлим ходимлари", "учёный совет"])
def test_title_stays_without_a_generic_word(query: str) -> None:
    assert_title_unchanged(query, "Professor")


@pytest.mark.parametrize(
    "query",
    [
        "ilmiy unvoni yo'q xodimlar",
        "ilmiy unvoni yo‘q xodimlar",
        "ilmiy unvoni yoʻq xodimlar",
        "ILMIY UNVONI YO'Q xodimlar",
        "илмий унвони йўқ ходимлар",
        "нет учёного звания",
        "без учёного звания",
        "сотрудники без ученого звания",
        "БЕЗ УЧЁНОГО ЗВАНИЯ",
        "ilmiy unvoni bo'lmagan xodimlar",
        "ilmiy unvoni bo‘lmagan xodimlar",
        "илмий унвони бўлмаган ходимлар",
    ],
)
def test_title_stays_when_negated(query: str) -> None:
    assert_title_unchanged(query, "Professor")


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("dosent unvoniga ega", "Dotsent"),
        ("profesor unvoni", "Professor"),
        # With the academic qualifier present, only the named stem can keep the value.
        ("dosent ilmiy unvoniga ega", "Dotsent"),
        ("dotsent ilmiy unvoni", "Professor"),
        ("досент илмий унвони", "Доцент"),
        ("дотсент илмий унвони", "Доцент"),
        ("доцент илмий унвони", "Профессор"),
        ("profesor ilmiy unvoni", "Professor"),
        ("профессор учёное звание", "Professor"),
        ("akademik ilmiy unvoni", "Fanlar akademiyasi a'zosi"),
        ("академик илмий унвони", "Fanlar akademiyasi a'zosi"),
        ("ilmiy xodim unvoni", "Katta ilmiy xodim"),
        ("илмий ходим унвони", "Катта илмий ходим"),
        ("старшие научные сотрудники, учёное звание", "Старший научный сотрудник"),
    ],
)
def test_title_stays_when_a_title_is_named(query: str, title: str) -> None:
    assert_title_unchanged(query, title)


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("katta o'qituvchi ilmiy unvoni", "Katta o'qituvchi"),
        ("katta o‘qituvchi ilmiy unvoni", "Katta o'qituvchi"),
        ("KATTA O'QITUVCHI ilmiy unvoni", "Katta o'qituvchi"),
    ],
)
def test_title_stays_when_its_value_is_in_the_query(query: str, title: str) -> None:
    assert_title_unchanged(query, title)


@pytest.mark.parametrize("fields", [{}, {"isAcademicTitle": True}, {"sex": "Ayol"}])
def test_generic_guard_needs_an_academic_title_value(fields: dict) -> None:
    intent = intent_of(**fields)
    out, fixes = fix_generic_title("ilmiy unvonga ega xodimlar", intent)
    assert fixes == []
    assert out == intent


# --------------------------------------------------------------------------- 2. evidence stems


@pytest.mark.parametrize(
    ("query", "education"),
    [("orta malumotli ayollar", "O'rta"), ("oliy malumotli", "Oliy"), ("oliy malumotli ayollar", "Oliy")],
)
def test_education_without_apostrophe_is_evidence(query: str, education: str) -> None:
    out, fixes = drop_unstated(query, intent_of(education=education))
    assert out.education == education
    assert fixes == []
    assert apply_all(query, intent_of(education=education))[0].education == education


@pytest.mark.parametrize("education", ["Boshlang'ich", "Начальное", "Oliy"])
def test_chief_is_not_primary_education(education: str) -> None:
    out, fixes = drop_unstated("начальники отделов", intent_of(education=education))
    assert out.education is None
    assert fixes == ["drop:education"]


def test_chief_query_loses_education_and_keeps_the_position() -> None:
    out, fixes = apply_all("начальники отделов", intent_of(education="Boshlang'ich", typeOfPosition=["Leader"]))
    assert out.education is None
    assert out.typeOfPosition == ["Leader"]
    assert "drop:education" in fixes


def test_primary_education_stated_in_russian_stays() -> None:
    out, fixes = drop_unstated("начальное образование", intent_of(education="Boshlang'ich"))
    assert out.education == "Boshlang'ich"
    assert fixes == []


@pytest.mark.parametrize(("query", "position"), [("tehnik xodimlar", "Technic"), ("mutahassislar", "Expert")])
def test_position_spelled_with_h_is_evidence(query: str, position: str) -> None:
    out, fixes = drop_unstated_positions(query, intent_of(typeOfPosition=[position]))
    assert out.typeOfPosition == [position]
    assert fixes == []
    assert apply_all(query, intent_of(typeOfPosition=[position]))[0].typeOfPosition == [position]


# --------------------------------------------------------------------------- 3. fix_age_direction

MIN_30 = ["не младше 30 лет", "не моложе 30 лет", "Не младше 30 лет", "сотрудники не моложе 30 лет", "не  младше 30"]
MAX_30 = ["младше 30", "младше 30 лет", "сотрудники младше 30 лет"]


@pytest.mark.parametrize("query", MIN_30)
def test_not_younger_than_is_a_minimum(query: str) -> None:
    out, fixes = fix_age_direction(query, intent_of(toAge=30))
    assert (out.fromAge, out.toAge) == (30, None)
    assert fixes == ["toAge->fromAge"]


@pytest.mark.parametrize("query", MIN_30)
def test_not_younger_than_leaves_a_correct_minimum(query: str) -> None:
    intent = intent_of(fromAge=30)
    assert fix_age_direction(query, intent) == (intent, [])


@pytest.mark.parametrize("query", MIN_30)
def test_not_younger_than_on_both_bounds_keeps_the_minimum(query: str) -> None:
    out, fixes = fix_age_direction(query, intent_of(fromAge=30, toAge=30))
    assert (out.fromAge, out.toAge) == (30, None)
    assert fixes == ["drop:toAge"]


@pytest.mark.parametrize("query", MAX_30)
def test_younger_than_is_a_maximum(query: str) -> None:
    out, fixes = fix_age_direction(query, intent_of(fromAge=30))
    assert (out.fromAge, out.toAge) == (None, 30)
    assert fixes == ["fromAge->toAge"]


@pytest.mark.parametrize("query", MAX_30)
def test_younger_than_leaves_a_correct_maximum(query: str) -> None:
    intent = intent_of(toAge=30)
    assert fix_age_direction(query, intent) == (intent, [])


@pytest.mark.parametrize("query", MAX_30)
def test_younger_than_on_both_bounds_keeps_the_maximum(query: str) -> None:
    out, fixes = fix_age_direction(query, intent_of(fromAge=30, toAge=30))
    assert (out.fromAge, out.toAge) == (None, 30)
    assert fixes == ["drop:fromAge"]


@pytest.mark.parametrize(
    "query",
    ["от 45 до 45 лет", "45 dan 45 gacha", "45 yoshdan 45 yoshgacha", "45 ёшдан 45 ёшгача"],
)
def test_number_written_twice_is_an_exact_age(query: str) -> None:
    intent = intent_of(fromAge=45, toAge=45)
    out, fixes = fix_age_direction(query, intent)
    assert (out.fromAge, out.toAge) == (45, 45)
    assert fixes == []
    out, fixes = apply_all(query, intent)
    assert (out.fromAge, out.toAge) == (45, 45)
    assert fixes == []


@pytest.mark.parametrize("query", ["до 45 лет", "45 yoshgacha", "45 ёшгача"])
def test_number_written_once_still_loses_the_extra_bound(query: str) -> None:
    out, fixes = fix_age_direction(query, intent_of(fromAge=45, toAge=45))
    assert (out.fromAge, out.toAge) == (None, 45)
    assert fixes == ["drop:fromAge"]


# --------------------------------------------------------------------------- 4. fetch_model


@pytest.fixture
def disk(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    def install(free: int) -> None:
        usage = DiskUsage(total=free + 1, used=1, free=free)
        monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage)

    return install


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty, writable MODEL_PATH; main() sees it as incomplete."""
    target = tmp_path / "models"
    target.mkdir()
    monkeypatch.setenv("MODEL_PATH", str(target))
    for name in ("HF_MODEL_REPO", "HF_MODEL_REVISION", "HR_AI_MODEL_READONLY"):
        monkeypatch.delenv(name, raising=False)
    return target


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(fetch_model, "download", lambda *args, **kwargs: calls.append((args, kwargs)))
    return calls


def hub_raising(monkeypatch: pytest.MonkeyPatch, *errors: BaseException) -> list[dict]:
    """Replace snapshot_download: raises the queued errors in order, then succeeds. Returns the recorded calls."""
    calls: list[dict] = []
    queue = list(errors)

    def snapshot_download(**kwargs) -> str:
        calls.append(kwargs)
        if queue:
            raise queue.pop(0)
        return str(kwargs["local_dir"])

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    return calls


def enospc() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device")


def test_disk_space_needs_the_whole_model_when_nothing_is_present(tmp_path: Path, disk) -> None:
    disk(fetch_model.MODEL_BYTES - 1)
    assert fetch_model.has_disk_space(tmp_path) is False
    disk(fetch_model.MODEL_BYTES)
    assert fetch_model.has_disk_space(tmp_path) is True
    disk(fetch_model.MODEL_BYTES + 1)
    assert fetch_model.has_disk_space(tmp_path) is True


def test_disk_space_subtracts_safetensors_already_present(tmp_path: Path, disk) -> None:
    (tmp_path / "model-00001-of-00002.safetensors").write_bytes(b"\0" * 1000)
    (tmp_path / "model-00002-of-00002.safetensors").write_bytes(b"\0" * 500)
    disk(fetch_model.MODEL_BYTES - 1500 - 1)
    assert fetch_model.has_disk_space(tmp_path) is False
    disk(fetch_model.MODEL_BYTES - 1500)
    assert fetch_model.has_disk_space(tmp_path) is True


def test_disk_space_counts_only_safetensors(tmp_path: Path, disk) -> None:
    (tmp_path / "config.json").write_bytes(b"\0" * 1000)
    (tmp_path / "notes.safetensors.txt").write_bytes(b"\0" * 1000)
    disk(fetch_model.MODEL_BYTES - 1)
    assert fetch_model.has_disk_space(tmp_path) is False


def test_disk_space_true_with_no_room_when_all_weights_are_present(
    tmp_path: Path, disk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_model, "MODEL_BYTES", 100)
    (tmp_path / "model.safetensors").write_bytes(b"\0" * 100)
    disk(0)
    assert fetch_model.has_disk_space(tmp_path) is True


@pytest.mark.parametrize("free", [0, fetch_model.MODEL_BYTES - 1])
def test_main_does_not_download_without_disk_space(
    model_dir: Path, disk, downloads: list[tuple], capsys: pytest.CaptureFixture[str], free: int
) -> None:
    disk(free)
    assert fetch_model.main() == 1
    assert downloads == []
    assert "disk" in capsys.readouterr().err.lower()


def test_main_downloads_the_remainder_when_it_fits(
    model_dir: Path, disk, downloads: list[tuple], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_model, "MODEL_BYTES", 100)
    (model_dir / "model-00001-of-00002.safetensors").write_bytes(b"\0" * 60)
    disk(40)
    fetch_model.main()
    assert len(downloads) == 1


def test_download_stops_on_a_full_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    error = enospc()
    calls = hub_raising(monkeypatch, error)
    with pytest.raises(OSError) as caught:
        fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert caught.value is error
    assert len(calls) == 1
    assert sleeps == []


def test_download_still_retries_other_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    calls = hub_raising(monkeypatch, OSError(errno.ECONNRESET, "Connection reset"))
    fetch_model.download(tmp_path, "org/repo", "a" * 40)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error",
    [enospc(), OSError(errno.ECONNRESET, "reset"), RuntimeError("hub"), ValueError("bad"), TimeoutError("slow")],
    ids=["enospc", "connreset", "runtime", "value", "timeout"],
)
def test_main_returns_1_when_download_raises(
    model_dir: Path, disk, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def failing(*_args, **_kwargs) -> None:
        raise error

    disk(fetch_model.MODEL_BYTES)
    monkeypatch.setattr(fetch_model, "download", failing)
    assert fetch_model.main() == 1
    captured = capsys.readouterr()
    assert "resume" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_main_returns_1_after_every_real_attempt_fails(
    model_dir: Path, disk, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], capsys: pytest.CaptureFixture[str]
) -> None:
    disk(fetch_model.MODEL_BYTES)
    calls = hub_raising(monkeypatch, *[OSError(f"failure {i}") for i in range(fetch_model.MAX_ATTEMPTS)])
    assert fetch_model.main() == 1
    assert len(calls) == fetch_model.MAX_ATTEMPTS
    assert "resume" in capsys.readouterr().err.lower()


def test_main_stops_at_the_first_attempt_on_a_full_disk(
    model_dir: Path, disk, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    disk(fetch_model.MODEL_BYTES)
    calls = hub_raising(monkeypatch, enospc())
    assert fetch_model.main() == 1
    assert len(calls) == 1
    assert sleeps == []


def test_read_only_incomplete_message_names_the_download_option(
    model_dir: Path, downloads: list[tuple], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HR_AI_MODEL_READONLY", "1")
    assert fetch_model.main() != 0
    assert downloads == []
    assert "--download-model" in capsys.readouterr().err


# --------------------------------------------------------------------------- 5. docker-compose.yml


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(read("docker-compose.yml"))["services"]


def volumes(service: dict) -> list[str]:
    return [str(volume) for volume in service.get("volumes", [])]


def test_model_init_forwards_the_model_pin(services: dict) -> None:
    environment = services["model-init"]["environment"]
    for name in ("HF_MODEL_REPO", "HF_MODEL_REVISION"):
        assert name in environment
        assert re.fullmatch(rf"\$\{{{name}(:?-[^}}]*)?\}}", str(environment[name])), environment[name]


def test_api_can_reach_services_on_the_host(services: dict) -> None:
    assert "host.docker.internal:host-gateway" in services["api"]["extra_hosts"]


def test_ops_bind_mounts_are_read_only_and_relabelled(services: dict) -> None:
    mounts = [v for service in services.values() for v in volumes(service) if v.startswith("./ops")]
    assert len(mounts) == 3, mounts
    for mount in mounts:
        assert mount.endswith(":ro,z"), mount


@pytest.mark.parametrize("name", ["model-init", "api"])
def test_model_mount_is_not_relabelled(services: dict, name: str) -> None:
    """The source variable can be the user's ./model, whose SELinux label must never be rewritten."""
    mounts = [v for v in volumes(services[name]) if re.search(r":/models(:|$)", v)]
    assert len(mounts) == 1, volumes(services[name])
    assert mounts[0].startswith("${HR_AI_MODEL_SOURCE")
    options = mounts[0].split(":/models", 1)[1].lstrip(":")
    assert not re.search(r"(^|[,:])[zZ]($|[,}])", options), mounts[0]


def test_start_scripts_never_put_a_relabel_flag_in_the_model_mode() -> None:
    modes = re.findall(r"\bmode=(\S+)", strip_sh(read("start.sh")))
    modes += re.findall(r'\$mode = "([^"]+)"', strip_ps1(read("start.ps1")))
    assert {"ro", "rw"} == set(modes)


# --------------------------------------------------------------------------- 6. start.sh / start.ps1


def strip_sh(text: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", text, flags=re.MULTILINE)


def strip_ps1(text: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", re.sub(r"<#.*?#>", "", text, flags=re.DOTALL), flags=re.MULTILINE)


@pytest.fixture(scope="module")
def sh() -> str:
    return strip_sh(read("start.sh"))


@pytest.fixture(scope="module")
def ps1() -> str:
    return strip_ps1(read("start.ps1"))


def sh_function(text: str, name: str) -> str:
    found = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    assert found, f"{name}() not found in start.sh"
    return found.group(1)


def ps1_function(text: str, name: str) -> str:
    found = re.search(rf"^function {name}\b[^\n]*\{{\n(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    assert found, f"function {name} not found in start.ps1"
    return found.group(1)


def main_flow(text: str) -> str:
    """What runs at top level: everything after the closing brace of the last multi-line function."""
    last = list(re.finditer(r"^(?:function [\w-]+|\w+\(\))[^\n]*\{\n", text, re.MULTILINE))[-1]
    closing = re.compile(r"^\}", re.MULTILINE).search(text, last.end())
    assert closing, "the last function is not closed"
    return text[closing.end() :]


def assert_order(text: str, *patterns: str) -> None:
    positions = []
    for pattern in patterns:
        found = re.search(pattern, text)
        assert found, f"{pattern!r} not found in the main flow"
        positions.append(found.start())
    assert positions == sorted(positions), patterns


def test_start_sh_requires_compose_2_24(sh: str) -> None:
    body = sh_function(sh, "check_compose_version")
    assert '[ "$major" -lt 2 ]' in body
    assert '[ "$major" -eq 2 ] && [ "$minor" -lt 24 ]' in body
    assert re.search(r"\bcheck_compose_version\b", sh_function(sh, "ensure_docker"))


def test_start_ps1_requires_compose_2_24(ps1: str) -> None:
    body = ps1_function(ps1, "Test-ComposeVersion")
    assert "[int]$Matches[1] -lt 2 -or ([int]$Matches[1] -eq 2 -and [int]$Matches[2] -lt 24)" in body
    assert re.search(r"\bTest-ComposeVersion\b", ps1_function(ps1, "Initialize-Docker"))


def test_start_scripts_check_docker_before_anything_else(sh: str, ps1: str) -> None:
    assert_order(main_flow(sh), r"\bensure_docker\b", r"\bdetect_hardware\b")
    assert_order(main_flow(ps1), r"\bInitialize-Docker\b", r"\bGet-Hardware\b")


@pytest.fixture(scope="module")
def bash() -> str:
    """Git Bash or a Linux shell. Windows' WSL launcher without a distro does not answer the probe, so it is skipped."""
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("bash is not installed")
    try:
        probe = subprocess.run([executable, "-s"], input=b"echo ok\n", capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("bash does not start")
    if probe.stdout.strip() != b"ok":
        pytest.skip("bash does not run scripts here")
    return executable


def compose_gate(bash: str, version: str) -> subprocess.CompletedProcess:
    """check_compose_version from start.sh, with `docker` and `die` as shell functions."""
    function = sh_function(read("start.sh"), "check_compose_version")
    script = (
        'die() { echo "DIED: $*"; exit 1; }\n'
        f"docker() {{ printf '%s\\n' '{version}'; }}\n"
        f"check_compose_version() {{\n{function}}}\n"
        "check_compose_version\n"
        "echo PASSED\n"
    )
    return subprocess.run([bash, "-s"], input=script.encode("utf-8"), capture_output=True, timeout=30)


@pytest.mark.parametrize("version", ["2.24.0", "v2.24.0", "2.24.0-desktop.1", "2.39.1", "3.0.0", "10.1.0"])
def test_start_sh_accepts_compose_from_2_24(bash: str, version: str) -> None:
    result = compose_gate(bash, version)
    assert result.returncode == 0, result.stdout + result.stderr
    assert b"PASSED" in result.stdout


@pytest.mark.parametrize("version", ["2.23.3", "2.9.0", "v2.3.1", "1.29.2", "", "unknown"])
def test_start_sh_refuses_older_or_unreadable_compose(bash: str, version: str) -> None:
    result = compose_gate(bash, version)
    assert result.returncode != 0
    assert b"DIED" in result.stdout
    assert b"PASSED" not in result.stdout


def test_start_sh_compares_memory_with_9500_mib(sh: str) -> None:
    assert re.search(r"^MIN_RAM_CPU_MIB=9500\b", sh, re.MULTILINE)
    body = sh_function(sh, "check_memory")
    assert "/ 1024 / 1024" in body
    assert '[ "$mem_mib" -lt "$MIN_RAM_CPU_MIB" ]' in body


def test_start_ps1_compares_memory_with_9500_mib(ps1: str) -> None:
    assert re.search(r"^\$MinRamCpuMib = 9500\b", ps1, re.MULTILINE)
    body = ps1_function(ps1, "Test-Memory")
    assert "/ 1MB" in body
    assert "$mib -ge $MinRamCpuMib" in body


def test_start_sh_checks_memory_and_ports_before_building(sh: str) -> None:
    flow = main_flow(sh)
    assert_order(flow, r"\bcheck_memory\b", r"\bcompose build\b")
    assert_order(flow, r"\bcheck_ports\b", r"\bcompose build\b")


def test_start_ps1_checks_memory_and_ports_before_building(ps1: str) -> None:
    flow = main_flow(ps1)
    assert_order(flow, r"\bTest-Memory\b", r"\bbuild api\b")
    assert_order(flow, r"\bTest-Ports\b", r"\bbuild api\b")


def test_start_sh_requires_curl(sh: str) -> None:
    assert re.search(r"command -v curl\b[^\n]*\|\|\s*die\b", sh_function(sh, "ensure_docker"))


def test_start_sh_amd_preflight_runs_through_compose(sh: str) -> None:
    body = sh_function(sh, "gpu_preflight")
    amd = re.search(r"^\s*amd\)\n(.*?)^\s*;;|^\s*amd\)\n(.*?);;", body, re.MULTILINE | re.DOTALL)
    assert amd, body
    branch = amd.group(1) or amd.group(2)
    assert re.search(r"^\s*compose run\b[^\n]*\bapi\b", branch, re.MULTILINE), branch
    assert "docker run" not in branch


def test_amd_overlay_adds_the_groups_the_preflight_relies_on() -> None:
    assert yaml.safe_load(read("docker/compose.amd.yml"))["services"]["api"]["group_add"]


def test_start_sh_low_vram_nvidia_drops_bitsandbytes(sh: str) -> None:
    assert re.search(r"^MIN_VRAM_MB=4500\b", sh, re.MULTILINE)
    body = sh_function(sh, "detect_hardware")
    low = re.search(r'if \[ "\$vram" -lt "\$MIN_VRAM_MB" \]; then\n(.*?)^\s*fi\b', body, re.MULTILINE | re.DOTALL)
    assert low, body
    assert re.search(r"\bINSTALL_BNB=0\b", low.group(1))
    assert re.search(r"\bMODEL_QUANTIZATION=none\b", low.group(1))
    assert body.index("INSTALL_BNB=1") < low.start(), "the NVIDIA defaults must not overwrite the low-VRAM values"


def test_start_ps1_low_vram_nvidia_drops_bitsandbytes(ps1: str) -> None:
    assert re.search(r"^\$MinVramMb = 4500\b", ps1, re.MULTILINE)
    body = ps1_function(ps1, "Get-Hardware")
    low = re.search(r"if \(\[int\]\$vram -lt \$MinVramMb\) \{\n(.*?)^\s*\}", body, re.MULTILINE | re.DOTALL)
    assert low, body
    assert re.search(r"\$hw\.Bnb = 0\b", low.group(1))
    assert '$hw.Quant = "none"' in low.group(1)
    assert body.index("$hw.Bnb = 1") < low.start(), "the NVIDIA defaults must not overwrite the low-VRAM values"
    assert not re.search(r"\$hw\.(Bnb|Quant) =", body[low.end() :])


# --------------------------------------------------------------------------- 7. GET /


def test_page_clears_the_waiting_text_when_the_request_fails(make_app) -> None:
    app, _ = make_app()
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    run = response.text.split("async function run()", 1)[1]
    branch = re.search(r"\}\s*catch\s*\(\w+\)\s*\{(.*?)\}\s*finally\b", run, re.DOTALL)
    assert branch, "the search function has no catch branch"
    assert re.search(r"""\$\("meta"\)\.textContent\s*=\s*(""|'')\s*;""", branch.group(1)), branch.group(1)
