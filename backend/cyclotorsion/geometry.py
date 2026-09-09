"""Geometry engine: deterministic rotation-angle computation.

This module is deliberately pure: no I/O, no network, no AI dependency. It
exists so that the angle returned to the surgeon is mathematically reproducible
and auditable (PRD US-2.2 / TDD Section 2.2). The AI locates landmarks; it is
never asked for a free-form angle estimate.

The convention used here mimics the physical geometry of a toric-IOL alignment
check:

  - Coordinates are in image space with the origin at the top-left corner,
    x increasing right, y increasing down (standard for pixel coordinates).
  - A "center" point is supplied (defaulting to the image center per PRD
    Section 8.4's documented pupil-center-assumption simplification).
  - The rotation of the landmark vector (from center to landmark) around that
    center is compared between the upright and rotated images, and the angle
    difference is the cyclotorsion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Physical range of cyclotorsion observed clinically (PRD Section 1.2).
CYCLOTORSION_RANGE_DEG = 15.0


@dataclass(frozen=True)
class Point:
    """A 2D coordinate in image pixel space (x, y)."""

    x: float
    y: float

    @classmethod
    def normalized(cls, nx: float, ny: float, width: int, height: int) -> Point:
        """Build a Point from normalized [0,1] landmark coordinates."""
        return cls(nx * width, ny * height)


def vector_angle_deg(center: Point, landmark: Point) -> float:
    """Angle (degrees) of the vector from ``center`` to ``landmark``.

    Uses arctan2, so the result is full-circle ([-180, 180]) and unambiguous
    about which quadrant the landmark lies in — exactly what we need to measure
    a rotation difference rather than just its magnitude.
    """
    dx = landmark.x - center.x
    dy = landmark.y - center.y
    return math.degrees(math.atan2(dy, dx))


def cyclotorsion_deg(upright: Point, rotated: Point, center: Point) -> float:
    """Compute the signed cyclotorsion angle (degrees) between two landmarks.

    ``upright`` is the landmark in the pre-op (upright) photo and ``rotated``
    is the corresponding landmark in the intra-op (supine) photo. The result is
    the signed angular difference measured around ``center``.

    The sign convention follows y-down image coordinates: a positive value
    means the landmark moved clockwise as displayed. Because the clinical
    magnitude is what matters most (PRD flags on absolute value), callers
    generally use ``abs()`` of the result, but the full signed value is kept
    for auditability.
    """
    a = vector_angle_deg(center, upright)
    b = vector_angle_deg(center, rotated)
    raw = b - a
    # Wrap into [-180, 180] so a small physical rotation near the +/-180
    # seam (e.g. 179 -> -179) is reported as ~2 degrees, not ~358.
    return (raw + 180.0) % 360.0 - 180.0


def corrected_axis_deg(target_axis_deg: float, angle_deg: float) -> float:
    """Compute the clinically-*real* corrected toric IOL axis (PRD Epic 6, US-6.2).

    The planned toric-IOL axis comes from the patient's own pre-op biometry
    (``target_axis_deg``). The detected cyclotorsion (``angle_deg``) is the
    rotation measured between the upright and supine photos. The corrected axis
    is the planned axis rotated by that cyclotorsion.

    Toric axes are conventionally expressed over a 0–180 degree range (an axis
    of 180° is equivalent to 0°), so the result is normalized into ``[0, 180)``.

    This replaces the prototype's *hardcoded 85° placeholder* — the corrected
    axis is only clinically meaningful when built from the patient's own target
    axis (TDD Section 3.2 design note). Pure arithmetic; no I/O, so it is
    unit-testable and auditable like the rest of the geometry engine.
    """
    raw = angle_deg + target_axis_deg
    return raw % 180.0


def round_to_two(value: float) -> float:
    """Round to two decimal places, as specified in PRD US-2.2."""
    return round(value, 2)
