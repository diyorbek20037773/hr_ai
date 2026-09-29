"""PostToolUse hook: run `ruff check` on the edited .py file. Reads the hook JSON from stdin.

Exit code 2 + stderr feeds lint errors back to Claude; non-Python files are ignored.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    target = Path(path)
    try:
        target.resolve().relative_to(ROOT)
    except ValueError:
        return 0  # outside the repo (e.g. scratchpad)
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", "--quiet", str(target)],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
