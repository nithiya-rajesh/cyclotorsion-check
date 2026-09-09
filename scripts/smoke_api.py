"""End-to-end smoke test: SPA <-> backend API contract.

Exercises /health, /detect, and /stats exactly as the SPA's js/api.js does
(multipart field names `upright`/`rotated`, Bearer token attachment, JSON
shapes). Requires the backend running in open or mock mode.

Usage:
    python scripts/smoke_api.py [base_url] [--with-auth-token TOKEN]
"""

import argparse
import io
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.generate_synthetic_data import generate_pair  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8080")
    ap.add_argument("--with-auth-token", default=None)
    args = ap.parse_args()

    base = args.base.rstrip("/")
    headers = {}
    if args.with_auth_token:
        headers["Authorization"] = f"Bearer {args.with_auth_token}"

    # /health (always open)
    r = requests.get(f"{base}/health", timeout=10)
    print("GET /health          ->", r.status_code, r.json().get("status"))
    assert r.status_code == 200, "health should be open and 200"

    # /detect with a synthetic pair (mock mode returns a plausible angle)
    upright, rotated, truth = generate_pair(angle_deg=5.0)
    files = {
        "upright": ("upright.png", io.BytesIO(upright), "image/png"),
        "rotated": ("rotated.png", io.BytesIO(rotated), "image/png"),
    }
    r = requests.post(f"{base}/detect", files=files, headers=headers, timeout=30)
    print("POST /detect        ->", r.status_code)
    if args.with_auth_token:
        assert r.status_code == 200, (
            f"detect failed with token: {r.status_code} {r.text}"
        )
    else:
        assert r.status_code in (200,), f"detect failed: {r.status_code} {r.text}"
    body = r.json()
    print("  angle_deg         ->", body.get("angle_deg"))
    print("  passed_sanity     ->", body.get("passed_sanity_check"))
    print("  test_id           ->", body.get("test_id"))
    for k in ("angle_deg", "passed_sanity_check", "test_id"):
        assert k in body, f"missing field {k} in detect response"

    # /stats
    r = requests.get(f"{base}/stats", headers=headers, timeout=15)
    print("GET /stats          ->", r.status_code, r.json())
    assert r.status_code == 200, "stats failed"

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
