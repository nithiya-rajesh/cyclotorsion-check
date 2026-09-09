"""Tests for the approval store (PRD 8.2 / TDD 5.2): in-memory + Firebase backends."""

import sys
import types

import pytest
from fastapi import HTTPException

from cyclotorsion import provisioning
from cyclotorsion.provisioning import (
    ApprovalStore,
    FirebaseApprovalStore,
    InMemoryApprovalStore,
    UserRecord,
    approval_store_from_config,
    reset_approval_store,
)


def _install_fake_firebase(monkeypatch, users=None, records=None):
    """Install fake `firebase_admin` + `firebase_admin.auth` into sys.modules.

    `users`: list of dicts to return from auth.list_users().iterate_all().
    """
    calls = {"set_claims": []}

    firebase_admin = types.ModuleType("firebase_admin")
    firebase_admin.initialize_app = lambda cred=None: "app"
    creds = types.ModuleType("firebase_admin.credentials")
    creds.Certificate = lambda path: f"cert:{path}"

    fb_auth = types.ModuleType("firebase_admin.auth")

    def list_users():
        page = types.SimpleNamespace(users=users or [])
        return types.SimpleNamespace(iterate_all=lambda: iter([page]))

    def set_custom_user_claims(uid, claims):
        calls["set_claims"].append((uid, claims))

    fb_auth.list_users = list_users
    fb_auth.set_custom_user_claims = set_custom_user_claims

    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", creds)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", fb_auth)
    return calls


# --------------------------------------------------------------------------- #
# InMemoryApprovalStore + ApprovalStore base


def test_in_memory_approve_unknown_uid_creates_record():
    store = InMemoryApprovalStore()
    rec = store.approve("new-uid", "surgeon", facility_id="fac_a")
    assert rec.uid == "new-uid"
    assert rec.role == "surgeon"
    assert rec.approved is True
    assert store.list_users()[0].facility_id == "fac_a"


def test_base_register_pending_raises_not_implemented():
    class _Concrete(ApprovalStore):
        def list_users(self):
            return []

        def approve(self, uid, role, facility_id=None):
            return UserRecord(
                uid=uid, role=role, facility_id=facility_id, approved=True
            )

    with pytest.raises(NotImplementedError):
        _Concrete().register_pending("u")


def test_in_memory_approve_invalid_role_rejected():
    store = InMemoryApprovalStore()
    with pytest.raises(HTTPException) as e:
        store.approve("u", "bogus")
    assert e.value.status_code == 400


def test_user_record_to_dict():
    rec = UserRecord(
        uid="u", email="a@b", role="surgeon", facility_id="f", approved=True
    )
    d = rec.to_dict()
    assert d == {
        "uid": "u",
        "email": "a@b",
        "role": "surgeon",
        "facility_id": "f",
        "approved": True,
    }


# --------------------------------------------------------------------------- #
# _scope_facility / input validation (HIGH-2, MEDIUM hardening)


def _cfg(**changes):
    import dataclasses

    from cyclotorsion.config import cfg

    return dataclasses.replace(cfg, **changes)


def test_scope_facility_facility_admin_own_facility(monkeypatch):
    from cyclotorsion.auth import UserContext

    monkeypatch.setattr(provisioning, "cfg", _cfg(auth_enabled=True))
    admin = UserContext(uid="a", role="facility_admin", facility_id="fac_a")
    assert provisioning._scope_facility(admin, "fac_a") == "fac_a"


def test_scope_facility_facility_admin_other_facility_denied(monkeypatch):
    from cyclotorsion.auth import UserContext

    monkeypatch.setattr(provisioning, "cfg", _cfg(auth_enabled=True))
    admin = UserContext(uid="a", role="facility_admin", facility_id="fac_a")
    with pytest.raises(HTTPException) as e:
        provisioning._scope_facility(admin, "fac_b")
    assert e.value.status_code == 403


def test_scope_facility_facility_admin_without_claim_denied(monkeypatch):
    # HIGH-2: a facility_admin without a facility_id claim must NOT be able to
    # approve at a caller-supplied facility (previously `or facility_id` let it).
    from cyclotorsion.auth import UserContext

    monkeypatch.setattr(provisioning, "cfg", _cfg(auth_enabled=True))
    admin = UserContext(uid="a", role="facility_admin")
    with pytest.raises(HTTPException) as e:
        provisioning._scope_facility(admin, "fac_b")
    assert e.value.status_code == 403


def test_scope_facility_program_officer_any_facility(monkeypatch):
    from cyclotorsion.auth import UserContext

    monkeypatch.setattr(provisioning, "cfg", _cfg(auth_enabled=True))
    po = UserContext(uid="p", role="program_officer")
    assert provisioning._scope_facility(po, "fac_x") == "fac_x"


