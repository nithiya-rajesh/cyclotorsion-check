"""Open infra endpoints: liveness probe and Prometheus metrics scrape (TDD 5.5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from cyclotorsion.metrics import get_registry

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe. Remains open for uptime monitoring (TDD 5.5)."""
    return {"status": "ok", "service": "cyclotorsion-check"}


@router.get("/metrics")
def metrics_endpoint() -> PlainTextResponse:
    """Prometheus-format custom metrics for Cloud Monitoring scraping (TDD 6.1)."""
    return PlainTextResponse(
        get_registry().render(), media_type="text/plain; version=0.0.4"
    )
