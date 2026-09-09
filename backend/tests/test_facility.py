"""Tests for facility-scoped /stats, RBAC, and the admin-approval flow (TDD 5.2 / PRD 8.2)."""

import dataclasses

import pytest
from fastapi import HTTPException

import cyclotorsion.app as app_module
import cyclotorsion.auth as auth
import cyclotorsion.provisioning as provisioning
from cyclotorsion.app import app
from cyclotorsion.auth import (
    ROLE_FACILITY_ADMIN,
    ROLE_PROGRAM_OFFICER,
    ROLE_SURGEON,
    UserContext,
    current_approved_user,
    current_user,
    require_any_role,
)
from cyclotorsion.provisioning import InMemoryApprovalStore
from cyclotorsion.rate_limit import rate_limit
from cyclotorsion.storage import ResultRow

FACILITY_A = "fac_a"
FACILITY_B = "fac_b"


def _row(angle: float, facility: str | None = None) -> ResultRow:
    return ResultRow(
        test_id="t",
        timestamp="2026-01-01T00:00:00+00:00",
        angle_deg=angle,
        upright_landmark="up",
        rotated_landmark="rot",
        passed_sanity_check=angle < 5,
        sanity_flags="" if angle < 5 else "large_angle",
        facility_id=facility,
        user_uid="u",
    )


@pytest.fixture(autouse=True)
def _auth_enabled(monkeypatch):
    """Enable auth for the whole module and isolate per-test state."""
    new_cfg = dataclasses.replace(auth.cfg, auth_enabled=True)
    monkeypatch.setattr(auth, "cfg", new_cfg)
    monkeypatch.setattr(provisioning, "cfg", new_cfg)
    monkeypatch.setattr(app_module, "cfg", new_cfg)
    app.dependency_overrides.clear()
    provisioning._store = None
    app_module._WRITER.rows = []
    yield
    app.dependency_overrides.clear()


def _seed(*rows):
    app_module._WRITER.rows = list(rows)


def _user(role, facility_id=None):
    return UserContext(uid="u-1", email="x@y.z", role=role, facility_id=facility_id)


def _client():
    from fastapi.testclient import TestClient

    return TestClient(app)


# --------------------------------------------------------------------------- #
# Facility-scoped /stats (approved callers)


def test_stats_facility_admin_sees_only_own_facility():
    _seed(_row(1.0, FACILITY_A), _row(2.0, FACILITY_A), _row(90.0, FACILITY_B))
    app.dependency_overrides[current_approved_user] = lambda: _user(
        ROLE_FACILITY_ADMIN, FACILITY_A
    )
    app.dependency_overrides[rate_limit] = lambda: None
    body = _client().get("/stats").json()
    assert body["total_tests"] == 2
    assert body["scoped_by_facility_id"] == FACILITY_A


def test_stats_program_officer_sees_all_facilities():
    _seed(_row(1.0, FACILITY_A), _row(90.0, FACILITY_B))
    app.dependency_overrides[current_approved_user] = lambda: _user(
        ROLE_PROGRAM_OFFICER
    )
    app.dependency_overrides[rate_limit] = lambda: None
    body = _client().get("/stats").json()
    assert body["total_tests"] == 2
    assert body["scoped_by_facility_id"] is None


def test_stats_surgeon_denied():
    _seed(_row(1.0, FACILITY_A))
    app.dependency_overrides[current_approved_user] = lambda: _user(ROLE_SURGEON)
    app.dependency_overrides[rate_limit] = lambda: None
    resp = _client().get("/stats")
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# current_approved_user gate (headless, calls the dependency directly)


def test_current_approved_user_rejects_pending(monkeypatch):
    # current_approved_user now takes a UserContext (via Depends(current_user));
    # call it directly with a pending-approval user.
    pending_user = UserContext(uid="p", role=None)
    with pytest.raises(HTTPException) as ei:
        auth.current_approved_user(pending_user)
    assert ei.value.status_code == 403
    assert "pending approval" in ei.value.detail


def test_current_approved_user_allows_approved():
    approved_user = UserContext(uid="u", role=ROLE_PROGRAM_OFFICER)
    assert auth.current_approved_user(approved_user) is not None


def test_require_any_role_rejects_wrong_role():
    dep = require_any_role(ROLE_PROGRAM_OFFICER)
    with pytest.raises(HTTPException) as ei:
        dep(_user(ROLE_SURGEON))
    assert ei.value.status_code == 403


def test_require_any_role_allows():
    dep = require_any_role(ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER)
    user = dep(_user(ROLE_PROGRAM_OFFICER))
    assert user.role == ROLE_PROGRAM_OFFICER


