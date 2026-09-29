---
description: Lint + fast test suite (no model) and a short summary
---
Run from the repo root using the project venv:

1. `.venv/Scripts/python.exe -m ruff check app tests`
2. `.venv/Scripts/python.exe -m pytest -m "not model" -q`

Report: ruff result, pytest summary line (passed/failed/skipped, duration), and any failures with file:line.
Verdict: PASS only if ruff is clean and pytest has 0 failures and ran < 60 s.
