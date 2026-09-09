"""Tests for the public package re-export surface (domain.py)."""

from cyclotorsion.domain import (
    FLAG_NEAR_ZERO,
    FLAG_TOO_LARGE,
    SanityResult,
    cyclotorsion_deg,
    evaluate_angle,
    human_message,
    round_to_two,
    vector_angle_deg,
)


def test_domain_re_exports_callable_surface():
    # Every re-export resolves to a working reference.
    assert callable(cyclotorsion_deg)
    assert callable(evaluate_angle)
    assert callable(human_message)
    assert callable(round_to_two)
    assert callable(vector_angle_deg)
    assert FLAG_NEAR_ZERO and FLAG_TOO_LARGE
    assert SanityResult is not None
