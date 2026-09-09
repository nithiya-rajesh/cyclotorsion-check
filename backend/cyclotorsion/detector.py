"""Landmark detection adapter.

Isolates all multimodal-AI interaction (prompt construction, retry, circuit
breaker) behind a single internal interface so the rest of the system never
calls Gemini directly (TDD Section 2.2). This boundary is what makes a future
AI-provider swap feasible without touching the geometry or sanity engines.

Two implementations are provided:

  - ``GeminiLandmarkDetector``: the production adapter that calls the Google
    Generative Language (Gemini) API.
  - ``MockLandmarkDetector``: a deterministic, credential-free detector used
    for local development and testing. It locates a synthetic marker we can
    inject into generated test images, letting the whole pipeline run (and be
    unit-tested) without any API key or GCP project.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from cyclotorsion.circuit_breaker import BreakerConfig, CircuitBreaker
from cyclotorsion.metrics import gemini_api_latency, gemini_retry_inc
from cyclotorsion.tracing import span

# Retry parameters (TDD Section 4.4): fixed 3s backoff, max 3 attempts.
RETRY_BACKOFF_SECONDS = 3.0
MAX_ATTEMPTS = 3

# Response-size cap: the model's free-text landmark description is bounded so a
# misbehaving response can never inflate the /detect payload (P3 hardening).
MAX_DESCRIPTION_CHARS = 200


@dataclass(frozen=True)
class Landmark:
    """A detected anatomical landmark with a human-readable description.

    ``description`` is intentionally a generic anatomical description (e.g.
    "small red spot on sclera") that is not patient-identifiable (TDD
    Section 3.2, PRD US-5).
    """

    x: float
    y: float
    description: str = "unnamed landmark"


class ProviderError(Exception):
    """Raised when the AI provider is unavailable or returns an invalid result."""


class LandmarkDetector(ABC):
    """Interface implemented by all landmark detectors."""

    @abstractmethod
    def detect(self, image_bytes: bytes, width: int, height: int) -> Landmark:
        """Return the landmark coordinates within ``image_bytes``.

        Coordinates are in normalized [0,1] space aligned with the caller's
        ``width``/``height``. Raises ``ProviderError`` on failure.
        """


class MockLandmarkDetector(LandmarkDetector):
    """Deterministic mock used for local dev/testing (no credentials needed).

    It searches the (mildly downscaled, single-channel) buffer for the
    centroid of the brightest region (the injected marker) and returns it as
    the landmark. Because our synthetic test images place a distinct bright
    marker at a known position, this gives a stable, testable approximation of
    the real pipeline — enough to exercise the geometry, sanity, and API layers
    end to end.
    """

    def detect(self, image_bytes: bytes, width: int, height: int) -> Landmark:
        import io

        from PIL import Image

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            # Guard against decompression bombs even if a caller bypasses the
            # app-level cap: never materialize an unreasonably large canvas.
            if img.size[0] * img.size[1] > 16_000_000:
                raise ProviderError("Image exceeds the safe pixel limit (mock)")
            # Downscale only mildly so the mock's centroid estimate stays
            # reasonably precise (downscaling too far dominates the error).
            img = img.resize((max(1, width // 2), max(1, height // 2)))
            px = img.load()
            w, h = img.size
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError("Mock detector could not decode image") from exc

        # Find the centroid of the brightest region (the injected marker).
        threshold = 200
        sum_x = sum_y = count = 0
        for yy in range(h):
            for xx in range(w):
                v = px[xx, yy]
                if v >= threshold:
                    sum_x += xx
                    sum_y += yy
                    count += 1

        if count == 0:  # pragma: no cover - no bright marker found
            raise ProviderError("Mock detector found no landmark (no bright marker)")

        cx = sum_x / count
        cy = sum_y / count
        # Return normalized coordinates in the original image's frame.
        nx = (cx + 0.5) / w
        ny = (cy + 0.5) / h
        return Landmark(x=nx, y=ny, description="bright marker (mock)")


class GeminiLandmarkDetector(LandmarkDetector):
    """Production adapter calling the Gemini generative-language API.

    Uses the ``google-genai`` SDK (v2 API: ``client.models.generate_content``
    with keyword-only args). Credentials are resolved lazily from an API key or
    Vertex AI ADC so the module imports cleanly without them.

    Two credential paths (mirroring config.Config):
      - API key via ``gemdai_http`` / ``api_key=...`` (Gemini Developer API).
      - Vertex AI via ``vertexai=True`` + ``project``/``location`` with ADC.

    The detector holds its own retry with jitter and a circuit breaker (TDD
    Section 4.4). It never returns an angle — only a landmark — and any
    response that cannot be parsed into in-range normalized coordinates raises
    ``ProviderError``, so the geometry engine downstream is the sole authority
    on the angle.
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
        vertex_location: str | None = None,
        project: str | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self._model = model
        self._api_key = api_key
        self._vertex_location = vertex_location
        self._project = project
        self._client = None
        self._breaker = circuit_breaker or CircuitBreaker()

    def _get_client(self):
        if self._client is None:
            from google import genai

            if self._vertex_location:
                # Vertex AI with Application Default Credentials.
                self._client = genai.Client(
                    vertexai=True,
                    project=self._project,
                    location=self._vertex_location,
                )
            else:
                self._client = genai.Client(api_key=self._api_key)
        return self._client

    def detect(self, image_bytes: bytes, width: int, height: int) -> Landmark:
        if not self._api_key and not self._vertex_location:
            raise ProviderError(
                "GeminiLandmarkDetector requires a Gemini API key (GEMINI_API_KEY) "
                "or Vertex AI credentials (GEMINI_VERTEX_LOCATION). Use the mock "
                "detector for local dev (CC_DETECT_MODE=mock)."
            )
        # Circuit breaker: fail fast if the provider is known-down (TDD 4.4).
        self._breaker.before_call()
        client = self._get_client()
        prompt = (
            "You are locating a stable anatomical landmark (scleral mark, "
            "vessel bifurcation) in an eye photograph for cyclotorsion "
            "measurement. Return ONLY a JSON object with three fields: "
            "x (0..1), y (0..1), and a short generic anatomical description. "
            f"Image dimensions: {width}x{height}."
        )
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            start = time.monotonic()
            try:
                with span("gemini.landmark", attempt=attempt, model=self._model):
                    response = client.models.generate_content(
                        model=self._model,
                        contents=[
                            prompt,
                            _image_part(image_bytes, content_type_hint="image/png"),
                        ],
                    )
                landmark = _parse_landmark(response.text, width, height)
                gemini_api_latency(time.monotonic() - start)
                self._breaker.on_success()
                return landmark
            except ProviderError:
                gemini_api_latency(time.monotonic() - start)
                self._breaker.on_success()
                raise
            except Exception as exc:  # transient overload / network
                gemini_api_latency(time.monotonic() - start)
                gemini_retry_inc()
                last_exc = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(_jitter(RETRY_BACKOFF_SECONDS))
        self._breaker.on_failure()
        raise ProviderError(
            f"Gemini detection failed after {MAX_ATTEMPTS} attempts"
        ) from last_exc