def test_scope_facility_open_mode_returns_passthrough(monkeypatch):
    from cyclotorsion.auth import UserContext

    monkeypatch.setattr(provisioning, "cfg", _cfg(auth_enabled=False))
    user = UserContext(uid=None, role="facility_admin", facility_id=None)
    assert provisioning._scope_facility(user, "whatever") == "whatever"


def test_validate_uid_accepts_firebase_style():
    assert provisioning._validate_uid("abcDEF123-_") == "abcDEF123-_"


def test_validate_uid_rejects_whitespace():
    with pytest.raises(HTTPException) as e:
        provisioning._validate_uid("has space")
    assert e.value.status_code == 400


def test_validate_facility_id_accepts_valid():
    assert provisioning._validate_facility_id("facility_a-1") == "facility_a-1"
    assert provisioning._validate_facility_id(None) is None
    assert provisioning._validate_facility_id("") is None


def test_validate_facility_id_rejects_malformed():
    with pytest.raises(HTTPException) as e:
        provisioning._validate_facility_id("bad facility! x")
    assert e.value.status_code == 400


# --------------------------------------------------------------------------- #
# FirebaseApprovalStore (live SDK mocked)


def test_firebase_list_users_maps_claims(monkeypatch):
    users = [
        types.SimpleNamespace(
            uid="u1",
            email="a@b.c",
            custom_claims={"role": "surgeon", "facility_id": "fac_a"},
        ),
        types.SimpleNamespace(uid="u2", email="", custom_claims=None),
    ]
    _install_fake_firebase(monkeypatch, users=users)
    store = FirebaseApprovalStore()
    records = store.list_users()
    assert len(records) == 2
    assert records[0].role == "surgeon"
    assert records[0].approved is True
    assert records[1].approved is False


def test_firebase_approve_sets_claims_with_facility(monkeypatch):
    calls = _install_fake_firebase(monkeypatch)
    store = FirebaseApprovalStore()
    rec = store.approve("u1", "facility_admin", facility_id="fac_b")
    assert calls["set_claims"] == [
        ("u1", {"role": "facility_admin", "facility_id": "fac_b"})
    ]
    assert rec.role == "facility_admin"
    assert rec.facility_id == "fac_b"
    assert rec.approved is True


def test_firebase_approve_sets_claims_without_facility(monkeypatch):
    calls = _install_fake_firebase(monkeypatch)
    store = FirebaseApprovalStore()
    store.approve("u1", "surgeon")
    assert calls["set_claims"] == [("u1", {"role": "surgeon"})]


def test_firebase_store_approve_invalid_role(monkeypatch):
    _install_fake_firebase(monkeypatch)
    store = FirebaseApprovalStore()
    with pytest.raises(HTTPException) as e:
        store.approve("u1", "nope")
    assert e.value.status_code == 400


# --------------------------------------------------------------------------- #
# Factory + reset


def test_factory_memory_mode():

    reset_approval_store()
    # Global config defaults to provisioning_mode="memory".
    store = approval_store_from_config()
    assert isinstance(store, InMemoryApprovalStore)
    reset_approval_store()


def test_factory_firebase_mode(monkeypatch):
    _install_fake_firebase(monkeypatch)
    monkeypatch.setattr(provisioning, "cfg", _cfg(provisioning_mode="firebase"))
    reset_approval_store()
    store = approval_store_from_config()
    assert isinstance(store, FirebaseApprovalStore)
    reset_approval_store()


def test_factory_auto_mode_uses_firebase_when_available(monkeypatch):
    _install_fake_firebase(monkeypatch)
    monkeypatch.setattr(provisioning, "cfg", _cfg(provisioning_mode="auto"))
    reset_approval_store()
    store = approval_store_from_config()
    assert isinstance(store, FirebaseApprovalStore)
    reset_approval_store()


def test_factory_auto_mode_falls_back_to_memory(monkeypatch):
    import builtins

    _install_fake_firebase(monkeypatch)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "firebase_admin" or name.startswith("firebase_admin"):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(provisioning, "cfg", _cfg(provisioning_mode="auto"))
    monkeypatch.setattr(builtins, "__import__", fake_import)
    reset_approval_store()
    store = approval_store_from_config()
    assert isinstance(store, InMemoryApprovalStore)
    reset_approval_store()


def test_reset_approval_store_invalidates_singleton():
    reset_approval_store()
    first = approval_store_from_config()
    assert isinstance(first, InMemoryApprovalStore)
    reset_approval_store()
    second = approval_store_from_config()
    assert second is not first
    reset_approval_store()
