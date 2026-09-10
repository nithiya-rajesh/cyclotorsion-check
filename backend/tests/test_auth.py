"""Tests for the Firebase auth layer (PRD 5.4, TDD 5.1/5.2/5.5).

These cover the open (dev) mode, the fail-closed invalid-token path, and the
role-guard logic. Live token *verification* requires real Firebase credentials
and is deliberately deferred to integration testing at deploy time — the unit
path here validates everything that does not need a live Firebase project.
"""

import pytest
from fastapi import HTTPException, status

from cyclotorsion import auth


class _FakeRequest:
    def __init__(self, auth_header: str | None = None):
        self.headers = {"authorization": auth_header} if auth_header else {}


def test_extract_token_valid(monkeypatch):
    req = _FakeRequest("Bearer abc.def.ghi")
    assert auth._extract_token(req) == "abc.def.ghi"


def test_extract_token_missing_header():
    req = _FakeRequest()
    with pytest.raises(HTTPException) as e:
        auth._extract_token(req)
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_extract_token_wrong_scheme():
    req = _FakeRequest("Basic dXNlcjpwYXNz")
    with pytest.raises(HTTPException) as e:
        auth._extract_token(req)
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_extract_token_empty():
    req = _FakeRequest("Bearer   ")
    with pytest.raises(HTTPException) as e:
        auth._extract_token(req)
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_current_user_open_mode_returns_unauthenticated(monkeypatch):
    # Force the config's auth_enabled to False regardless of environment.
    class _FakeConfig:
        auth_enabled = False

    monkeypatch.setattr(auth, "cfg", _FakeConfig())
    user = auth.current_user(_FakeRequest("Bearer x"))
    assert user.uid is None
    assert user.is_authenticated is False


def test_current_user_production_disabled_auth_fails_closed(monkeypatch):
    # HIGH-1 fix: a production deployment with auth disabled must NOT run open.
    class _FakeConfig:
        auth_enabled = False
        app_env = "production"

    monkeypatch.setattr(auth, "cfg", _FakeConfig())
    with pytest.raises(HTTPException) as e:
        auth.current_user(_FakeRequest("Bearer x"))
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_current_user_production_enforcing_same_as_normal(monkeypatch):
    # With auth enabled in production, verification behaves exactly as usual
    # (missing token -> 401).
    class _FakeConfig:
        auth_enabled = True
        app_env = "production"

    monkeypatch.setattr(auth, "cfg", _FakeConfig())
    with pytest.raises(HTTPException) as e:
        auth.current_user(_FakeRequest())
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_current_user_enforcing_mode_rejects_missing_token(monkeypatch):
    class _FakeConfig:
        auth_enabled = True

    monkeypatch.setattr(auth, "cfg", _FakeConfig())
    with pytest.raises(HTTPException) as e:
        auth.current_user(_FakeRequest())
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_current_user_enforcing_mode_rejects_invalid_token(monkeypatch):
    class _FakeConfig:
        auth_enabled = True
        firebase_credentials_path = ""

    # The live verify call would need real Firebase; here we stub it to prove
    # the dependency fails closed on a bad token rather than leaking through.
    monkeypatch.setattr(auth, "cfg", _FakeConfig())

    def _fake_verify(token):
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    monkeypatch.setattr(auth, "_verify_with_firebase", _fake_verify)
    with pytest.raises(HTTPException):
        auth.current_user(_FakeRequest("Bearer invalid"))


# --------------------------------------------------------------------------- #
# _ensure_firebase_app / _verify_with_firebase (live SDK mocked)


def _install_fake_firebase(monkeypatch, verify_result=None, verify_error=None):
    """Insert a fake `firebase_admin` (+credentials, +auth) into sys.modules."""
    import sys
    import types

    calls = {"init": [], "verify": []}

    firebase_admin = types.ModuleType("firebase_admin")

    def initialize_app(cred=None):
        calls["init"].append(cred)
        return "app-instance"

    firebase_admin.initialize_app = initialize_app

    creds = types.ModuleType("firebase_admin.credentials")

    def certificate(path):
        return f"cert:{path}"

    creds.Certificate = certificate

    fb_auth = types.ModuleType("firebase_admin.auth")

    def verify_id_token(token, check_revoked=False):
        calls["verify"].append((token, check_revoked))
        if verify_error is not None:
            raise verify_error
        return verify_result if verify_result is not None else {}

    fb_auth.verify_id_token = verify_id_token

    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", creds)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", fb_auth)
    return calls


