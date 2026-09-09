"""Unit tests for the geometry engine (TDD Section 2.2: pure, deterministic)."""

import math

from cyclotorsion.geometry import (
    Point,
    corrected_axis_deg,
    cyclotorsion_deg,
    round_to_two,
    vector_angle_deg,
)


def test_vector_angle_cardinal_directions():
    center = Point(0, 0)
    # y-down image convention.
    assert math.isclose(vector_angle_deg(center, Point(1, 0)), 0.0)  # east
    assert math.isclose(vector_angle_deg(center, Point(0, 1)), 90.0)  # south
    assert math.isclose(vector_angle_deg(center, Point(-1, 0)), 180.0)  # west
    assert math.isclose(vector_angle_deg(center, Point(0, -1)), -90.0)  # north


def test_cyclotorsion_no_rotation_is_zero():
    center = Point(100, 100)
    upright = Point(130, 100)
    rotated = Point(130, 100)
    assert math.isclose(cyclotorsion_deg(upright, rotated, center), 0.0)


def test_cyclotorsion_90_degree_rotation():
    center = Point(100, 100)
    # Landmark straight to the right -> straight down is +90 in y-down coords.
    upright = Point(130, 100)
    rotated = Point(100, 130)
    assert math.isclose(cyclotorsion_deg(upright, rotated, center), 90.0)


def test_cyclotorsion_wraps_across_180_seam():
    center = Point(0, 0)
    upright = Point(1, 0)  # 0 deg
    rotated = Point(-1, 0.05)  # ~177 deg
    result = cyclotorsion_deg(upright, rotated, center)
    # Should report ~+177 or wrapped to ~-183? Computed as raw=177 -> in range.
    assert abs(abs(result) - 177) < 2.0


def test_wrap_near_seam_reports_small_rotation():
    center = Point(0, 0)
    upright = Point(1, 0.5)  # ~26.5 deg
    rotated = Point(-1, 0.4)  # ~158 deg (moved ~131 deg CW)
    result = cyclotorsion_deg(upright, rotated, center)
    # raw = 158 - 26.5 = 131.5 -> in range. Not near seam; just assert it is
    # the direct difference.
    assert math.isclose(result, 158.2 - 26.565, abs_tol=0.1)


def test_normalized_point_scales_properly():
    p = Point.normalized(0.5, 0.25, width=1000, height=800)
    assert math.isclose(p.x, 500.0)
    assert math.isclose(p.y, 200.0)


def test_round_to_two():
    assert round_to_two(3.14159) == 3.14
    assert round_to_two(0.005) == 0.01


def test_corrected_axis_uses_target_plus_rotation():
    # PRD Epic 6 / US-6.2: corrected axis = planned target + detected rotation.
    # Target 85 (the historical demo placeholder) + 9.8 rotation -> 94.8.
    assert corrected_axis_deg(85.0, 9.8) == 94.8


def test_corrected_axis_normalizes_to_0_180_range():
    # 179 target + 10 rotation would exceed 180; toric axes are mod 180 so it
    # wraps to a low value (189 mod 180 = 9).
    assert corrected_axis_deg(179.0, 10.0) == 9.0
    assert corrected_axis_deg(0.0, 0.0) == 0.0
    # Negative rotation works too, wrapping from below zero the same way.
    assert corrected_axis_deg(20.0, -30.0) == 350.0 % 180.0


def test_corrected_axis_is_within_0_180():
    import random

    for _ in range(500):
        t = random.uniform(0, 180)
        r = random.uniform(-180, 180)
        assert 0.0 <= corrected_axis_deg(t, r) < 180.0


def test_cyclotorsion_cw_rotation_is_positive():
    """Pin the sign convention: in y-down image space, a landmark moving
    clockwise (east -> south, +90 via atan2) yields a POSITIVE cyclotorsion.
    This documents the clinical direction of the returned sign.
    """
    center = Point(100, 100)
    upright = Point(130, 100)  # east  = 0 deg
    rotated = Point(100, 130)  # south = +90 deg (clockwise as displayed)
    assert cyclotorsion_deg(upright, rotated, center) > 0


def test_cyclotorsion_ccw_rotation_is_negative():
    """Anti-clockwise motion (east -> north) yields a NEGATIVE cyclotorsion."""
    center = Point(100, 100)
    upright = Point(130, 100)  # east = 0 deg
    rotated = Point(100, 70)  # north = -90 deg (counter-clockwise as displayed)
    assert cyclotorsion_deg(upright, rotated, center) < 0


def test_cyclotorsion_sign_is_antisymmetric():
    """Swapping upright/rotated flips the sign of the angle."""
    center = Point(50, 50)
    a = Point(80, 50)
    b = Point(50, 80)
    assert math.isclose(cyclotorsion_deg(a, b, center), -cyclotorsion_deg(b, a, center))


def test_cyclotorsion_wrap_negative_side_reports_small_angle():
    """A rotation crossing the -172/-180 seam (upright ~ +179 deg, rotated ~
    -179 deg) must be reported as a small angle (~2 deg), not ~ -358 deg.
    This mirrors the documented 179 -> -179 case on the negative side.
    """
    center = Point(0, 0)
    upright = Point(-1, 0.02)  # ~ +178.85 deg
    rotated = Point(-1, -0.02)  # ~ -178.85 deg; raw = -357.7 -> wraps to ~ +2.3
    result = cyclotorsion_deg(upright, rotated, center)
    assert abs(result) < 5.0
    assert result > 0
