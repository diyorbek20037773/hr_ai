import hashlib
import io
import json
import logging

import pytest

from app.core.logging import JsonFormatter, RedactionFilter, RequestIdFilter, query_fingerprint, redact

PINFL = "31234567890123"
INN = "305123456"


@pytest.fixture
def capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    handler.addFilter(RequestIdFilter())
    logger = logging.getLogger("hr_ai.tests.redaction")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, stream
    logger.handlers = []


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_redact_function():
    assert redact(f"pinfl {PINFL} ok") == "pinfl ***PINFL*** ok"
    assert redact(f"inn {INN}") == "inn ***INN***"
    assert "1234567890" in redact("ten 1234567890 digits")  # not a standalone 9- or 14-digit run


def test_pinfl_redacted_in_message(capture):
    logger, stream = capture
    logger.info(f"searching {PINFL}")
    out = stream.getvalue()
    assert PINFL not in out
    assert "***PINFL***" in out


def test_pinfl_redacted_in_args_and_extra(capture):
    logger, stream = capture
    logger.warning("user %s inn %s", PINFL, INN, extra={"pinfl": PINFL, "inn": INN})
    out = stream.getvalue()
    assert PINFL not in out
    assert INN not in out
    assert "***PINFL***" in out
    assert "***INN***" in out


def test_inn_redacted(capture):
    logger, stream = capture
    logger.info(f"institution inn={INN}")
    out = stream.getvalue()
    assert INN not in out
    assert "***INN***" in out


def test_json_line_format(capture):
    logger, stream = capture
    logger.info("hello_event")
    lines = _lines(stream)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["level"] == "INFO"
    assert "ts" in rec
    assert rec["logger"] == "hr_ai.tests.redaction"


def test_query_fingerprint_hides_query(capture):
    logger, stream = capture
    query = "Familiyasi Karimov, ismi Ali"
    fp = query_fingerprint(query)
    assert fp["queryLength"] == len(query)
    assert fp["queryHash"] == hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    logger.info("parse_request", extra=fp)
    out = stream.getvalue()
    assert "Karimov" not in out
    assert query not in out
    rec = _lines(stream)[0]
    assert rec["queryLength"] == len(query)
    assert rec["queryHash"] == fp["queryHash"]
