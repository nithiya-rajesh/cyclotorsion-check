"""Public API of the CyclotorsionCheck Python package.

Re-exports the stable classes/functions that other modules, tests, and the
local evaluation scripts import. Keeping the public surface small makes the
package easier to consume and reason about.
"""

from cyclotorsion.geometry import (
    Point,
    corrected_axis_deg,
    cyclotorsion_deg,
    round_to_two,
    vector_angle_deg,
)
from cyclotorsion.sanity import (
    FLAG_NEAR_ZERO,
    FLAG_TOO_LARGE,
    SanityResult,
    evaluate_angle,
    human_message,
)

__all__ = [
    "Point",
    "SanityResult",
    "corrected_axis_deg",
    "cyclotorsion_deg",
    "evaluate_angle",
    "human_message",
    "round_to_two",
    "vector_angle_deg",
    "FLAG_NEAR_ZERO",
    "FLAG_TOO_LARGE",
]
