---
description: Full release gate — qa + eval + ops-check, then conventional commit and tag
argument-hint: <version e.g. v0.1.1>
---
1. Run the /qa steps. Stop if not PASS.
2. If anything under `app/prompts/` or `app/services/llm_service.py` changed since the last tag, run `.venv/Scripts/python.exe -m pytest -m model -q` and the /eval steps. Stop if targets in CLAUDE.md §7 are not met (report the gap; do not edit expectations).
3. Run the /ops-check steps. Stop if not OK.
4. `git status` must show only intended changes; `model/ .venv/ logs/ .env` must not be staged.
5. Commit with a conventional message and tag `$ARGUMENTS` (`git tag -a $ARGUMENTS -m "$ARGUMENTS"`). Never push unless a remote was explicitly given.