# --------------------------------------------------------------------------- #
# Admin-approval flow (HTTP, via current_user override -> require_any_role)


def _store() -> InMemoryApprovalStore:
    store = provisioning.get_approval_store()
    assert isinstance(store, InMemoryApprovalStore)
    return store


def test_admin_list_and_approve_flow():
    _store().register_pending("uid-pending", "dr@fac-a")
    app.dependency_overrides[current_user] = lambda: _user(ROLE_PROGRAM_OFFICER)

    before = _client().get("/admin/users").json()
    assert before[0]["approved"] is False

    resp = _client().post(
        "/admin/approve",
        json={"uid": "uid-pending", "role": ROLE_SURGEON, "facility_id": FACILITY_A},
    )
    assert resp.status_code == 200
    assert resp.json()["approved"] is True
    assert resp.json()["role"] == ROLE_SURGEON

    after = _client().get("/admin/users").json()
    assert after[0]["approved"] is True


def test_facility_admin_approval_scoped_to_own_facility():
    _store().register_pending("uid-pending")
    app.dependency_overrides[current_user] = lambda: _user(
        ROLE_FACILITY_ADMIN, FACILITY_A
    )

    ok = _client().post(
        "/admin/approve",
        json={"uid": "uid-pending", "role": ROLE_SURGEON, "facility_id": FACILITY_A},
    )
    assert ok.status_code == 200
    assert ok.json()["facility_id"] == FACILITY_A

    denied = _client().post(
        "/admin/approve",
        json={"uid": "uid-other", "role": ROLE_SURGEON, "facility_id": FACILITY_B},
    )
    assert denied.status_code == 403


def test_facility_admin_without_claim_cannot_approve_anywhere():
    # HIGH-2: a facility_admin whose own account carries no facility_id claim is
    # refused (fail closed), even if they supply a facility_id.
    _store().register_pending("uid-pending")
    app.dependency_overrides[current_user] = lambda: _user(ROLE_FACILITY_ADMIN)
    resp = _client().post(
        "/admin/approve",
        json={"uid": "uid-pending", "role": ROLE_SURGEON, "facility_id": FACILITY_B},
    )
    assert resp.status_code == 403


def test_admin_users_facility_admin_scoped_to_own_facility():
    # HIGH-2: a facility_admin only sees their own facility's accounts.
    _store().approve("a-own", ROLE_SURGEON, FACILITY_A)
    _store().approve("b-other", ROLE_SURGEON, FACILITY_B)
    _store().approve("a-pending", ROLE_SURGEON, FACILITY_A)
    app.dependency_overrides[current_user] = lambda: _user(
        ROLE_FACILITY_ADMIN, FACILITY_A
    )
    users = _client().get("/admin/users").json()
    ids = {u["uid"] for u in users}
    assert "a-own" in ids and "a-pending" in ids
    assert "b-other" not in ids


def test_admin_users_program_officer_sees_all():
    _store().approve("a-own", ROLE_SURGEON, FACILITY_A)
    _store().approve("b-other", ROLE_SURGEON, FACILITY_B)
    app.dependency_overrides[current_user] = lambda: _user(ROLE_PROGRAM_OFFICER)
    users = _client().get("/admin/users").json()
    assert {u["uid"] for u in users} == {"a-own", "b-other"}


def test_admin_approve_rejects_invalid_uid_whitespace():
    app.dependency_overrides[current_user] = lambda: _user(ROLE_PROGRAM_OFFICER)
    resp = _client().post(
        "/admin/approve", json={"uid": "has space", "role": ROLE_SURGEON}
    )
    assert resp.status_code == 400


def test_admin_approve_rejects_invalid_facility_id():
    app.dependency_overrides[current_user] = lambda: _user(ROLE_PROGRAM_OFFICER)
    resp = _client().post(
        "/admin/approve",
        json={"uid": "u", "role": ROLE_SURGEON, "facility_id": "bad facility!"},
    )
    assert resp.status_code == 400


def test_admin_approve_invalid_role():
    _store().register_pending("uid-pending")
    app.dependency_overrides[current_user] = lambda: _user(ROLE_PROGRAM_OFFICER)
    resp = _client().post(
        "/admin/approve", json={"uid": "uid-pending", "role": "nonexistent"}
    )
    assert resp.status_code == 400


def test_admin_approve_requires_role():
    app.dependency_overrides[current_user] = lambda: _user(ROLE_SURGEON)
    resp = _client().post("/admin/approve", json={"uid": "u", "role": ROLE_SURGEON})
    assert resp.status_code == 403
