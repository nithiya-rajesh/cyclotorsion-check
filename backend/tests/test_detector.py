"""Tests for the Gemini landmark-detection adapter (TDD 2.2, 4.4, 5.5)."""

import sys
import types
from types import SimpleNamespace

import pytest

from cyclotorsion.detector import (
    MAX_DESCRIPTION_CHARS,
    GeminiLandmarkDetector,
    ProviderError,
    _image_part,
    _jitter,
    _parse_landmark,
    detector_from_config,
)


def _install_fake_google(monkeypatch, client_factory=lambda kw: object()):
    """Insert fake `google.genai` modules so Client()/Part() are controllable."""
    google = types.ModuleType("google")
    google.__path__ = []
    genai = types.ModuleType("google.genai")
    genai.__path__ = []

    def _client(**kw):
        return client_factory(kw)

    genai.Client = _client

    types_mod = types.ModuleType("google.genai.types")

    class _Part:
        @staticmethod
        def from_bytes(data, mime_type):
            return SimpleNamespace(data=data, mime_type=mime_type)

    types_mod.Part = _Part

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)


def test_parse_landmark_valid_json():
    text = '{"x": 0.5, "y": 0.3, "description": "small red spot on sclera"}'
    lm = _parse_landmark(text, 800, 600)
    assert lm.x == 0.5
    assert lm.y == 0.3
    assert lm.description == "small red spot on sclera"


def test_parse_landmark_ignores_surrounding_text():
    text = 'Sure! Here it is:\n{"x": 0.2, "y": 0.7, "description": "vessel"}\nDone.'
    lm = _parse_landmark(text, 100, 100)
    assert lm.x == 0.2
    assert lm.y == 0.7


def test_parse_landmark_rejects_out_of_range():
    text = '{"x": 1.5, "y": 0.3, "description": "bad"}'
    with pytest.raises(ProviderError):
        _parse_landmark(text, 100, 100)


def test_parse_landmark_rejects_no_json():
    with pytest.raises(ProviderError):
        _parse_landmark("the image is fine", 100, 100)


def test_parse_landmark_caps_description_length():
    # A bloated model description must not be allowed to inflate the response.
    long_desc = "x" * (MAX_DESCRIPTION_CHARS + 500)
    text = f'{{"x": 0.5, "y": 0.3, "description": "{long_desc}"}}'
    lm = _parse_landmark(text, 100, 100)
    assert len(lm.description) == MAX_DESCRIPTION_CHARS
    assert lm.description.startswith("x" * MAX_DESCRIPTION_CHARS)


def test_mock_detector_raises_on_unreadable_image():
    from cyclotorsion.detector import MockLandmarkDetector

    with pytest.raises(ProviderError):
        MockLandmarkDetector().detect(b"not an image", 100, 100)


# --------------------------------------------------------------------------- #
# GeminiLandmarkDetector (production adapter) — external client mocked


class _FakeClient:
    """A drop-in fake for `client.models.generate_content`."""

    def __init__(self, result_text, fail_times=0, error=None):
        self.result_text = result_text
        self.fail_times = fail_times
        self.error = error or RuntimeError("transient overload")
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return SimpleNamespace(text=self.result_text)


def test_gemini_detector_requires_credentials():
    with pytest.raises(ProviderError):
        GeminiLandmarkDetector(api_key=None, vertex_location=None).detect(b"x", 10, 10)


def test_get_client_uses_api_key_when_no_vertex(monkeypatch):
    captured = {}

    def factory(kw):
        captured.update(kw)
        return object()

    _install_fake_google(monkeypatch, factory)
    d = GeminiLandmarkDetector(api_key="sekret", vertex_location=None)
    d._get_client()
    assert captured == {"api_key": "sekret"}


def test_get_client_uses_vertex_when_location_set(monkeypatch):
    captured = {}

    def factory(kw):
        captured.update(kw)
        return object()

    _install_fake_google(monkeypatch, factory)
    d = GeminiLandmarkDetector(
        api_key=None, vertex_location="projects/p/locations/us-central1", project="p"
    )
    d._get_client()
    assert captured == {
        "vertexai": True,
        "project": "p",
        "location": "projects/p/locations/us-central1",
    }
    # second call reuses the cached client (no new Client() construction)
    assert d._get_client() is not None


