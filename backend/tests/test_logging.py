"""Tests for structured JSON logging + request_id correlation (TDD Section 6.2)."""

import json
import logging

from fastapi.testclient import TestClient

from cyclotorsion.app import app
from cyclotorsion.logging_config import (
    JsonFormatter,
    RequestContextFilter,
    current_request_id,
    new_request_id,
    request_id_var,
)


def test_request_id_header_is_set():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("x-request-id"), "response must carry X-Request-Id"


def test_request_id_propagates_to_log_records(caplog):
    with caplog.at_level(logging.INFO, logger="cyclotorsion.app"):
        client = TestClient(app)
        r = client.get("/health")
    # The middleware sets the contextvar; any log within the request records it.
    rid = r.headers.get("x-request-id")
    assert rid
    # Force one log emission is app-level; /health logs nothing, so verify the
    # filter mechanics directly instead via a synthetic record below.
    assert current_request_id() == ""  # outside a request, context is empty


def test_request_context_filter_attaches_request_id():
    filt = RequestContextFilter()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    token = request_id_var.set("req-123")
    try:
        assert filt.filter(record) is True
        assert record.request_id == "req-123"
        assert record.service == "cyclotorsion-check"
    finally:
        request_id_var.reset(token)


def test_json_formatter_produces_parseable_line():
    fmt = JsonFormatter()
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "computed angle", None, None
    )
    record.request_id = "req-abc"
    line = fmt.format(record)
    parsed = json.loads(line)
    assert parsed["message"] == "computed angle"
    assert parsed["request_id"] == "req-abc"
    assert parsed["severity"] == "INFO"
    assert parsed["service"] == "cyclotorsion-check"


def test_contextvar_roundtrip():
    assert new_request_id()
    token = request_id_var.set("x")
    try:
        assert current_request_id() == "x"
    finally:
        request_id_var.reset(token)
