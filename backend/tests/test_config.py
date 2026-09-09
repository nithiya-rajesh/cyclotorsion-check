"""Tests for the environment-driven config helpers and derived properties."""

from cyclotorsion.config import (
    Config,
    _env_bool,
    _env_float,
    _env_float_or_none,
    _env_int,
    _env_str,
)


def test_env_bool_false_for_non_truthy(monkeypatch):
    monkeypatch.setenv("X", "off")
    assert _env_bool("X", False) is False


def test_env_bool_defaults(monkeypatch):
    monkeypatch.delenv("Y", raising=False)
    assert _env_bool("Y", True) is True


def test_env_str_strips(monkeypatch):
    monkeypatch.setenv("S", "  hello  ")
    assert _env_str("S") == "hello"


def test_env_float_parses_and_defaults(monkeypatch):
    monkeypatch.setenv("F", "3.5")
    assert _env_float("F", 1.0) == 3.5
    monkeypatch.delenv("F", raising=False)
    assert _env_float("F", 1.0) == 1.0


def test_env_float_bad_value_uses_default(monkeypatch):
    monkeypatch.setenv("F", "abc")
    assert _env_float("F", 2.0) == 2.0


def test_env_float_or_none(monkeypatch):
    monkeypatch.delenv("FN", raising=False)
    assert _env_float_or_none("FN") is None
    monkeypatch.setenv("FN", "7.7")
    assert _env_float_or_none("FN") == 7.7
    monkeypatch.setenv("FN", "nope")
    assert _env_float_or_none("FN") is None


def test_env_int_parses_and_defaults(monkeypatch):
    monkeypatch.setenv("I", "42")
    assert _env_int("I", 1) == 42
    monkeypatch.delenv("I", raising=False)
    assert _env_int("I", 9) == 9


def test_env_int_bad_value_uses_default(monkeypatch):
    monkeypatch.setenv("I", "x")
    assert _env_int("I", 5) == 5


def test_cors_origin_list_custom():
    cfg = Config(cors_origins=" https://app.com , https://api.com ")
    assert cfg.cors_origin_list == ["https://app.com", "https://api.com"]


def test_app_env_defaults_to_development():
    assert Config().app_env == "development"
    assert Config().is_production is False


def test_app_env_production_flags_is_production():
    cfg = Config(app_env="production")
    assert cfg.is_production is True
    assert Config(app_env="prod").is_production is True
    assert Config(app_env="staging").is_production is False


def test_cors_origin_list_default():
    assert "http://localhost:3000" in Config().cors_origin_list


def test_cors_origin_list_production_with_custom_uses_custom():
    cfg = Config(app_env="production", cors_origins="https://app.example.com")
    assert cfg.cors_origin_list == ["https://app.example.com"]


def test_cors_origin_list_production_empty_is_fail_closed():
    # Architecture review Major: an empty CC_CORS_ORIGINS in production must
    # yield an empty allowlist (no dev-origin fallback), not the localhost list.
    cfg = Config(app_env="production", cors_origins="")
    assert cfg.cors_origin_list == []


def test_cors_origin_list_prod_alias_also_fail_closed():
    assert Config(app_env="prod", cors_origins="").cors_origin_list == []
    assert Config(app_env="production", cors_origins=" ").cors_origin_list == []
    # Non-production unaffected.
    assert "http://localhost:3000" in Config(app_env="development").cors_origin_list


def test_use_real_gemini_modes():
    assert Config(detect_mode="mock").use_real_gemini is False
    assert Config(detect_mode="gemini").use_real_gemini is True
    # auto -> true only with creds
    assert Config(detect_mode="auto", gemini_api_key="k").use_real_gemini is True
    assert Config(detect_mode="auto").use_real_gemini is False


def test_use_bigquery_memory_force_false():
    assert Config(storage_mode="memory").use_bigquery is False


def test_use_bigquery_explicit_true():
    assert Config(storage_mode="bigquery").use_bigquery is True


def test_use_bigquery_auto_true_when_sdk_available():
    # google.cloud is installed in this env, so auto -> True
    assert Config(storage_mode="auto").use_bigquery is True


def test_use_bigquery_auto_false_when_sdk_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "google.cloud" or name.startswith("google.cloud"):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert Config(storage_mode="auto").use_bigquery is False
