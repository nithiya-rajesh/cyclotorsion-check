"""End-to-end pipeline tests using synthetic images and the mock detector."""

import math

from cyclotorsion.detector import MockLandmarkDetector
from cyclotorsion.pipeline import run_detection


def _pair(angle: float, size: int = 256):
    from generate_synthetic_data import generate_pair

    return generate_pair(angle, size)


def test_pipeline_recovers_positive_rotation():
    detector = MockLandmarkDetector()
    upright, rotated, truth = _pair(8.0)
    outcome = run_detection(detector, upright, rotated, 256, 256)
    assert math.isclose(outcome.angle_deg, truth, abs_tol=4.0)


def test_pipeline_recovers_zero_rotation():
    detector = MockLandmarkDetector()
    upright, rotated, truth = _pair(0.0)
    outcome = run_detection(detector, upright, rotated, 256, 256)
    assert math.isclose(outcome.angle_deg, 0.0, abs_tol=4.0)


def test_pipeline_output_roundtrip():
    detector = MockLandmarkDetector()
    upright, rotated, _ = _pair(4.0)
    outcome = run_detection(detector, upright, rotated, 256, 256)
    client = outcome.to_client_dict()
    # angle_deg is rounded to two places in the client payload.
    assert isinstance(client["angle_deg"], float)
    assert "passed_sanity_check" in client
    assert "upright_landmark" in client
