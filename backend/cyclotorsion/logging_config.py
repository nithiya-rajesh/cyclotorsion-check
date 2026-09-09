"""Structured JSON logging with request_id correlation (TDD Section 6.2).

Addresses the TDD gap: the codebase previously scattered ``print()``/plain
``logging`` output. This module:

  - Provides a ``JsonFormatter`` that serializes every log record to a single
    line of JSON, which Cloud Logging can natively parse and index.
  - Generates a ``request_id`` (UUID) at the start of each request and threads
    it through every log line of that request's lifecycle (a
    ``contextvars.ContextVar`` read by a ``logging.Filter``). This makes the
    full decision trail of a single clinical test reconstructable from logs
    alone — the TDD's stated motivation (a surgeon questioning a result should
    yield a complete, searchable record).
  - Is idempotent and dependency-free (stdlib ``logging``, ``json``,
    ``contextvars``, ``uuid``). No third-party logger added.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SERVICE_NAME = "cyclotorsion-check"

# The per-request correlation id, propagated via contextvars so any logging
# call made within a request's task carries the same id.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def current_request_id() -> str:
    """Return the request_id for the current context ("" outside a request)."""
    return request_id_var.get()


def new_request_id() -> str:
    return uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON for Cloud Logging."""

    _FIELDS = (
        "asctime",
        "levelname",
        "name",
        "message",
    )
    _EXTRA_FIELDS = ("request_id", "service", "event")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", None) or SERVICE_NAME,
            "message": record.getMessage(),
        }
        # Surface any structured extra fields attached to the record.
        for field in self._EXTRA_FIELDS:
            if field == "service":
                continue  # handled above (always present)
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        # Include the request_id via the record (set by RequestContextFilter).
        rid = getattr(record, "request_id", None) or request_id_var.get()
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class RequestContextFilter(logging.Filter):
    """Attach the current request_id (+service) to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        record.service = SERVICE_NAME
        return True


def configure_logging(level: str = "INFO") -> None:
    """Apply the JSON formatter + request-context filter to the app logger.

    Idempotent: guards against double-installation on reload.
    """
    root = logging.getLogger("cyclotorsion")
    root.setLevel(level.upper())
    handler = _json_handler()
    # Avoid stacking duplicate handlers on repeated calls (tests/reload).
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_cyclo_json", False)
        for h in root.handlers
    ):
        root.addHandler(handler)
    if not any(isinstance(f, RequestContextFilter) for f in root.filters):
        root.addFilter(RequestContextFilter())


def _json_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._cyclo_json = True  # type: ignore[attr-defined]
    return handler


class RequestIdMiddleware(BaseHTTPMiddleware):
    """ASGI middleware assigning a request_id to each incoming request.

    The id is stored in ``request_id_var`` for the duration of the request so
    that the RequestContextFilter threads it into every log line emitted while
    serving that request. The id is also echoed back in a response header so
    callers can correlate a user-facing failure with its server log trail.
    """

    async def dispatch(self, request: Request, call_next):
        rid = new_request_id()
        token = request_id_var.set(rid)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-Id"] = rid
            return response
        finally:
            request_id_var.reset(token)
