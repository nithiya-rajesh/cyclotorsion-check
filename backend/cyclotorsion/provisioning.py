"""Facility/admin account approval store (PRD Section 8.2, TDD Section 5.2).

Replaces the R11 (Section 7) *manual allow-listing* workaround with a real,
programmatic facility-admin account-approval interface.

A user's "approval" is represented by the presence of a valid ``role`` custom
claim on their Firebase account (plus an optional ``facility_id``). Enforcement
happens in ``auth.current_approved_user``: a verified caller without an
approved role is rejected with 403 "account pending approval".

This module provides the store behind the admin endpoints (list pending/known
users, approve a user by assigning role + facility claims):

  - ``InMemoryApprovalStore``: local/dev fallback and tests. A small registry
    simulating accounts that are provisioned (pending) and later approved.
  - ``FirebaseApprovalStore``: production. Lists Firebase accounts and their
    custom claims, and writes role/facility claims to approve a user. Lazily
    imports the Firebase Admin SDK so the module is importable without creds —
    the same lazy/optional-cloud pattern as ``BigQueryWriter``.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import HTTPException, status

from cyclotorsion.auth import (
    APPROVED_ROLES,
    ROLE_FACILITY_ADMIN,
    UserContext,
)
from cyclotorsion.config import cfg

logger = logging.getLogger("cyclotorsion.provisioning")


@dataclass(frozen=True)
class UserRecord:
    """A provisioned account with its approval-relevant claims."""

    uid: str
    email: str = ""
    role: str | None = None
    facility_id: str | None = None
    approved: bool = False

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "email": self.email,
            "role": self.role,
            "facility_id": self.facility_id,
            "approved": self.approved,
        }


def _ensure_valid_role(role: str) -> str:
    if role not in APPROVED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown role '{role}'. Valid roles: {', '.join(sorted(APPROVED_ROLES))}",
        )
    return role


# Firebase custom-claim attributes are stored as arbitrary claim strings. We
# bound the shape of a UID (base64url-ish, Firebase issues <=128 chars) and a
# facility id (DNS-label-ish) to reject malformed/oversized input up front.
_MAX_UID_CHARS = 128
_FACILITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _validate_uid(uid: str) -> str:
    if not isinstance(uid, str) or not uid or len(uid) > _MAX_UID_CHARS:
        raise HTTPException(
            status_code=400, detail="'uid' is required and <= 128 chars"
        )
    if any(c.isspace() for c in uid):
        raise HTTPException(status_code=400, detail="'uid' must not contain whitespace")
    return uid


def _validate_facility_id(facility_id: str | None) -> str | None:
    if facility_id is None or facility_id == "":
        return None
    if not _FACILITY_ID_RE.match(facility_id):
        raise HTTPException(
            status_code=400,
            detail="'facility_id' must be a simple identifier (letters, digits, . _ -)",
        )
    return facility_id


def _scope_facility(user: UserContext, facility_id: str | None) -> str | None:
    """Constrain an approver to their own facility unless they are program-level.

    A ``facility_admin`` may only approve accounts at their own facility; a
    ``program_officer`` may approve anywhere.

    Security (HIGH-2 fix): a ``facility_admin`` whose own account carries no
    ``facility_id`` claim is REFUSED (fail closed) rather than allowed to
    approve at a caller-supplied facility. The verified claim is always the
    authoritative scope — a caller-supplied ``facility_id`` is never trusted for
    a facility admin.
    """
    if cfg.auth_enabled and user.role == ROLE_FACILITY_ADMIN:
        if not user.facility_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account has no facility; contact a program officer to approve users",
            )
        if facility_id != user.facility_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Facility admins may only approve accounts for their own facility",
            )
        return user.facility_id
    return facility_id


class ApprovalStore(ABC):
    @abstractmethod
    def list_users(self) -> list[UserRecord]:
        """Return all known accounts with their approval status."""

    @abstractmethod
    def approve(
        self, uid: str, role: str, facility_id: str | None = None
    ) -> UserRecord:
        """Assign ``role`` (+ optionally ``facility_id``) claims, approving the user."""

    def register_pending(self, uid: str, email: str = "") -> None:
        """Record an account awaiting approval (dev/tests). No-op by default."""
        raise NotImplementedError


class InMemoryApprovalStore(ApprovalStore):
    """Local/dev backend; a registry of known accounts."""

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}

    def register_pending(self, uid: str, email: str = "") -> None:
        self._users.setdefault(uid, UserRecord(uid=uid, email=email))

    def list_users(self) -> list[UserRecord]:
        return list(self._users.values())

    def approve(
        self, uid: str, role: str, facility_id: str | None = None
    ) -> UserRecord:
        role = _ensure_valid_role(role)
        record = self._users.get(uid)
        if record is None:
            record = UserRecord(uid=uid, email="")
        approved = UserRecord(
            uid=record.uid,
            email=record.email,
            role=role,
            facility_id=facility_id,
            approved=True,
        )
        self._users[uid] = approved
        return approved


class FirebaseApprovalStore(ApprovalStore):
    """Production backend backed by Firebase Authentication + custom claims."""

    def _auth(self):
        from cyclotorsion.auth import _ensure_firebase_app

        _ensure_firebase_app()
        from firebase_admin import auth

        return auth

    def list_users(self) -> list[UserRecord]:
        auth = self._auth()
        records: list[UserRecord] = []
        for page in auth.list_users().iterate_all():
            for user in page.users:
                claims = user.custom_claims or {}
                role = claims.get("role")
                site = claims.get("facility_id")
                records.append(
                    UserRecord(
                        uid=user.uid,
                        email=user.email or "",
                        role=role,
                        facility_id=site,
                        approved=role in APPROVED_ROLES,
                    )
                )
        return records

    def approve(
        self, uid: str, role: str, facility_id: str | None = None
    ) -> UserRecord:
        role = _ensure_valid_role(role)
        auth = self._auth()
        claims = {"role": role}
        if facility_id:
            claims["facility_id"] = facility_id
        auth.set_custom_user_claims(uid, claims)
        return UserRecord(uid=uid, role=role, facility_id=facility_id, approved=True)


_store: ApprovalStore | None = None


def approval_store_from_config() -> ApprovalStore:
    """Build the approval store from config. Options:

    - CC_PROVISIONING_MODE=memory (default, dev/tests) -> InMemoryApprovalStore
    - CC_PROVISIONING_MODE=firebase (prod) -> FirebaseApprovalStore
    - CC_PROVISIONING_MODE=auto -> Firebase when an SDK is present, else memory
    """
    global _store
    if _store is not None:
        return _store
    mode = (cfg.provisioning_mode or "memory").lower()
    if mode == "firebase":
        _store = FirebaseApprovalStore()
    elif mode == "auto":
        try:
            import firebase_admin  # noqa: F401

            _store = FirebaseApprovalStore()
        except Exception:  # noqa: BLE001
            _store = InMemoryApprovalStore()
    else:
        _store = InMemoryApprovalStore()
    return _store


def get_approval_store() -> ApprovalStore:
    return approval_store_from_config()


def reset_approval_store() -> None:
    global _store
    _store = None
