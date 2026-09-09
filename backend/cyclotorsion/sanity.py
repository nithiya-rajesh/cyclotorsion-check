"""Sanity validation engine: rule-based plausibility checks.

Read-only with respect to the calculated angle — it flags, never modifies, the
result (TDD Section 2.2). This corresponds to PRD US-3.1 / Epic 3: prevent
physically implausible results from being presented as trustworthy.

Rules are deliberately kept here in one place so that the safety logic is
testable in isolation and defensible in procurement review (US-3.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Flags produced by the sanity engine.
FLAG_TOO_LARGE = "angle_too_large"
FLAG_NEAR_ZERO = "angle_near_zero"

# Thresholds from PRD US-3.1.
MAX_PLAUSIBLE_ABS_DEG = 20.0
NEAR_ZERO_THRESHOLD_DEG = 0.1


@dataclass(frozen=True)
class SanityResult:
    """Outcome of running the sanity engine on a calculated angle."""

    passed: bool
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def flags_csv(self) -> str:
        """Comma-joined flag string for persistence (TDD Section 3.2)."""
        return ",".join(self.flags) if self.flags else ""


def evaluate_angle(angle_deg: float) -> SanityResult:
    """Evaluate ``angle_deg`` against the plausible-range rules.

    Returns ``SanityResult(passed=True)`` for an in-range angle, or sets the
    appropriate flag(s) for out-of-range angles. An angle can only trigger one
    of the two rules (they are mutually exclusive), but the method generalizes
    if more rules are added later.
    """
    flags: list[str] = []
    abs_angle = abs(angle_deg)

    if abs_angle > MAX_PLAUSIBLE_ABS_DEG:
        flags.append(FLAG_TOO_LARGE)
    elif abs_angle < NEAR_ZERO_THRESHOLD_DEG:
        flags.append(FLAG_NEAR_ZERO)

    return SanityResult(passed=not flags, flags=tuple(flags))


def human_message(flags: tuple[str, ...]) -> str | None:
    """Return the surgeon-facing warning label for a set of flags.

    Mirrors the copy specified in PRD US-3.1. Returns None when there is
    nothing to flag.
    """
    if not flags:
        return None
    if FLAG_TOO_LARGE in flags:
        return "Flagged — unusually large, verify manually"
    if FLAG_NEAR_ZERO in flags:
        return "Flagged — angle near zero, confirm photos are distinct"
    return "Flagged — result failed sanity check"
