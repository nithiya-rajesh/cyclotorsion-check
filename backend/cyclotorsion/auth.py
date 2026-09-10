"""Firebase Authentication integration and FastAPI security dependency.

Implements the recommendation in PRD Section 5.4 / TDD Section 5.1 and 5.5:
server-side Firebase ID-token verification. This replaces the (already
insecure, now removed concept of) client-side demo auth.

Behavior is gated by ``CC_AUTH_ENABLED`` (see config.Config):

  - When **disabled** (local dev default): endpoints run "open". This is a
    deliberate dev convenience, identical to the pre-wiring state, so the
    service runs with zero Firebase setup. A ``UserContext`` with uid=None is
    returned.
  - When **enabled** (production/pilot): every protected endpoint verifies the
    caller's Firebase ID token via the Firebase Admin SDK and **fails closed**
    with 401 on any bad/missing token (TDD Section 4.4 — access control is a
    hard boundary, unlike the soft-fail pattern used for analytics logging).

RBAC groundwork (TDD Section 5.2): the verified token's custom claims are read
into ``UserContext.role`` and ``UserContext.facility_id`` so that a future
``/stats`` scoping per facility (PRD Section 8.2) can be added without changing
the authentication plumbing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, HTTPException, Request, status

from cyclotorsion.config import cfg

logger = logging.getLogger("cyclotorsion.auth")


class Role(StrEnum):
    """Roles from TDD Section 5.2 (staff review: previously three bare string
    constants — primitive obsession).

    ``StrEnum`` keeps every existing comparison, hash, join, f-string, and
    ``json.dumps`` call working unchanged: a Firebase custom claim decodes to
    a plain ``str``, and ``Role`` members compare equal to, hash the same as,
    and *format* as their string value, e.g. ``"surgeon" == Role.SURGEON`` and
    ``f"{Role.SURGEON}" == "surgeon"`` are both true (unlike a plain
    ``class Role(str, Enum)`` mixin, which formats as ``"Role.SURGEON"``).
    ``ROLE_SURGEON`` etc. remain as module-level aliases so no call site
    elsewhere in the codebase needs to change.
    """

    SURGEON = "surgeon"
    FACILITY_ADMIN = "facility_admin"
    PROGRAM_OFFICER = "program_officer"


ROLE_SURGEON = Role.SURGEON
ROLE_FACILITY_ADMIN = Role.FACILITY_ADMIN
ROLE_PROGRAM_OFFICER = Role.PROGRAM_OFFICER

# Every role that counts as "provisioned/approved". A verified caller whose
# token carries a role NOT in this set (or no role at all) is treated as
# *pending approval* — they have no access until an administrator approves them
# with a real role claim (PRD Section 8.2 admin-approval flow replacing the
# R11 manual allow-list).
APPROVED_ROLES = frozenset({ROLE_SURGEON, ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER})

AUTH_HEADER = "authorization"
BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class UserContext:
    """Authenticated caller info derived from a verified Firebase ID token."""

    uid: str | None
    email: str | None = None
    role: str | None = None
    facility_id: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.uid is not None

    @property
    def is_pending_approval(self) -> bool:
        """True if this caller is verified but not yet approved (no valid role).

        Never true in open/dev mode (uid is None there), so this only affects
        enforced, authenticated deployments.
        """
        return self.role not in APPROVED_ROLES


# Lazy-initialized Firebase Admin SDK app. Importing firebase_admin is
# deferred until the first *enforced* request so the local-dev import of this
# module never requires Firebase credentials.
_firebase_app = None


def _ensure_firebase_app():
    """Initialize the Firebase Admin SDK once, if not already done."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    import firebase_admin
    from firebase_admin import credentials as fb_credentials

    if cfg.firebase_credentials_path:
        cred = fb_credentials.Certificate(cfg.firebase_credentials_path)
        _firebase_app = firebase_admin.initialize_app(cred)
    else:
        # Falls back to GOOGLE_APPLICATION_CREDENTIALS / ADC.
        _firebase_app = firebase_admin.initialize_app()
    return _firebase_app


