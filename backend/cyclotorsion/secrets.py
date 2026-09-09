"""Google Secret Manager resolver for the Gemini API key (TDD Section 5.4).

Closes the plaintext-secret gap identified in the TDD: the Gemini API key is
currently passed via Cloud Run's ``--set-env-vars`` flag, storing it in
plaintext, visible to anyone with ``gcloud run services describe`` access.

Migration options (both supported):

  1. **Runtime access** (this module): the service resolves the key directly
     from Secret Manager via the ``google-cloud-secret-manager`` client. Secret
     Manager access is governed by IAM, yielding an audit trail of every read.
  2. **Secret mount** (IaC, see infra/): Cloud Run mounts the secret as an env
     var at deploy time, so it never sits in plaintext config. When that is
     used, ``cfg.gemini_api_key`` is already populated at runtime and this
     resolver simply returns it unchanged.
"""

from __future__ import annotations

import logging

from cyclotorsion.config import cfg

logger = logging.getLogger("cyclotorsion.secrets")

_cache: dict[str, str] = {}


def resolve_gemini_api_key(cached: bool = True) -> str:
    """Return the Gemini HTTP API key, resolving from Secret Manager if configured.

    Resolution order:

      - If ``cfg.gemini_secret`` names a Secret Manager version, fetch and cache
        its decrypted payload (lazily; requires GCP creds — a Cloud Run replica
        has them via metadata server). On failure, falls back to the env-var
        key rather than crashing at boot.
      - Otherwise return ``cfg.gemini_api_key`` (the classic env var / the IaC
        secret mount).

    Returns "" when no key is configured (caller then uses Vertex or mock).
    """
    if cached and _cache.get("gemini_api_key"):
        return _cache["gemini_api_key"]

    key = ""
    if cfg.gemini_secret:
        try:
            key = _read_secret(cfg.gemini_secret)
            _cache["gemini_api_key"] = key
            logger.info(
                "secrets.gemini_resolved", extra={"event": "secrets.gemini_resolved"}
            )
        except Exception as exc:  # noqa: BLE001 - never block boot on secret issues
            logger.warning(
                "secrets.gemini_resolve_failed",
                extra={
                    "event": "secrets.gemini_resolve_failed",
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            key = cfg.gemini_api_key
    else:
        key = cfg.gemini_api_key

    if key:
        _cache["gemini_api_key"] = key
    return key


def _read_secret(secret_version: str) -> str:
    """Fetch and decrypt ``secret_version`` via the Secret Manager client."""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_version)
    return response.payload.data.decode("utf-8")


def reset_cache() -> None:
    _cache.clear()
