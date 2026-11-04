"""Install-time model download (run by the `model-init` container), never used by the API.

    python -m app.scripts.fetch_model

The API itself stays offline: it reads MODEL_PATH with local_files_only=True (CLAUDE.md §3.1). This one-shot step
fills that directory from the Hugging Face Hub, at a pinned revision so every machine runs the same weights the eval
was measured on. A complete directory is left untouched, and a read-only one (the repo's own ./model) is never
written to: if it is incomplete the step fails and reports the missing files.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import time
from pathlib import Path

from app.services.llm_service import ModelNotFoundError, verify_model_files

DEFAULT_REPO = "Qwen/Qwen3-4B-Instruct-2507"
DEFAULT_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
REQUIRED_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json")
INDEX_FILE = "model.safetensors.index.json"
ALLOW_PATTERNS = ["*.safetensors", "*.json", "*.txt", "LICENSE", "README.md"]
MAX_ATTEMPTS = 5
MODEL_BYTES = 8_100_000_000


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


def download(path: Path, repo: str, revision: str, attempts: int = MAX_ATTEMPTS) -> None:
    """Resumable: partial files stay on the volume, so a retry or a re-run continues where it stopped."""
    from huggingface_hub import snapshot_download

    for attempt in range(1, attempts + 1):
        try:
            snapshot_download(repo_id=repo, revision=revision, local_dir=path, allow_patterns=ALLOW_PATTERNS)
            return
        except Exception as exc:  # noqa: BLE001 — network errors have many types; the last one is re-raised
            if attempt == attempts or (isinstance(exc, OSError) and exc.errno == errno.ENOSPC):
                raise
            delay = min(60, 5 * 2 ** (attempt - 1))
            print(f"Download attempt {attempt}/{attempts} failed ({type(exc).__name__}: {exc}); retry in {delay}s",
                  flush=True)
            time.sleep(delay)


def has_disk_space(path: Path) -> bool:
    present = sum(f.stat().st_size for f in path.glob("*.safetensors"))
    free = shutil.disk_usage(path).free
    return free >= MODEL_BYTES - present


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
    if not has_disk_space(path):
        print(f"Not enough free disk for the model (about {MODEL_BYTES / 1e9:.0f} GB is needed). Free some space "
              "or enlarge Docker's disk, then run the start command again.", file=sys.stderr)
        return 1

    print(f"Downloading {repo}@{revision[:12]} to {path} (about 8 GB, resumable)...", flush=True)
    try:
        download(path, repo, revision)
    except Exception as exc:  # noqa: BLE001 — any failure here has the same remedy
        print(f"Model download failed ({type(exc).__name__}: {exc}). Check the internet connection and run the start "
              "command again: the download resumes where it stopped.", file=sys.stderr)
        return 1
    complete, detail = is_complete(path)
    if not complete:
        print(f"Download finished but the model is incomplete: {detail}", file=sys.stderr)
        return 1
    print("Model download complete.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