def _jitter(base: float) -> float:
    """Add +/-20% jitter to ``base`` to avoid synchronized retry storms (TDD 4.4)."""
    import random

    return base * (1.0 + (random.random() - 0.5) * 0.4)


def _image_part(image_bytes: bytes, content_type_hint: str = "image/png"):
    from google.genai import types

    return types.Part.from_bytes(data=image_bytes, mime_type=content_type_hint)


def _parse_landmark(text: str, width: int, height: int) -> Landmark:
    import json
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ProviderError("Gemini response contained no JSON object")
    data = json.loads(match.group(0))
    nx = float(data["x"])
    ny = float(data["y"])
    if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
        raise ProviderError("Gemini returned out-of-range normalized coordinates")
    # The model's free-text description is untrusted input (architecture review
    # Major): sanitize it here at the source so no markup/control sequences from
    # Gemini ever reach the response/SPA.
    from cyclotorsion.text import sanitize_text

    description = sanitize_text(
        str(data.get("description", "landmark")), max_chars=MAX_DESCRIPTION_CHARS
    )
    return Landmark(
        x=nx,
        y=ny,
        description=description or "landmark",
    )


def detector_from_config() -> LandmarkDetector:
    """Build a detector from the application config (config.Config).

    - CC_DETECT_MODE=mock -> MockLandmarkDetector (deterministic, no creds).
    - CC_DETECT_MODE=gemini -> GeminiLandmarkDetector, error if no creds.
    - CC_DETECT_MODE=auto -> real Gemini if GEMINI_API_KEY or
      GEMINI_VERTEX_LOCATION is set, otherwise the mock detector.

    Detector selections happen **once at import time** and are stable for the
    process, exactly as the FastAPI app module consumes them.
    """
    from cyclotorsion.config import cfg
    from cyclotorsion.secrets import resolve_gemini_api_key

    if not cfg.use_real_gemini:
        return MockLandmarkDetector()
    return GeminiLandmarkDetector(
        model=cfg.gemini_model,
        api_key=resolve_gemini_api_key() or None,
        vertex_location=cfg.gemini_vertex_location or None,
        circuit_breaker=CircuitBreaker(
            BreakerConfig(
                failure_threshold=cfg.circuit_breaker_failure_threshold,
                recovery_timeout_seconds=cfg.circuit_breaker_recovery_seconds,
            )
        ),
    )
