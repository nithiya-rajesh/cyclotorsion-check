"""Synthetic eye-image generator (PRD TDD Appendix A.2).

Generates paired eye photographs with a *known* ground-truth rotation angle.
This is the only practical way to obtain ground-truth-labeled test data, since
no public dataset of paired pre-op/intra-op eye rotation photos exists and real
patient data is out of scope for development.

Approach:
  - Draw a simple synthetic "eye" (a blurred ring for the iris, a dark pupil
    disk) plus a distinct bright marker at a known polar position.
  - The "upright" image is drawn as-is; the "rotated" image is the same scene
    rotated by ``angle_deg`` around the image center.
  - The mock landmark detector finds the bright marker, so its detected
    rotation should approximate the injected ground-truth angle — letting the
    evaluation script measure pipeline accuracy (PRD Section 1.6 / A.1).
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

DEFAULT_SIZE = 512


def _eye_base(size: int) -> Image.Image:
    from PIL import ImageFilter

    img = Image.new("RGB", (size, size), (220, 220, 220))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2.0
    r_outer = size * 0.38
    # Iris ring.
    d.ellipse(
        [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], fill=(120, 90, 60)
    )
    # Pupil.
    r_pupil = size * 0.16
    d.ellipse([cx - r_pupil, cy - r_pupil, cx + r_pupil, cy + r_pupil], fill=(0, 0, 0))
    return img.filter(ImageFilter.GaussianBlur(1.2))


def _place_marker(
    img: Image.Image, polar_angle_deg: float, radius_frac: float = 0.30
) -> tuple[int, int]:
    """Draw a bright marker at ``polar_angle_deg`` (y-down convention) and return pixel coords."""
    size = img.size[0]
    cx = cy = size / 2.0
    r = radius_frac * size
    # y-down: positive angle moves the marker "clockwise" as displayed.
    x = cx + r * math.cos(math.radians(polar_angle_deg))
    y = cy + r * math.sin(math.radians(polar_angle_deg))
    d = ImageDraw.Draw(img)
    mr = size * 0.03
    d.ellipse([x - mr, y - mr, x + mr, y + mr], fill=(255, 255, 255))
    return int(x), int(y)


def generate_pair(
    angle_deg: float, size: int = DEFAULT_SIZE, out_dir: str | Path | None = None
) -> tuple[bytes, bytes, float]:
    """Return (upright_bytes, rotated_bytes, ground_truth_angle).

    The upright image places the marker at a random reference polar angle; the
    rotated image applies the requested rotation, enabling exact ground truth.
    """
    base = _eye_base(size)
    ref_angle = random.uniform(-160, 160)
    _place_marker(base, ref_angle)

    rotated = base.rotate(
        -angle_deg, resample=Image.BICUBIC, center=(size / 2, size / 2)
    )
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        base.save(out_dir / "upright.png")
        rotated.save(out_dir / "rotated.png")

    import io

    buf = io.BytesIO()
    base.save(buf, format="PNG")
    upright_bytes = buf.getvalue()
    buf = io.BytesIO()
    rotated.save(buf, format="PNG")
    rotated_bytes = buf.getvalue()
    return upright_bytes, rotated_bytes, angle_deg