def _extract_token(request: Request) -> str:
    """Pull the Bearer token from the Authorization header, or raise 401."""
    header = request.headers.get(AUTH_HEADER, "")
    if not header.lower().startswith(BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header[len(BEARER_PREFIX) :].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _verify_with_firebase(token: str) -> UserContext:
    """Verify ``token`` against Firebase and build a UserContext.

    Raises ``HTTPException(401)`` on any verification failure (expired,
    invalid signature, revoked, malformed). This is the hard-boundary path.

    ``check_revoked=True`` (LOW-5 fix) makes Firebase reject tokens whose
    originating account or session has been revoked (e.g. a de-provisioned
    clinician), so access ends promptly instead of lingering until expiry.
    """
    try:
        _ensure_firebase_app()
        from firebase_admin import auth as fb_auth

        decoded = fb_auth.verify_id_token(token, check_revoked=True)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - token verification must fail closed
        logger.warning("Firebase token verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # firebase_admin.auth.verify_id_token() returns the decoded JWT payload
    # with custom claims (role, facility_id) merged flat at the top level —
    # there is no separate nested "claims" key. An earlier version of this
    # function read decoded.get("claims"), which is always absent from the
    # real SDK's return value: every authenticated caller in production
    # silently got role=None and facility_id=None regardless of what an
    # admin had actually approved, permanently failing the approval check
    # (is_pending_approval) for everyone. Confirmed against a real deployed
    # Firebase project + live token, not just the (incorrectly mocked) test
    # suite, which fabricated the nested shape this code expected instead of
    # the SDK's actual one.
    return UserContext(
        uid=decoded.get("uid"),
        email=decoded.get("email"),
        role=decoded.get("role"),
        facility_id=decoded.get("facility_id"),
    )


def current_user(request: Request) -> UserContext:
    """FastAPI dependency providing the authenticated caller.

    When auth is disabled in a NON-production environment, returns an
    unauthenticated context (local dev). In a **production** environment,
    leaving auth disabled is treated as a misconfiguration and access is
    denied with 401 (fail-closed) — a config typo can no longer silently expose
    the protected endpoints. When auth is enabled, verifies the Bearer token
    via Firebase and fails closed on any bad/missing token.
    """
    if not cfg.auth_enabled:
        if getattr(cfg, "app_env", "development") in {"prod", "production"}:
            logger.critical(
                "auth.disabled_in_production",
                extra={"event": "auth.disabled_in_production"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is disabled in this environment",
            )
        return UserContext(uid=None)

    token = _extract_token(request)
    return _verify_with_firebase(token)


def _try_auto_approve(user: UserContext) -> UserContext | None:
    """Review-period convenience (cfg.auto_approve_new_users): grant a pending
    account the `surgeon` role automatically instead of requiring a
    facility_admin to act first. Returns the newly-approved context, or None
    if auto-approval isn't enabled/applicable (caller falls back to the
    normal fail-closed 403).

    Persists a real custom-claims grant via the approval store (the same
    call an admin's POST /admin/approve makes) so the account is genuinely
    approved from here on, not just for this one request — and returns an
    approved context immediately, so the caller doesn't have to wait for
    their ID token to naturally refresh before the grant takes effect.
    Best-effort: if the approval-store write fails, the caller falls back to
    the normal pending-approval rejection rather than silently proceeding
    unapproved.
    """
    if not (cfg.auto_approve_new_users and user.uid and is_pending_approval(user)):
        return None
    try:
        from cyclotorsion.provisioning import get_approval_store

        get_approval_store().approve(
            user.uid, ROLE_SURGEON, cfg.auto_approve_facility_id
        )
    except Exception:  # noqa: BLE001 - best-effort; fall back to normal 403
        logger.warning(
            "auth.auto_approve_failed", extra={"event": "auth.auto_approve_failed"}
        )
        return None
    return UserContext(
        uid=user.uid,
        email=user.email,
        role=ROLE_SURGEON,
        facility_id=cfg.auto_approve_facility_id,
    )


def require_any_role(*roles: str):
    """Dependency requiring the caller to hold one of ``roles`` (RBAC, TDD 5.2).

    In open/dev mode all callers are permitted. When auth is enabled, the caller
    must hold at least one of the given role custom claims or they are rejected
    with 403 (fail closed).
    """

    allowed = frozenset(roles)

    def _dep(user: UserContext = Depends(current_user)) -> UserContext:
        if cfg.auth_enabled and user.role not in allowed:
            # Auto-approval only ever grants `surgeon` — a route that doesn't
            # accept that role (facility_admin-only account/erasure
            # management) stays gated exactly as before, auto-approve or not.
            if ROLE_SURGEON in allowed:
                approved = _try_auto_approve(user)
                if approved is not None:
                    return approved
            detail = (
                "Account pending approval"
                if is_pending_approval(user)
                else f"Requires one of roles: {', '.join(sorted(allowed))}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=detail,
            )
        return user

    return _dep


def current_approved_user(
    user: UserContext = Depends(current_user),
) -> UserContext:
    """Verifies the caller AND enforces the admin-approval gate (PRD 8.2).

    In open/dev mode: returns an unauthenticated context (permitted) so the
    service still runs end to end without Firebase.

    With auth enabled: a valid ID token is required (401 on bad/missing); a
    valid token whose claims carry no approved role is rejected with 403
    "account pending approval" — closing the R11 self-registration gap by
    turning the old manual allow-list into a hard fail-closed boundary. Only
    accounts an administrator has approved (given a real role claim) gain
    access.

    Depends on ``current_user`` via FastAPI's dependency cache so the Firebase
    ID-token is verified exactly once per request, even though
    ``rate_limit`` and ``concurrency_limit`` also depend on ``current_user``.
    """
    if cfg.auth_enabled and is_pending_approval(user):
        approved = _try_auto_approve(user)
        if approved is not None:
            return approved
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval",
        )
    return user


def is_pending_approval(user: UserContext) -> bool:
    """True when auth is enforced and the caller has no approved role claim."""
    return user.is_pending_approval
