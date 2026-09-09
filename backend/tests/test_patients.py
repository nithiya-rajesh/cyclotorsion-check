"""Epic 7 (Patient Profile) tests: /patients/* CRUD, search, consent, erasure.

Uses FastAPI's TestClient against the in-memory patient store and in-memory
writer (default open/dev config -> facility "local"). Where a test needs a
real write to the writer/cases, it swaps in an isolated InMemoryWriter and a
fresh BackgroundWorker, then flushes to make read-after-write deterministic —
the same harness pattern used by test_api.py.
"""

import pytest
from fastapi.testclient import TestClient

from cyclotorsion.app import app

client = TestClient(app)


def _png_bytes() -> bytes:
    from generate_synthetic_data import generate_pair

    upright, _, _ = generate_pair(0.0, 128)
    return upright


def _create(monkeypatch) -> tuple[dict, object]:
    """Swap in isolated patient store + writer + worker; return (response, writer)."""
    from cyclotorsion import app as app_mod
    from cyclotorsion.background import BackgroundWorker
    from cyclotorsion.patients import InMemoryPatientStore
    from cyclotorsion.storage import InMemoryWriter

    store = InMemoryPatientStore()
    writer = InMemoryWriter()
    worker = BackgroundWorker()
    monkeypatch.setattr(app_mod, "_PATIENT_STORE", store)
    monkeypatch.setattr(app_mod, "_WRITER", writer)
    monkeypatch.setattr(app_mod, "_ANALYTICS_WORKER", worker)
    return store, writer


def _new_patient(monkeypatch, **overrides) -> dict:
    # Assumes _create(monkeypatch) was already called in this test so the
    # patient store is monkeypatched. Only posts the create request.
    body = {
        "full_name": "Anjali Rao",
        "date_of_birth": "1985-04-12",
        "mrn": "MRN-1001",
        "consent": True,
    }
    body.update(overrides)
    r = client.post("/patients", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_patient_requires_consent(monkeypatch):
    _create(monkeypatch)
    r = client.post(
        "/patients",
        json={
            "full_name": "Anjali Rao",
            "date_of_birth": "1985-04-12",
            "mrn": "MRN-1001",
        },
    )
    assert r.status_code == 422
    assert "consent" in r.json()["detail"].lower()


def test_create_patient_success_us71(monkeypatch):
    store, _ = _create(monkeypatch)
    body = _new_patient(monkeypatch)
    assert "patient_id" in body
    assert body["full_name"] == "Anjali Rao"
    assert body["facility_id"] == "local"
    assert body["consent_captured_by"] == "local"
    # persisted in the store, facility-scoped (US-7.6)
    assert store.get_patient(body["patient_id"], "local") is not None


def test_create_patient_duplicate_mrn_conflict(monkeypatch):
    _create(monkeypatch)
    _new_patient(monkeypatch, mrn="MRN-1001")
    r = client.post(
        "/patients",
        json={
            "full_name": "Another Person",
            "date_of_birth": "1990-01-01",
            "mrn": "MRN-1001",
            "consent": True,
        },
    )
    assert r.status_code == 409


def test_create_patient_invalid_dob(monkeypatch):
    _create(monkeypatch)
    r = client.post(
        "/patients",
        json={
            "full_name": "X",
            "date_of_birth": "not-a-date",
            "mrn": "MRN-9",
            "consent": True,
        },
    )
    assert r.status_code == 422


def test_search_by_name_and_mrn_us72(monkeypatch):
    _create(monkeypatch)
    _new_patient(monkeypatch, full_name="Anjali Rao", mrn="MRN-1001")
    _new_patient(monkeypatch, full_name="Ravi Kumar", mrn="MRN-1002")

    by_name = client.get("/patients/search", params={"q": "Anj"}).json()
    assert len(by_name["results"]) == 1
    assert by_name["results"][0]["full_name"] == "Anjali Rao"

    by_mrn = client.get("/patients/search", params={"q": "MRN-1002"}).json()
    assert len(by_mrn["results"]) == 1
    assert by_mrn["results"][0]["mrn"] == "MRN-1002"


def test_suggest_mrn_returns_unique_looking_placeholder(monkeypatch):
    _create(monkeypatch)
    r1 = client.get("/patients/suggest-mrn")
    r2 = client.get("/patients/suggest-mrn")
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    mrn1 = r1.json()["mrn"]
    mrn2 = r2.json()["mrn"]
    assert mrn1.startswith("MRN-")
    # Two separate calls should not collide (random suffix per call).
    assert mrn1 != mrn2


def test_suggest_mrn_is_only_a_default_not_enforced(monkeypatch):
    # The suggested value is never required or checked against on create —
    # any facility-unique string is still accepted (US-7.1 stays optional
    # about where the MRN came from).
    _create(monkeypatch)
    suggestion = client.get("/patients/suggest-mrn").json()["mrn"]
    body = _new_patient(monkeypatch, mrn=suggestion)
    assert body["mrn"] == suggestion
    # A surgeon typing their own real hospital MRN instead works identically.
    other = _new_patient(monkeypatch, mrn="REAL-HOSPITAL-MRN-77")
    assert other["mrn"] == "REAL-HOSPITAL-MRN-77"


def test_get_patient_facility_scoped_us76(monkeypatch):
    store, _ = _create(monkeypatch)
    body = _new_patient(monkeypatch)
    # same-facility read succeeds
    ok = client.get(f"/patients/{body['patient_id']}")
    assert ok.status_code == 200
    # a non-existent patient is 404
    assert (
        client.get("/patients/00000000-0000-0000-0000-000000000000").status_code == 404
    )


def test_patient_cases_history_us73(monkeypatch):
    store, writer = _create(monkeypatch)
    pat = _new_patient(monkeypatch)
    # run a detect linked to this patient+case_ref, then flush
    from cyclotorsion.app import flush_analytics

    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={"case_ref": "CASE-EPIC7", "patient_id": pat["patient_id"]},
    )
    assert r.status_code == 200, r.text
    flush_analytics()
    assert writer.cases["CASE-EPIC7"].patient_id == pat["patient_id"]

    hist = client.get(f"/patients/{pat['patient_id']}/cases").json()
    assert hist["case_refs"] == ["CASE-EPIC7"]
    assert len(hist["results"]) == 1
    assert hist["results"][0]["case_ref"] == "CASE-EPIC7"