def test_ensure_firebase_app_with_credentials_path(monkeypatch):
    calls = _install_fake_firebase(monkeypatch)
    monkeypatch.setattr(auth, "_firebase_app", None)
    monkeypatch.setattr(
        auth, "cfg", type("C", (), {"firebase_credentials_path": "/creds.json"})()
    )
    app = auth._ensure_firebase_app()
    assert app == "app-instance"
    assert calls["init"] == ["cert:/creds.json"]
    # second call reuses the cached app, does not re-init
    assert auth._ensure_firebase_app() == "app-instance"
    assert len(calls["init"]) == 1


def test_ensure_firebase_app_without_path_uses_adc(monkeypatch):
    calls = _install_fake_firebase(monkeypatch)
    monkeypatch.setattr(auth, "_firebase_app", None)
    monkeypatch.setattr(auth, "cfg", type("C", (), {"firebase_credentials_path": ""})())
    auth._ensure_firebase_app()
    assert calls["init"] == [None]  # initialize_app() with no cred -> ADC


def test_verify_with_firebase_builds_user_context(monkeypatch):
    # Custom claims (role, facility_id) are merged flat into the decoded
    # token by the real firebase_admin SDK — not nested under a "claims" key.
    calls = _install_fake_firebase(
        monkeypatch,
        verify_result={
            "uid": "u-1",
            "email": "a@b.c",
            "role": "surgeon",
            "facility_id": "fac_a",
        },
    )
    user = auth._verify_with_firebase("tok")
    assert calls["verify"] == [("tok", True)]  # check_revoked=True (LOW-5)
    assert user.uid == "u-1"
    assert user.email == "a@b.c"
    assert user.role == "surgeon"
    assert user.facility_id == "fac_a"


def test_verify_with_firebase_rejects_on_exception(monkeypatch):
    _install_fake_firebase(monkeypatch, verify_error=RuntimeError("bad sig"))
    with pytest.raises(HTTPException) as e:
        auth._verify_with_firebase("bad")
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_verify_with_firebase_handles_missing_claims(monkeypatch):
    _install_fake_firebase(monkeypatch, verify_result={"uid": "u9"})
    user = auth._verify_with_firebase("tok")
    assert user.uid == "u9"
    assert user.role is None
    assert user.facility_id is None


def test_verify_with_firebase_uses_check_revoked(monkeypatch):
    calls = _install_fake_firebase(monkeypatch, verify_result={"uid": "u1"})
    auth._verify_with_firebase("tok")
    # LOW-5: enforcement calls verify with check_revoked=True.
    assert calls["verify"] == [("tok", True)]


def test_verify_with_firebase_revoked_token_fails_closed(monkeypatch):
    # Reproduce a revoked session (firebase_admin.auth.RevokedIdTokenError) and
    # ensure it maps to a 401 rather than leaking through.
    _install_fake_firebase(monkeypatch, verify_error=RuntimeError("revoked token"))
    with pytest.raises(HTTPException) as e:
        auth._verify_with_firebase("old-token")
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


# --------------------------------------------------------------------------- #
# Review-period auto-approve (cfg.auto_approve_new_users)


class _AutoApproveConfig:
    """Minimal cfg stand-in exposing exactly what auth.py's auto-approve path
    reads, so these tests don't depend on the real Config's full field set."""

    def __init__(
        self, auth_enabled=True, auto_approve_new_users=False, facility_id="review-demo"
    ):
        self.auth_enabled = auth_enabled
        self.auto_approve_new_users = auto_approve_new_users
        self.auto_approve_facility_id = facility_id


