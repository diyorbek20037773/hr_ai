"""Install-time model download (run by the `model-init` container), never used by the API.

    python -m app.scripts.fetch_model

The API itself stays offline: it reads MODEL_PATH with local_files_only=True (CLAUDE.md §3.1). This one-shot step
fills that directory from the Hugging Face Hub, at a pinned revision so every machine runs the same weights the eval
was measured on. A complete directory is left untouched, and a read-only one (the repo's own ./model) is never
written to: if it is incomplete the step fails and reports the missing files.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

import httpx

from app.services.llm_service import ModelNotFoundError, verify_model_files

DEFAULT_REPO = "Qwen/Qwen3-4B-Instruct-2507"
DEFAULT_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
REQUIRED_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json")
INDEX_FILE = "model.safetensors.index.json"
ALLOW_PATTERNS = ["*.safetensors", "*.json", "*.txt", "LICENSE", "README.md"]
MAX_ATTEMPTS = 10  # with the 60 s cap: about 6 minutes without any progress before giving up
MODEL_BYTES = 8_100_000_000
CHUNK_BYTES = 1024 * 1024
READ_TIMEOUT_SECONDS = 60
REPORT_SECONDS = 30


def is_complete(path: Path) -> tuple[bool, str]:
    missing = [f for f in REQUIRED_FILES if not (path / f).is_file()]
    if missing:
        return False, f"missing: {', '.join(missing)}"
    index = path / INDEX_FILE
    if index.is_file():
        # An interrupted or corrupt index must trigger a re-download, and an empty one must not pass as "no shards".
        try:
            weight_map = json.loads(index.read_text(encoding="utf-8")).get("weight_map")
        except (ValueError, AttributeError) as exc:
            return False, f"{INDEX_FILE} is unreadable: {exc}"
        if not weight_map:
            return False, f"{INDEX_FILE} lists no weight files"
    try:
        verify_model_files(path)
    except ModelNotFoundError as exc:
        return False, str(exc)
    return True, "complete"


def list_files(repo: str, revision: str) -> list[tuple[str, int, str | None]]:
    """(name, size, sha256 or None) of the wanted files. LFS files (the weights) carry a sha256."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, revision=revision, files_metadata=True)
    return [(s.rfilename, s.size, s.lfs.sha256 if s.lfs else None) for s in info.siblings
            if any(fnmatch(s.rfilename, pattern) for pattern in ALLOW_PATTERNS)]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_file(client: httpx.Client, url: str, dest: Path, size: int, sha256: str | None) -> None:
    """Append to <dest>.part from the byte it stopped at, then verify and move it into place.

    huggingface_hub restarts a file from zero after every interruption, which on a slow line or a laptop that sleeps
    means the 8 GB model may never finish. Plain HTTP Range requests survive both.
    """
    if dest.is_file() and dest.stat().st_size == size:
        return
    part = dest.with_name(dest.name + ".part")
    part.touch()
    done = part.stat().st_size
    if done > size:
        part.write_bytes(b"")
        done = 0
    if done < size:
        # identity: byte offsets must be file offsets, so no transfer compression on resume.
        range_headers = {"Range": f"bytes={done}-", "Accept-Encoding": "identity"}
        with client.stream("GET", url, headers=range_headers) as response:
            response.raise_for_status()
            mode = "ab" if response.status_code == 206 else "wb"  # 200: the server ignored the range
            done = done if mode == "ab" else 0
            last_report = time.monotonic()
            with part.open(mode) as f:
                # iter_raw() without a size: a sized iterator holds back what arrived before a dropped connection.
                for chunk in response.iter_raw():
                    f.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last_report >= REPORT_SECONDS:
                        last_report = time.monotonic()
                        print(f"  {dest.name}: {done / 1e9:.2f} / {size / 1e9:.2f} GB", flush=True)
    if part.stat().st_size != size:
        raise OSError(f"{dest.name}: got {part.stat().st_size} bytes, expected {size}")
    if sha256 and sha256_of(part) != sha256:
        part.unlink()
        raise OSError(f"{dest.name}: checksum mismatch, the file will be downloaded again")
    part.replace(dest)


def bytes_on_disk(path: Path) -> int:
    return sum(f.stat().st_size for pattern in ("*.safetensors", "*.part") for f in path.glob(pattern))


def progress_marker(path: Path) -> int:
    return sum(f.stat().st_size for f in path.iterdir() if f.is_file())


def download(path: Path, repo: str, revision: str, attempts: int = MAX_ATTEMPTS) -> None:
    """Resumable. `attempts` counts consecutive failures without progress, so a flaky line keeps moving forward."""
    endpoint = (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    token = os.environ.get("HF_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    failures = 0
    while True:
        before = progress_marker(path)
        try:
            timeout = httpx.Timeout(30, read=READ_TIMEOUT_SECONDS)
            with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
                for name, size, sha256 in list_files(repo, revision):
                    dest = path / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    fetch_file(client, f"{endpoint}/{repo}/resolve/{revision}/{name}", dest, size, sha256)
            return
        except Exception as exc:  # noqa: BLE001 — network errors have many types; the last one is re-raised
            if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                raise
            failures = 1 if progress_marker(path) > before else failures + 1
            if failures >= attempts:
                raise
            delay = min(60, 5 * 2 ** (failures - 1))
            print(f"Download interrupted ({type(exc).__name__}: {exc}); retry {failures}/{attempts} in {delay}s",
                  flush=True)
            time.sleep(delay)


def has_disk_space(path: Path) -> bool:
    return shutil.disk_usage(path).free >= MODEL_BYTES - bytes_on_disk(path)


def main() -> int:
    path = Path(os.environ.get("MODEL_PATH") or "/models")
    repo = os.environ.get("HF_MODEL_REPO") or DEFAULT_REPO
    revision = os.environ.get("HF_MODEL_REVISION") or DEFAULT_REVISION

    complete, detail = is_complete(path)
    if complete:
        print(f"Model already present in {path}; nothing to download.", flush=True)
        return 0
    if os.environ.get("HR_AI_MODEL_READONLY") == "1":
        print(f"Model in {path} is incomplete and read-only, so it was not changed: {detail}\n"
              "Restore the files, or use the Hugging Face copy: ./start.sh --download-model "
              "(Windows: start.bat -DownloadModel).", file=sys.stderr)
        return 2
    path.mkdir(parents=True, exist_ok=True)
    if not has_disk_space(path):
        print(f"Not enough free disk for the model (about {MODEL_BYTES / 1e9:.0f} GB is needed). Free some space "
              "or enlarge Docker's disk, then run the start command again.", file=sys.stderr)
        return 1

    print(f"Downloading {repo}@{revision[:12]} to {path} (about 8 GB, resumable)...", flush=True)
    try:
        download(path, repo, revision)
    except Exception as exc:  # noqa: BLE001 — every failure ends in the same "fix it and re-run" advice
        no_space = getattr(exc, "errno", None) == errno.ENOSPC
        remedy = "Free some disk space" if no_space else "Check the internet connection"
        print(f"Model download failed ({type(exc).__name__}: {exc}). {remedy} and run the start command again: the "
              "download resumes where it stopped.", file=sys.stderr)
        return 1
    complete, detail = is_complete(path)
    if not complete:
        print(f"Download finished but the model is incomplete: {detail}", file=sys.stderr)
        return 1
    print("Model download complete.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
