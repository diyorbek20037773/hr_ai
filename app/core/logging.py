"""Structured JSON logging with PII redaction (CLAUDE.md §3.9).

Every record passes through `RedactionFilter` before output: 14-digit runs -> ***PINFL***,
standalone 9-digit runs -> ***INN***. Raw query text must only be logged at DEBUG; INFO uses
`query_fingerprint()` (length + sha256 prefix).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_PINFL_RE = re.compile(r"(?<!\d)\d{14}(?!\d)")
_INN_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")

# Attributes every LogRecord has; anything else passed via `extra=` is emitted as a JSON field.
_STD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _INN_RE.sub("***INN***", _PINFL_RE.sub("***PINFL***", value))
    if isinstance(value, int) and not isinstance(value, bool):
        s = str(value)
        return redact(s) if s != redact(s) else value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def query_fingerprint(query: str) -> dict[str, Any]:
    return {"queryLength": len(query), "queryHash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]}


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        for key, val in list(vars(record).items()):
            if key not in _STD_ATTRS:
                setattr(record, key, redact(val))
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "requestId"):
            record.requestId = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "requestId": getattr(record, "requestId", "-"),
            "event": record.getMessage(),
        }
        for key, val in vars(record).items():
            if key not in _STD_ATTRS and key not in payload:
                payload[key] = val
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs", to_file: bool = True) -> None:
    """Idempotent. Console + rotating file (10 MB x 5), JSON lines, redaction on every handler."""
    global _configured
    root = logging.getLogger()
    root.setLevel(level.upper())
    if _configured:
        return
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if to_file:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(path / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5,
                                            encoding="utf-8"))
    for h in handlers:
        h.setFormatter(formatter)
        h.addFilter(RequestIdFilter())
        h.addFilter(RedactionFilter())
        root.addHandler(h)
    # uvicorn's access log would print raw paths/query strings; our middleware logs requests instead.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "httpx2", "httpcore2", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