def _pending_user(uid="new-uid"):
    return auth.UserContext(
        uid=uid, email="new@example.com", role=None, facility_id=None
    )


def _install_fake_approval_store(monkeypatch):
    """Swap in a real InMemoryApprovalStore so approve() calls are observable,
    without touching the process-wide singleton other tests may share."""
    from cyclotorsion.provisioning import InMemoryApprovalStore

    store = InMemoryApprovalStore()
    monkeypatch.setattr("cyclotorsion.provisioning.get_approval_store", lambda: store)
    return store


def test_try_auto_approve_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=False))
    assert auth._try_auto_approve(_pending_user()) is None


def test_try_auto_approve_returns_none_for_non_pending_user(monkeypatch):
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=True))
    already_approved = auth.UserContext(
        uid="u1", role=auth.ROLE_SURGEON, facility_id="fac_a"
    )
    assert auth._try_auto_approve(already_approved) is None


def test_try_auto_approve_grants_surgeon_and_persists_it(monkeypatch):
    monkeypatch.setattr(
        auth,
        "cfg",
        _AutoApproveConfig(auto_approve_new_users=True, facility_id="review-demo"),
    )
    store = _install_fake_approval_store(monkeypatch)

    approved = auth._try_auto_approve(_pending_user("new-uid"))

    assert approved is not None
    assert approved.role == auth.ROLE_SURGEON
    assert approved.facility_id == "review-demo"
    assert approved.uid == "new-uid"
    # Persisted via the same store an admin's POST /admin/approve would use —
    # not just an in-request-only bypass.
    persisted = {u.uid: u for u in store.list_users()}
    assert persisted["new-uid"].role == auth.ROLE_SURGEON
    assert persisted["new-uid"].approved is True


def test_current_approved_user_blocks_pending_when_auto_approve_disabled(monkeypatch):
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=False))
    with pytest.raises(HTTPException) as e:
        auth.current_approved_user(_pending_user())
    assert e.value.status_code == status.HTTP_403_FORBIDDEN
    assert "pending approval" in e.value.detail.lower()


def test_current_approved_user_auto_approves_when_enabled(monkeypatch):
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=True))
    _install_fake_approval_store(monkeypatch)

    result = auth.current_approved_user(_pending_user())

    assert result.role == auth.ROLE_SURGEON
    assert result.is_pending_approval is False


def test_require_any_role_auto_approves_when_surgeon_is_an_allowed_role(monkeypatch):
    # /patients create/search/etc. allow (surgeon, facility_admin) — the
    # surgeon-level auto-grant should let a fresh account through.
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=True))
    _install_fake_approval_store(monkeypatch)
    dep = auth.require_any_role(auth.ROLE_SURGEON, auth.ROLE_FACILITY_ADMIN)

    result = dep(_pending_user())

    assert result.role == auth.ROLE_SURGEON


def test_require_any_role_does_not_auto_approve_facility_admin_only_routes(monkeypatch):
    # Patient erasure and account/admin management are facility_admin-only —
    # auto-approve must NOT quietly grant that, even during a review window.
    # Otherwise any anonymous visitor could delete patient records or approve
    # other accounts, which is a materially different risk than letting them
    # try the core Analyze/Patients flow.
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=True))
    _install_fake_approval_store(monkeypatch)
    dep = auth.require_any_role(auth.ROLE_FACILITY_ADMIN)

    with pytest.raises(HTTPException) as e:
        dep(_pending_user())
    assert e.value.status_code == status.HTTP_403_FORBIDDEN


def test_try_auto_approve_falls_back_on_store_failure(monkeypatch):
    # If persisting the grant fails, the caller must fall back to the normal
    # fail-closed 403 rather than silently treating the request as approved.
    monkeypatch.setattr(auth, "cfg", _AutoApproveConfig(auto_approve_new_users=True))

    class _BrokenStore:
        def approve(self, *a, **k):
            raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(
        "cyclotorsion.provisioning.get_approval_store", lambda: _BrokenStore()
    )

    assert auth._try_auto_approve(_pending_user()) is None
