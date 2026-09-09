"""``/admin/*``: facility-admin / program-officer approval flow (PRD 8.2)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from cyclotorsion.app import get_cfg
from cyclotorsion.auth import (
    ROLE_FACILITY_ADMIN,
    ROLE_PROGRAM_OFFICER,
    UserContext,
    require_any_role,
)
from cyclotorsion.config import Config
from cyclotorsion.provisioning import (
    _ensure_valid_role,
    _scope_facility,
    _validate_facility_id,
    _validate_uid,
    get_approval_store,
)
from cyclotorsion.rate_limit import rate_limit

logger = logging.getLogger("cyclotorsion.app")

router = APIRouter()


@router.get("/admin/users")
def admin_list_users(
    user: UserContext = Depends(
        require_any_role(ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER)
    ),
    _rate: None = Depends(rate_limit),
    cfg: Config = Depends(get_cfg),
) -> list[dict[str, Any]]:
    """List known accounts with approval status (PRD 8.2 admin-approval flow).

    Requires a ``facility_admin`` or ``program_officer`` role when auth is
    enabled. A ``facility_admin`` sees ONLY their own facility's accounts
    (HIGH-2 fix); a ``program_officer`` sees all. Exposes only account metadata
    (uid, email, role, facility, approved) — never any patient data.
    """
    store = get_approval_store()
    records = store.list_users()
    if cfg.auth_enabled and user.role == ROLE_FACILITY_ADMIN:
        records = [r for r in records if r.facility_id == user.facility_id]
    return [record.to_dict() for record in records]


@router.post("/admin/approve")
def admin_approve(
    body: dict[str, Any],
    user: UserContext = Depends(
        require_any_role(ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER)
    ),
    _rate: None = Depends(rate_limit),
) -> dict[str, Any]:
    """Approve a user by assigning role (+ facility) custom claims.

    Requires ``facility_admin`` (their own facility only) or ``program_officer``
    (any facility). This is the PRD 8.2 admin-approval interface that replaces
    the R11 manual allow-list.
    """
    uid = body.get("uid")
    role = body.get("role")
    if not uid or not role:
        raise HTTPException(status_code=400, detail="'uid' and 'role' are required")
    role = _ensure_valid_role(role)
    uid = _validate_uid(uid)
    _validate_facility_id(body.get("facility_id"))
    facility_id = _scope_facility(user, body.get("facility_id"))
    record = get_approval_store().approve(uid, role, facility_id)
    logger.info(
        "admin.user_approved",
        extra={
            "event": "admin.user_approved",
            "uid": uid,
            "role": role,
            "facility_id": facility_id,
            "approved_by": user.uid,
        },
    )
    return record.to_dict()