def test_detect_rejects_unknown_patient_id(monkeypatch):
    _create(monkeypatch)
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={"case_ref": "C", "patient_id": "00000000-0000-0000-0000-000000000000"},
    )
    # logical-FK validation (TDD 3.2a): unknown patient in this facility -> 404
    assert r.status_code == 404


def test_erase_cascades_and_logs_us75(monkeypatch):
    store, writer = _create(monkeypatch)
    pat = _new_patient(monkeypatch)
    from cyclotorsion.app import flush_analytics

    data = _png_bytes()
    client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={"case_ref": "CASE-ERASE", "patient_id": pat["patient_id"]},
    )
    flush_analytics()

    r = client.delete(f"/patients/{pat['patient_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["erasure_recorded"] is True
    assert body["cases_deleted"] == 1
    assert body["results_deleted"] == 1

    # patient row gone
    assert store.get_patient(pat["patient_id"], "local") is None
    # case + results gone from writer
    assert "CASE-ERASE" not in writer.cases
    assert all(rr.case_ref != "CASE-ERASE" for rr in writer.rows)
    # erasure log retained (never deleted) with counts only
    events = store.list_erasure_events("local")
    assert len(events) == 1
    assert events[0].cases_deleted == 1
    assert events[0].results_deleted == 1


def test_erasure_log_endpoint_us75(monkeypatch):
    store, _ = _create(monkeypatch)
    pat = _new_patient(monkeypatch)
    client.delete(f"/patients/{pat['patient_id']}")
    r = client.get("/admin/erasure-log")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 1
    assert events[0]["patient_id"] == pat["patient_id"]


def test_erase_foreign_facility_not_found(monkeypatch):
    store, _ = _create(monkeypatch)
    pat = _new_patient(monkeypatch)
    # A patient created under "local"; erase from a different facility identity
    # isn't expressible in open mode, but an unknown id must 404 and erase nothing.
    r = client.delete("/patients/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert store.get_patient(pat["patient_id"], "local") is not None


def test_erase_is_admin_role_gated_when_auth_enabled(monkeypatch):
    # US-7.5 is facility-admin only: under auth-enabled config, a surgeon token
    # must be denied erasure (TDD 5.2 "Added by PRD Epic 7" — program_officer
    # and surgeon may not reach patient-destructive endpoints).
    from fastapi import HTTPException

    from cyclotorsion import auth as auth_mod
    from cyclotorsion.auth import (
        ROLE_FACILITY_ADMIN,
        ROLE_SURGEON,
        UserContext,
        require_any_role,
    )

    class _Cfg:
        auth_enabled = True

    monkeypatch.setattr(auth_mod, "cfg", _Cfg())

    admin_dep = require_any_role(ROLE_FACILITY_ADMIN)
    surgeon = UserContext(uid="u1", role=ROLE_SURGEON, facility_id="fac")

    with pytest.raises(HTTPException) as exc:
        admin_dep(user=surgeon)
    assert exc.value.status_code == 403


def test_erase_route_is_admin_only():
    # The DELETE /patients/{patient_id} route must require facility_admin —
    # guarded against a future refactor silently widening who can erase.
    from cyclotorsion.auth import ROLE_FACILITY_ADMIN
    from cyclotorsion.routers.patients import router

    target = None
    for route in router.routes:
        if route.path == "/patients/{patient_id}" and "DELETE" in route.methods:
            target = route
    assert target is not None

    # require_any_role(role) returns a closure whose ``allowed`` cell holds the
    # exact frozenset of permitted roles. Assert one such dependency allows
    # facility_admin (and is the only role on the destructive route).
    dep_calls = [
        d.call for d in target.dependant.dependencies if getattr(d, "call", None)
    ]
    allowed_sets = [
        cell.cell_contents
        for c in dep_calls
        if getattr(c, "__closure__", None)
        for cell in c.__closure__
        if isinstance(cell.cell_contents, frozenset)
        and cell.cell_contents == {ROLE_FACILITY_ADMIN}
    ]
    assert allowed_sets, "erase route must require facility_admin"
