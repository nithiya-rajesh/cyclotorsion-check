"""Detection pipeline: orchestration of detection -> geometry -> sanity.

This module combines the three core engines into the single flow that a
``/detect`` request executes (PRD Epic 2 & 3, TDD Section 2.1). It is kept free
of HTTP concerns so it can be unit-tested directly and reused by evaluation
scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyclotorsion.detector import Landmark, LandmarkDetector
from cyclotorsion.geometry import Point, cyclotorsion_deg, round_to_two
from cyclotorsion.sanity import SanityResult, evaluate_angle, human_message


@dataclass(frozen=True)
class DetectionOutcome:
    """The result returned to the surgeon plus what was logged."""

    angle_deg: float
    angle_deg_rounded: float
    upright_landmark: Landmark
    rotated_landmark: Landmark
    sanity: SanityResult
    center: Point
    warning_message: str | None = None

    def to_client_dict(self) -> dict[str, Any]:
        """Shape the protocol-level response (PRD US-2.2/3.1 fields).

        Model-produced free text (descriptions, warning) is treated as untrusted
        and sanitized at this response boundary (architecture review Major) so
        no markup/control sequences that would reach the SPA survive even if a
        future detector embeds raw content.
        """
        from cyclotorsion.text import sanitize_text

        d: dict[str, Any] = {
            "angle_deg": self.angle_deg_rounded,
            "upright_landmark": sanitize_text(self.upright_landmark.description),
            "rotated_landmark": sanitize_text(self.rotated_landmark.description),
            "passed_sanity_check": self.sanity.passed,
            "sanity_flags": list(self.sanity.flags),
        }
        msg = self.warning_message or human_message(self.sanity.flags)
        if msg:
            d["warning"] = sanitize_text(msg)
        return d


def run_detection(
    detector: LandmarkDetector,
    upright_bytes: bytes,
    rotated_bytes: bytes,
    width: int,
    height: int,
) -> DetectionOutcome:
    """Run the full pipeline for one pair of images.

    The image center is used as the rotation center by default — the documented
    simplification in PRD Section 8.4 (pupil-center auto-detection is a
    Phase-4, evidence-gated item), not an overlooked shortcut.
    """
    # 1. Landmark detection (the AI's only job).
    upright = detector.detect(upright_bytes, width, height)
    rotated = detector.detect(rotated_bytes, width, height)

    # 2. Deterministic geometry (never model-estimated).
    center = Point(width / 2.0, height / 2.0)
    angle = cyclotorsion_deg(
        Point.normalized(upright.x, upright.y, width, height),
        Point.normalized(rotated.x, rotated.y, width, height),
        center,
    )

    # 3. Rule-based sanity validation.
    sanity = evaluate_angle(angle)

    return DetectionOutcome(
        angle_deg=angle,
        angle_deg_rounded=round_to_two(angle),
        upright_landmark=upright,
        rotated_landmark=rotated,
        sanity=sanity,
        center=center,
    )