def test_gemini_detect_success(monkeypatch):
    _install_fake_google(monkeypatch)
    fake = _FakeClient('{"x": 0.5, "y": 0.3, "description": "vessel"}')
    d = GeminiLandmarkDetector(api_key="k")
    d._client = SimpleNamespace(
        models=SimpleNamespace(generate_content=fake.generate_content)
    )
    lm = d.detect(b"img", 800, 600)
    assert lm.x == 0.5
    assert lm.y == 0.3
    assert fake.calls == 1


def test_gemini_detect_unparseable_reraises_provider_error(monkeypatch):
    _install_fake_google(monkeypatch)
    fake = _FakeClient("no json here")
    d = GeminiLandmarkDetector(api_key="k")
    d._client = SimpleNamespace(
        models=SimpleNamespace(generate_content=fake.generate_content)
    )
    with pytest.raises(ProviderError):
        d.detect(b"img", 800, 600)
    # a parse failure is not retried and the breaker records success
    assert fake.calls == 1


def test_gemini_detect_retries_transient_then_succeeds(monkeypatch):
    _install_fake_google(monkeypatch)
    fake = _FakeClient('{"x": 0.1, "y": 0.1}', fail_times=1)
    monkeypatch.setattr("cyclotorsion.detector.time.sleep", lambda _s: None)
    d = GeminiLandmarkDetector(api_key="k")
    d._client = SimpleNamespace(
        models=SimpleNamespace(generate_content=fake.generate_content)
    )
    lm = d.detect(b"img", 800, 600)
    assert lm.x == 0.1
    assert fake.calls == 2  # first attempt failed, second succeeded


def test_gemini_detect_exhausts_then_raises(monkeypatch):
    _install_fake_google(monkeypatch)
    fake = _FakeClient("irrelevant", fail_times=99)
    monkeypatch.setattr("cyclotorsion.detector.time.sleep", lambda _s: None)
    d = GeminiLandmarkDetector(api_key="k")
    d._client = SimpleNamespace(
        models=SimpleNamespace(generate_content=fake.generate_content)
    )
    with pytest.raises(ProviderError):
        d.detect(b"img", 800, 600)
    assert fake.calls == 3  # MAX_ATTEMPTS


def test_open_breaker_fails_fast(monkeypatch):
    """With a pre-built open breaker, detect() raises without touching Gemini."""
    from cyclotorsion.circuit_breaker import (
        STATE_OPEN,
        BreakerConfig,
        CircuitBreaker,
    )

    _install_fake_google(monkeypatch)
    breaker = CircuitBreaker(BreakerConfig(failure_threshold=1))
    breaker.on_failure()  # opens the breaker
    assert breaker.state == STATE_OPEN
    d = GeminiLandmarkDetector(api_key="k", circuit_breaker=breaker)
    with pytest.raises(ProviderError):
        d.detect(b"img", 800, 600)


def test_jitter_within_expected_bounds():
    for _ in range(50):
        j = _jitter(1.0)
        assert 0.8 <= j <= 1.2


def test_image_part_builds_part(monkeypatch):
    _install_fake_google(monkeypatch)
    part = _image_part(b"bytes", "image/png")
    assert part.data == b"bytes"
    assert part.mime_type == "image/png"


def test_detector_from_config_mock_when_disabled(monkeypatch):
    import dataclasses

    from cyclotorsion.config import cfg

    new_cfg = dataclasses.replace(cfg, detect_mode="mock")
    monkeypatch.setattr("cyclotorsion.config.cfg", new_cfg)
    from cyclotorsion.detector import MockLandmarkDetector

    assert isinstance(detector_from_config(), MockLandmarkDetector)


def test_detector_from_config_gemini(monkeypatch):
    import dataclasses

    from cyclotorsion.config import cfg

    new_cfg = dataclasses.replace(
        cfg,
        detect_mode="gemini",
        gemini_model="gemini-2.5-flash",
        gemini_vertex_location="",
        circuit_breaker_failure_threshold=3,
        circuit_breaker_recovery_seconds=10.0,
    )
    monkeypatch.setattr("cyclotorsion.config.cfg", new_cfg)
    monkeypatch.setattr(
        "cyclotorsion.secrets.resolve_gemini_api_key", lambda: "resolved-key"
    )
    d = detector_from_config()
    assert isinstance(d, GeminiLandmarkDetector)
    assert d._api_key == "resolved-key"
    assert d._breaker._conf.failure_threshold == 3
