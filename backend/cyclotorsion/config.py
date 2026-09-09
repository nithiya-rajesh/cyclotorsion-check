"""Environment-driven configuration for CyclotorsionCheck.

Centralizes every operational setting read from the environment so that the
service can run identically in three modes:

  - Local/dev (default): mock detector, in-memory storage, endpoints open.
  - Production-deployed: Gemini detector (API key or Vertex ADC), BigQuery
    writer, Firebase auth enforcement, tightened CORS.

Keeping this in one module means the FastAPI app, the auth layer, and the
detector/storage factories all agree on how the environment was interpreted —
there is exactly one definition of each switch, not several scattered readers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_float_or_none(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """All runtime settings, captured once at import time."""

    # --- Security / auth ---
    # Deployment environment: "development" (default, for local dev/testing),
    # or "production" (pilot). When "production", endpoints FAIL CLOSED if auth
    # is ever disabled — a config typo can no longer silently expose /detect,
    # /stats, or /admin/* to the public internet (the report's HIGH-1 fix).
    app_env: str = "development"
    # Enforcement mode. Default False so the local dev service runs open
    # without any Firebase project wired up. Set True (with creds) to protect
    # /detect and /stats with Firebase ID-token verification. In a production
    # environment, leaving this False is rejected by the auth layer (fail-closed).
    auth_enabled: bool = False
    # Optional Firebase service-account JSON path. When auth is enabled and
    # this is absent, standard Application Default Credentials / GOOGLE_APPLICATION_CREDENTIALS
    # are used on the first verified request.
    firebase_credentials_path: str = ""
    # Approver store backend: "memory" (dev/tests) | "firebase" | "auto".
    provisioning_mode: str = "memory"

    # --- Gemini detection ---
    # "auto" -> real Gemini when an API key or Vertex ADC is available, else mock.
    # "mock" -> always use the mock detector (deterministic local testing).
    # "gemini" -> always use Gemini; require a key/credentials.
    detect_mode: str = "auto"
    gemini_api_key: str = ""
    # When set, resolve the Gemini HTTP API key from Google Secret Manager at
    # runtime instead of relying on the (plaintext) GEMINI_API_KEY env var
    # (TDD Section 5.4). E.g. "projects/PROJECT/secrets/gemini-api-key/versions/latest".
    gemini_secret: str = ""
    # Explicit override of the Gemini model. gemini-2.5-flash is the current
    # stable default — confirmed against the actual deployed project's Vertex
    # AI Model Garden access (2026-09): gemini-2.0-flash has since been
    # retired, and newer catalog entries (gemini-3.x) are listed but return
    # 404 for this project (likely allowlist/quota-gated), so this is not an
    # arbitrary choice — it is the newest model verified to actually work.
    gemini_model: str = "gemini-2.5-flash"
    # When non-empty, use Vertex AI (Application Default Credentials) instead
    # of the API key, e.g. "projects/PROJECT/locations/asia-south1".
    gemini_vertex_location: str = ""

    # --- Storage ---
    # "auto" -> BigQuery when available, else in-memory. "memory" forces
    # in-memory. "bigquery" forces BigQuery.
    storage_mode: str = "memory"
    bigquery_table: str = "cyclotorsion_check.results"

    # --- Patient Profile store (PRD Epic 7 / TDD Section 3.2a) ---
    # "memory" -> in-memory (local/dev/tests). "cloudsql" -> Cloud SQL
    # (PostgreSQL). "auto" -> Cloud SQL when a connection string is set, else
    # memory.
    patient_store_mode: str = "memory"
    # Cloud SQL (PostgreSQL) connection string for production
    # (e.g. "postgresql://user:pass@x:5432/cyclotorsion"). Only used by the
    # "cloudsql"/"auto" modes.
    cloud_sql_connection_string: str = ""

    # --- CORS ---
    # Comma-separated allowed origins. In production set to the exact Firebase
    # Hosting origin; leave empty for dev (accept localhost).
    cors_origins: str = ""

    # --- Rate limiting (TDD Section 4.3) ---
    # Per-user (UID / IP in open mode) token bucket. Disabled by default for
    # frictionless local dev; enable for pilot to protect the shared Gemini
    # quota from a single client exhausting it.
    rate_limit_enabled: bool = False
    rate_limit_per_minute: float = 20.0
    rate_limit_burst: float | None = None

    # --- Concurrency limiting (P3 hardening) ---
    # Caps how many /detect requests a single user may have in flight at once
    # (in addition to the rate limit, which caps requests/minute). Guards the
    # shared Gemini quota / Cloud Run capacity against a client stacking
    # simultaneous requests.
    concurrency_limit_enabled: bool = False
    max_concurrent_per_user: int = 2

    # --- Circuit breaker (TDD Section 4.4) ---
    # Open the breaker after N consecutive Gemini failures; recover after
    # ``recovery_timeout_seconds``.
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_seconds: float = 60.0

    # --- Observability / tracing (TDD Section 6.3) ---
    # When enabled, per-request spans (Gemini call, /detect lifecycle) are
    # exported to Cloud Trace keyed by the same request_id used in logs so logs
    # and traces correlate. Disabled by default (no GCP creds in local dev).
    tracing_enabled: bool = False
    # Google Cloud project id, e.g. "cyclotorsion-check"; required to address
    # the Cloud Trace / Secret Manager resource. Optional placeholder for dev.
    project_id: str = ""

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            app_env=_env_str("CC_APP_ENV", "development").lower(),
            auth_enabled=_env_bool("CC_AUTH_ENABLED", False),
            firebase_credentials_path=_env_str("CC_FIREBASE_CREDENTIALS"),
            provisioning_mode=_env_str("CC_PROVISIONING_MODE", "memory").lower(),
            detect_mode=_env_str("CC_DETECT_MODE", "auto").lower(),
            gemini_api_key=_env_str("GEMINI_API_KEY"),
            gemini_secret=_env_str("CC_GEMINI_SECRET"),
            gemini_model=_env_str("GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_vertex_location=_env_str("GEMINI_VERTEX_LOCATION"),
            storage_mode=_env_str("CC_STORAGE_MODE", "memory").lower(),
            bigquery_table=_env_str("CC_BIGQUERY_TABLE", "cyclotorsion_check.results"),
            patient_store_mode=_env_str("CC_PATIENT_STORE_MODE", "memory").lower(),
            cloud_sql_connection_string=_env_str("CC_CLOUD_SQL_CONNECTION_STRING"),
            cors_origins=_env_str("CC_CORS_ORIGINS"),
            rate_limit_enabled=_env_bool("CC_RATE_LIMIT_ENABLED", False),
            rate_limit_per_minute=_env_float("CC_RATE_LIMIT_PER_MINUTE", 20.0),
            rate_limit_burst=_env_float_or_none("CC_RATE_LIMIT_BURST"),
            concurrency_limit_enabled=_env_bool("CC_CONCURRENCY_LIMIT_ENABLED", False),
            max_concurrent_per_user=_env_int("CC_MAX_CONCURRENT_PER_USER", 2),
            circuit_breaker_failure_threshold=_env_int("CC_CB_FAILURE_THRESHOLD", 5),
            circuit_breaker_recovery_seconds=_env_float("CC_CB_RECOVERY_SECONDS", 60.0),
            tracing_enabled=_env_bool("CC_TRACING_ENABLED", False),
            project_id=_env_str("CC_PROJECT_ID"),
        )

    @property
    def is_production(self) -> bool:
        return self.app_env in {"prod", "production"}

    @property
    def cors_origin_list(self) -> list[str]:
        """Resolve the CORS origin allowlist.

        In a **production** environment an empty ``CC_CORS_ORIGINS`` is treated
        as a misconfiguration: we return an empty allowlist (fail closed) rather
        than silently allowing credentialed requests from developer origins.
        In development the fallback allowlist of localhost origins is used.
        """
        if self.cors_origins:
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.is_production:
            # Fail closed: no dev-origin fallback may be active in production.
            return []
        return [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8080",
            "http://localhost:8090",
            "http://127.0.0.1:8090",
        ]

    @property
    def has_gemini_credentials(self) -> bool:
        return bool(
            self.gemini_api_key or self.gemini_secret or self.gemini_vertex_location
        )

    @property
    def use_real_gemini(self) -> bool:
        if self.detect_mode == "mock":
            return False
        if self.detect_mode == "gemini":
            return True
        # "auto"
        return self.has_gemini_credentials

    @property
    def use_bigquery(self) -> bool:
        if self.storage_mode == "memory":
            return False
        if self.storage_mode == "bigquery":
            return True
        # "auto"
        try:
            import google.cloud  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False


# Single shared instance.
cfg = Config.from_env()
