"""``GET /stats``: aggregate, de-identified statistics (PRD Epic 4, US-4.2/4.3)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from cyclotorsion.app import get_cfg, get_writer
from cyclotorsion.auth import (
    ROLE_FACILITY_ADMIN,
    ROLE_PROGRAM_OFFICER,
    UserContext,
    current_approved_user,
)
from cyclotorsion.config import Config
from cyclotorsion.rate_limit import rate_limit
from cyclotorsion.storage import ResultWriter

logger = logging.getLogger("cyclotorsion.app")

router = APIRouter()


@router.get("/stats")
def stats(
    user: UserContext = Depends(current_approved_user),
    _rate: None = Depends(rate_limit),
    cfg: Config = Depends(get_cfg),
    writer: ResultWriter = Depends(get_writer),
) -> dict[str, Any]:
    """Aggregate, de-identified statistics (PRD Epic 4, US-4.2/4.3).

    No field here can identify a patient — only counts, averages, and pass
    rates over the persisted result rows.

    RBAC / facility scoping (TDD Section 5.2): a surgeon may not read stats at
    all; a ``facility_admin`` sees only their own ``facility_id``'s aggregate;
    a ``program_officer`` sees the across-all-facilities aggregate. In open/dev
    mode, all persisted rows are aggregated (unchanged local behavior).
    """
    # RBAC check BEFORE any storage I/O (staff review Major): an unauthorized
    # role must never trigger a BigQuery read. Checking after the fetch let an
    # approved-but-unauthorized caller (e.g. a surgeon) pay for a full
    # analytics query on every call before being rejected with 403.
    if cfg.auth_enabled and user.role not in (
        ROLE_FACILITY_ADMIN,
        ROLE_PROGRAM_OFFICER,
    ):
        raise HTTPException(
            status_code=403,
            detail="Requires facility_admin or program_officer role to view statistics",
        )
    rows, read_error = _visible_rows(user, cfg, writer)
    result: dict[str, Any] = {
        "total_tests": 0,
        "average_abs_angle_deg": None,
        "sanity_check_pass_rate": None,
    }
    if read_error is not None:
        # Distinguish "no data" from "analytics degraded" (architecture review
        # Major): a BigQuery read failure must not be silently misread as zero
        # tests. Surface a generic, internal-safe warning; the raw exception is
        # logged server-side only (MEDIUM-3 + resp_boundary).
        result["analytics_warning"] = "analytics read failed"
        logger.warning(
            "stats.read_failed",
            extra={
                "event": "stats.read_failed",
                "error_type": type(read_error).__name__,
            },
        )
    if rows:
        total = len(rows)
        avg_abs = sum(abs(r.angle_deg) for r in rows) / total
        passed = sum(1 for r in rows if r.passed_sanity_check)
        result.update(
            {
                "total_tests": total,
                "average_abs_angle_deg": round(avg_abs, 2),
                "sanity_check_pass_rate": round(passed / total, 4),
            }
        )
    if cfg.auth_enabled:
        result["scoped_by_facility_id"] = (
            user.facility_id if user.role == ROLE_FACILITY_ADMIN else None
        )
    return result


def _visible_rows(
    user: UserContext, cfg: Config, writer: ResultWriter
) -> tuple[list[Any], Exception | None]:
    """Rows this caller is authorized to aggregate (PRD Epic 4 / TDD 5.2).

    Delegates the read to the configured storage backend (in-memory or
    BigQuery) so ``/stats`` returns real aggregates in production, not just in
    dev. Returns ``(rows, error)``; a non-None error indicates the analytics
    read failed and ``/stats`` must surface it rather than report empty data.
    Open/dev mode (auth disabled): all rows. Auth enabled: ``facility_admin``
    is restricted to their own facility; ``program_officer`` sees all; other
    callers are denied by the RBAC check above.
    """
    facility_id = None
    if cfg.auth_enabled and user.role == ROLE_FACILITY_ADMIN:
        facility_id = user.facility_id
    return writer.read_rows(facility_id=facility_id)
