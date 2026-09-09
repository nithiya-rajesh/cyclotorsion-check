"""Tests for the Secret Manager Gemini-key resolver (TDD Section 5.4)."""

import dataclasses

from cyclotorsion import secrets


def _cfg(**changes):
    base = dataclasses.replace(secrets.cfg)
    return dataclasses.replace(base, **changes)


def test_returns_env_key_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(
        secrets, "cfg", _cfg(gemini_secret="", gemini_api_key="env-key")
    )
    secrets.reset_cache()
    assert secrets.resolve_gemini_api_key() == "env-key"


def test_fetches_from_secret_manager(monkeypatch):
    monkeypatch.setattr(
        secrets,
        "cfg",
        _cfg(
            gemini_secret="projects/p/secrets/gemini-api-key/versions/latest",
            gemini_api_key="",
        ),
    )
    monkeypatch.setattr(secrets, "_read_secret", lambda name: "secret-key")
    secrets.reset_cache()
    assert secrets.resolve_gemini_api_key() == "secret-key"


def test_falls_back_to_env_on_secret_failure(monkeypatch):
    monkeypatch.setattr(
        secrets,
        "cfg",
        _cfg(gemini_secret="projects/p/s/x/latest", gemini_api_key="env-key"),
    )
    monkeypatch.setattr(
        secrets,
        "_read_secret",
        lambda name: (_ for _ in ()).throw(RuntimeError("no creds")),
    )
    secrets.reset_cache()
    assert secrets.resolve_gemini_api_key() == "env-key"


def test_resolves_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(secrets, "cfg", _cfg(gemini_secret="", gemini_api_key=""))
    secrets.reset_cache()
    assert secrets.resolve_gemini_api_key() == ""


def test_secret_result_is_cached(monkeypatch):
    monkeypatch.setattr(
        secrets,
        "cfg",
        _cfg(gemini_secret="projects/p/secrets/gemini-api-key/versions/latest"),
    )
    calls = []
    monkeypatch.setattr(
        secrets, "_read_secret", lambda name: calls.append(name) or "secret-key"
    )
    secrets.reset_cache()
    assert secrets.resolve_gemini_api_key() == "secret-key"
    assert secrets.resolve_gemini_api_key() == "secret-key"
    assert len(calls) == 1  # cached after the first fetch


def test_read_secret_decodes_payload(monkeypatch):
    import sys
    import types

    secretmanager = types.ModuleType("google.cloud.secretmanager")
    secretmanager_version = types.ModuleType("google.cloud.secretmanager_v1")
    types_mod = types.ModuleType("google.cloud.secretmanager_v1.types")

    class _Service:
        def __init__(self):
            self.accessed = []

        def access_secret_version(self, name):
            self.accessed.append(name)
            return types.SimpleNamespace(
                payload=types.SimpleNamespace(data=b"decoded-key")
            )

    service = _Service()
    secretmanager.SecretManagerServiceClient = lambda: service
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", secretmanager)
    monkeypatch.setitem(
        sys.modules, "google.cloud.secretmanager_v1", secretmanager_version
    )
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager_v1.types", types_mod)

    result = secrets._read_secret("projects/p/secrets/k/versions/latest")
    assert result == "decoded-key"
    assert service.accessed == ["projects/p/secrets/k/versions/latest"]
