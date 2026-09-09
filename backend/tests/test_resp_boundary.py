"""Tests for the central response/error boundary (architecture review Major)."""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from cyclotorsion.resp_boundary import (
    RESPONSE_ALLOW_LIST,
    assert_allowed_fields,
    register_exception_handlers,
    sanitize_detail,
)


def test_sanitize_detail_scrubs_internal_fragments():
    assert "projects/" not in sanitize_detail("failed gcloud projects/foo/bar")
    assert "SELECT" not in sanitize_detail("bad SELECT FROM table")
    assert "Traceback" not in sanitize_detail("Traceback (most recent call last)")
    # Non-marked, intentional user messages pass through unchanged.
    assert sanitize_detail("Both an upright and a supine image are required") == (
        "Both an upright and a supine image are required"
    )


def test_sanitize_detail_handles_structured_422_like_detail():
    detail = [{"loc": ["body", "x"], "msg": "gcloud projects/foo crashed SELECT"}]
    out = sanitize_detail(detail)
    assert isinstance(out, list)
    # Every string leaf is scrubbed; structure preserved.
    assert "projects/" not in str(out)
    assert out[0]["loc"] == ["body", "x"]


def test_response_allow_list_has_expected_fields():
    for field in (
        "angle_deg",
        "warning",
        "test_id",
        "total_tests",
        "analytics_warning",
        "facility_id",
    ):
        assert field in RESPONSE_ALLOW_LIST


def test_assert_allowed_fields_rejects_unlisted_field():
    import pytest

    with pytest.raises(AssertionError):
        assert_allowed_fields({"angle_deg": 1.0, "internal_debug_trace": "x"})


def test_assert_allowed_fields_accepts_nested_allowed_payload():
    assert_allowed_fields(
        [{"uid": "u1", "role": "surgeon", "facility_id": None, "approved": True}]
    )


def test_success_responses_match_allow_list():
    # staff review Minor: RESPONSE_ALLOW_LIST was documentation only. This
    # walks real success response bodies from every endpoint and fails CI if
    # a field slips in that isn't on the reviewed allow-list.
    from fastapi.testclient import TestClient

    from cyclotorsion.app import app, flush_analytics
    from generate_synthetic_data import generate_pair

    client = TestClient(app)
    upright, _, _ = generate_pair(0.0, 128)

    assert_allowed_fields(client.get("/health").json(), context="/health")

    detect_resp = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", upright, "image/png")),
            ("rotated", ("r.png", upright, "image/png")),
        ],
    )
    assert detect_resp.status_code == 200
    assert_allowed_fields(detect_resp.json(), context="/detect")

    flush_analytics()
    assert_allowed_fields(client.get("/stats").json(), context="/stats")
    assert_allowed_fields(client.get("/admin/users").json(), context="/admin/users")


def _make_app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise HTTPException(
            status_code=500,
            detail="internal gcloud projects/secret-proj/zones/us-central1 SELECT ...",
        )

    @app.get("/scoped")
    def scoped():
        raise HTTPException(status_code=409, detail="order conflict")

    @app.get("/validated")
    def validated():
        raise RequestValidationError(
            [
                {
                    "loc": ["body", "x"],
                    "msg": "gcloud projects/leak SELECT ...",
                    "type": "x",
                }
            ]
        )

    return app


def test_exception_handler_scrubs_raised_detail():
    client = TestClient(_make_app())
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert "projects/" not in str(body)
    assert "SELECT" not in str(body)
    assert body["detail"] == "Request could not be processed"


def test_exception_handler_preserves_intentional_detail():
    client = TestClient(_make_app())
    r = client.get("/scoped")
    assert r.status_code == 409
    assert r.json()["detail"] == "order conflict"


def test_validation_handler_scrubs_details_but_keeps_structure():
    client = TestClient(_make_app())
    r = client.get("/validated")
    assert r.status_code == 422
    body = r.json()
    assert "projects/" not in str(body)
    assert "SELECT" not in str(body)
