"""CyclotorsionCheck backend package.

A low-cost, AI-powered clinical decision-support tool that verifies toric IOL
alignment by computing cyclotorsion between two eye photographs.

Design principle (TDD Section 2.2): the AI only locates anatomical landmarks;
the rotation angle is always computed with deterministic arithmetic (arctan2),
never estimated by a language model. See the PRD for the clinical rationale.
"""

__version__ = "0.1.0"
