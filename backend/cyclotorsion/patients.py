"""Patient Profile store (PRD Epic 7 / TDD Section 3.2a).

This is a **deliberately separate database** from the BigQuery ``results`` /
``cases`` tables. Its access pattern — low-latency point lookups and prefix
search (US-7.2) plus transactional row-level CRUD and a correctness-critical
cascading delete (US-7.5) — is exactly what TDD Section 3.3 argues BigQuery is
the wrong tool for. The recommended production store is thus **Cloud SQL
(PostgreSQL)**, ``asia-south1``.

The ``patients`` table holds real patient identity by design (name, DOB, MRN,
optional phone) — this reverses the pre-Epic-7 "zero PII" stance for *this one
store only*. Every other store/surface (``results``, ``cases``, ``/stats``)
remains architecturally incapable of carrying patient-identifiable data.

Key properties:

  - **Facility scoping (US-7.6)** at the query layer: ``facility_id`` is on the
    patient row and every get/search/delete requires it, so a caller can only
    ever reach their own facility's patients.
  - **Consent (US-7.4)**: each patient row records who captured consent and
    when; a profile cannot be created without it.
  - **Erasure (US-7.5)**: ``patient_erasure_log`` is a separate, PII-free audit
    table that is *never* deleted, retaining the fact+actor+timestamp of each
    erasure so a DPDP compliance audit can always answer "was patient X's data
    erased, by whom, when" without holding X's data.

Cascading delete across the two stores (`patients` in Cloud SQL, `cases` /
`results` in BigQuery) has no database-enforced FK between them, so the
application orchestrates it (TDD Section 3.2a note). That orchestration lives
in :mod:`cyclotorsion.routers.patients`; this module owns only the Cloud SQL
row operations and the erasure-log write.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_PATIENTS_TABLE = "patients"
DEFAULT_ERASURE_LOG_TABLE = "patient_erasure_log"

# Open/dev default facility so the patient flow is testable end-to-end without
# Firebase (an unauthenticated UserContext carries facility_id=None). In
# enforced auth mode the caller's real facility is always used instead.
DEFAULT_DEV_FACILITY = "local"

# Defensive max lengths — the schema columns are unbounded TEXT in DDL, but the
# API should never accept unbounded free text in identity fields.
MAX_FULL_NAME_CHARS = 200
MAX_MRN_CHARS = 64
MAX_PHONE_CHARS = 32

_VALID_TABLE_RE = re.compile(r"^[\w.-]+$")

logger = logging.getLogger("cyclotorsion.patients")


@dataclass(frozen=True)
class PatientRow:
    """A row in the Cloud SQL ``patients`` table (TDD Section 3.2a).

    ``patient_id`` is a UUID. ``facility_id`` enforces US-7.6 scoping at the
    query layer. ``mrn`` is the hospital-assigned Medical Record Number and is
    unique *within a facility* (MRNs are not globally unique across hospitals).
    Consent fields (``consent_captured_by`` / ``consent_captured_at``) record
    the PRD US-7.4 lawful-processing basis.
    """

    patient_id: str
    full_name: str
    date_of_birth: str
    mrn: str
    facility_id: str
    created_by: str
    created_at: str
    consent_captured_by: str
    consent_captured_at: str
    phone: str | None = None


@dataclass(frozen=True)
class ErasureLogRow:
    """A row in the PII-free ``patient_erasure_log`` table (PRD US-7.5).

    Deliberately contains **no** identifiable field — only the fact and actor
    of deletion, and the counts of dependent rows erased. This table is never
    deleted, so a compliance audit can always confirm an erasure happened
    without that confirmation requiring the very data it attests was erased.
    """

    erasure_id: str
    patient_id: str
    requested_by: str
    facility_id: str
    erased_at: str
    cases_deleted: int
    results_deleted: int


class PatientStore(ABC):
    """Interface every patient-store backend implements.

    All operations are facility-scoped: the caller supplies the ``facility_id``
    they are authorized for (derived server-side from the authenticated user),
    and the store never returns or mutates a patient outside that facility
    (US-7.6). The cascade into ``cases``/``results`` is orchestrated by the
    router because it crosses into BigQuery.
    """

    @abstractmethod
    def create_patient(
        self,
        full_name: str,
        date_of_birth: str,
        mrn: str,
        facility_id: str,
        created_by: str,
        consent_captured_by: str,
        consent_captured_at: str,
        phone: str | None = None,
    ) -> PatientRow:
        """Create a patient; returns the new row (raises on duplicate MRN)."""

    @abstractmethod
    def get_patient(self, patient_id: str, facility_id: str) -> PatientRow | None:
        """Return the patient iff it exists AND belongs to ``facility_id``."""

    @abstractmethod
    def search_patients(
        self, facility_id: str, query: str, limit: int = 10
    ) -> list[PatientRow]:
        """Prefix search on MRN and name, scoped to ``facility_id``."""

    @abstractmethod
    def delete_patient(self, patient_id: str, facility_id: str) -> PatientRow | None:
        """Delete a patient (facility-scoped); return the removed row or None."""

    @abstractmethod
    def record_erasure(self, log: ErasureLogRow) -> None:
        """Append an erasure-log entry (never deletes this table)."""

    @abstractmethod
    def list_erasure_events(
        self, facility_id: str, limit: int = 50
    ) -> list[ErasureLogRow]:
        """Return recent erasure events for a facility."""


class InMemoryPatientStore(PatientStore):
    """Local/dev backend. Patients live in a dict for the process lifetime."""

    def __init__(self) -> None:
        self.patients: dict[str, PatientRow] = {}
        self.erasure_log: list[ErasureLogRow] = []

    def _check_unique_mrn(self, mrn: str, facility_id: str, exclude: str | None = None):
        for p in self.patients.values():
            if (
                p.mrn == mrn
                and p.facility_id == facility_id
                and p.patient_id != exclude
            ):
                raise ValueError(
                    "A patient with this MRN already exists in this facility"
                )

    def create_patient(
        self,
        full_name: str,
        date_of_birth: str,
        mrn: str,
        facility_id: str,
        created_by: str,
        consent_captured_by: str,
        consent_captured_at: str,
        phone: str | None = None,
    ) -> PatientRow:
        self._check_unique_mrn(mrn, facility_id)
        row = PatientRow(
            patient_id=str(uuid.uuid4()),
            full_name=full_name,
            date_of_birth=date_of_birth,
            mrn=mrn,
            facility_id=facility_id,
            created_by=created_by,
            created_at=datetime.now(UTC).isoformat(),
            consent_captured_by=consent_captured_by,
            consent_captured_at=consent_captured_at,
            phone=phone,
        )
        self.patients[row.patient_id] = row
        return row

    def get_patient(self, patient_id: str, facility_id: str) -> PatientRow | None:
        row = self.patients.get(patient_id)
        if row is None or row.facility_id != facility_id:
            return None
        return row

    def search_patients(
        self, facility_id: str, query: str, limit: int = 10
    ) -> list[PatientRow]:
        q = (query or "").strip().lower()
        if not q:
            return []
        results = [
            p
            for p in self.patients.values()
            if p.facility_id == facility_id
            and (p.full_name.lower().startswith(q) or p.mrn.lower().startswith(q))
        ]
        results.sort(key=lambda p: (p.full_name.lower(), p.mrn.lower()))
        return results[:limit]

    def delete_patient(self, patient_id: str, facility_id: str) -> PatientRow | None:
        row = self.patients.get(patient_id)
        if row is None or row.facility_id != facility_id:
            return None
        del self.patients[patient_id]
        return row

    def record_erasure(self, log: ErasureLogRow) -> None:
        self.erasure_log.append(log)

    def list_erasure_events(
        self, facility_id: str, limit: int = 50
    ) -> list[ErasureLogRow]:
        events = [e for e in self.erasure_log if e.facility_id == facility_id]
        events.sort(key=lambda e: e.erased_at, reverse=True)
        return events[:limit]


class PostgresPatientStore(PatientStore):
    """Production backend targeting Cloud SQL (PostgreSQL), asia-south1.

    Requires the ``psycopg`` package and a ``cloud_sql_connection_string``.
    The DDL matches TDD Section 3.2a (patients + patient_erasure_log + the two
    facility-scoped indexes). We use parameterized SQL exclusively so all input
    is bound, never interpolated.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS {patients} (
      patient_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      full_name       TEXT NOT NULL,
      date_of_birth   DATE NOT NULL,
      mrn             TEXT NOT NULL,
      phone           TEXT,
      facility_id     TEXT NOT NULL,
      created_by      TEXT NOT NULL,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
      consent_captured_by TEXT NOT NULL,
      consent_captured_at TIMESTAMPTZ NOT NULL,
      UNIQUE (facility_id, mrn)
    );
    CREATE INDEX IF NOT EXISTS idx_patients_facility_name
      ON {patients} (facility_id, full_name);
    CREATE INDEX IF NOT EXISTS idx_patients_facility_mrn
      ON {patients} (facility_id, mrn);
    CREATE TABLE IF NOT EXISTS {erasure_log} (
      erasure_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      patient_id      UUID NOT NULL,
      requested_by    TEXT NOT NULL,
      facility_id     TEXT NOT NULL,
      erased_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
      cases_deleted   INT NOT NULL,
      results_deleted INT NOT NULL
    );
    """

    def __init__(
        self,
        connection_string: str,
        patients_table: str = DEFAULT_PATIENTS_TABLE,
        erasure_log_table: str = DEFAULT_ERASURE_LOG_TABLE,
    ) -> None:
        if not _VALID_TABLE_RE.match(patients_table) or not _VALID_TABLE_RE.match(
            erasure_log_table
        ):
            raise ValueError(
                "Invalid Cloud SQL table identifier: "
                "expected only letters, digits, '.', '-', '_'."
            )
        self._connection_string = connection_string
        self._patients = patients_table
        self._erasure_log = erasure_log_table

    def _connect(self):
        import psycopg

        return psycopg.connect(self._connection_string)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    self._SCHEMA.format(
                        patients=self._patients, erasure_log=self._erasure_log
                    )
                )
            conn.commit()

    def create_patient(
        self,
        full_name: str,
        date_of_birth: str,
        mrn: str,
        facility_id: str,
        created_by: str,
        consent_captured_by: str,
        consent_captured_at: str,
        phone: str | None = None,
    ) -> PatientRow:
        self._init_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._patients}"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                    " (full_name, date_of_birth, mrn, phone, facility_id,"
                    "  created_by, created_at, consent_captured_by,"
                    "  consent_captured_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " RETURNING patient_id, created_at",
                    (
                        full_name,
                        date_of_birth,
                        mrn,
                        phone,
                        facility_id,
                        created_by,
                        datetime.now(UTC).isoformat(),
                        consent_captured_by,
                        consent_captured_at,
                    ),
                )
                patient_id, created_at = cur.fetchone()
            conn.commit()
            row = PatientRow(
                patient_id=str(patient_id),
                full_name=full_name,
                date_of_birth=date_of_birth,
                mrn=mrn,
                facility_id=facility_id,
                created_by=created_by,
                created_at=str(created_at),
                consent_captured_by=consent_captured_by,
                consent_captured_at=consent_captured_at,
                phone=phone,
            )
            return row

    def get_patient(self, patient_id: str, facility_id: str) -> PatientRow | None:
        self._init_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT patient_id, full_name, date_of_birth, mrn, phone,"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                f" facility_id, created_by, created_at, consent_captured_by,"
                f" consent_captured_at FROM {self._patients}"
                " WHERE patient_id = %s AND facility_id = %s",
                (patient_id, facility_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        cols = [
            "patient_id",
            "full_name",
            "date_of_birth",
            "mrn",
            "phone",
            "facility_id",
            "created_by",
            "created_at",
            "consent_captured_by",
            "consent_captured_at",
        ]
        return PatientRow(
            **dict(
                zip(cols, (str(v) if v is not None else None for v in row), strict=True)
            )
        )

    def search_patients(
        self, facility_id: str, query: str, limit: int = 10
    ) -> list[PatientRow]:
        self._init_schema()
        q = (query or "").strip().lower() + "%"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT patient_id, full_name, date_of_birth, mrn, phone,"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                f" facility_id, created_by, created_at, consent_captured_by,"
                f" consent_captured_at FROM {self._patients}"
                " WHERE facility_id = %s"
                " AND (LOWER(full_name) LIKE %s OR LOWER(mrn) LIKE %s)"
                " ORDER BY full_name, mrn LIMIT %s",
                (facility_id, q, q, int(limit)),
            )
            rows = cur.fetchall()
        cols = [
            "patient_id",
            "full_name",
            "date_of_birth",
            "mrn",
            "phone",
            "facility_id",
            "created_by",
            "created_at",
            "consent_captured_by",
            "consent_captured_at",
        ]
        return [
            PatientRow(
                **dict(
                    zip(
                        cols,
                        (str(v) if v is not None else None for v in r),
                        strict=True,
                    )
                )
            )
            for r in rows
        ]

    def delete_patient(self, patient_id: str, facility_id: str) -> PatientRow | None:
        self._init_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._patients}"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                    " WHERE patient_id = %s AND facility_id = %s"
                    " RETURNING full_name, date_of_birth, mrn",
                    (patient_id, facility_id),
                )
                deleted = cur.fetchone()
            conn.commit()
        if deleted is None:
            return None
        return PatientRow(
            patient_id=patient_id,
            full_name=deleted[0],
            date_of_birth=str(deleted[1]),
            mrn=deleted[2],
            facility_id=facility_id,
            created_by="",
            created_at="",
            consent_captured_by="",
            consent_captured_at="",
        )

    def record_erasure(self, log: ErasureLogRow) -> None:
        self._init_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._erasure_log}"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                    " (erasure_id, patient_id, requested_by, facility_id,"
                    "  erased_at, cases_deleted, results_deleted)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        log.erasure_id,
                        log.patient_id,
                        log.requested_by,
                        log.facility_id,
                        log.erased_at,
                        log.cases_deleted,
                        log.results_deleted,
                    ),
                )
            conn.commit()

    def list_erasure_events(
        self, facility_id: str, limit: int = 50
    ) -> list[ErasureLogRow]:
        self._init_schema()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT erasure_id, patient_id, requested_by, facility_id,"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                f" erased_at, cases_deleted, results_deleted"
                f" FROM {self._erasure_log} WHERE facility_id = %s"
                " ORDER BY erased_at DESC LIMIT %s",
                (facility_id, int(limit)),
            )
            rows = cur.fetchall()
        cols = [
            "erasure_id",
            "patient_id",
            "requested_by",
            "facility_id",
            "erased_at",
            "cases_deleted",
            "results_deleted",
        ]
        return [
            ErasureLogRow(
                **dict(
                    zip(
                        cols,
                        (
                            (
                                int(v)
                                if k in {"cases_deleted", "results_deleted"}
                                and v is not None
                                else str(v)
                            )
                            if v is not None
                            else v
                            for k, v in zip(cols, r, strict=True)
                        ),
                        strict=True,
                    )
                )
            )
            for r in rows
        ]


def patient_store_from_config():
    """Build a patient store from config (``patient_store_mode``).

    - ``memory`` -> InMemoryPatientStore (local/dev).
    - ``cloudsql`` -> PostgresPatientStore (production).
    - ``auto`` -> Postgres when a connection string is configured, else memory.
    """
    from cyclotorsion.config import cfg

    mode = getattr(cfg, "patient_store_mode", "memory")
    conn = getattr(cfg, "cloud_sql_connection_string", "")
    if mode == "cloudsql" or (mode == "auto" and conn):
        return PostgresPatientStore(conn)
    return InMemoryPatientStore()


def new_erasure_id() -> str:
    return str(uuid.uuid4())


def generate_mrn_suggestion() -> str:
    """A placeholder MRN the create-patient form can pre-fill (US-7.1).

    Purely a convenience default for when the surgeon doesn't have the
    patient's real hospital-assigned MRN in front of them yet — the field
    stays fully editable client-side, and this is never a substitute for the
    hospital's own record number once it's known. Uniqueness is still
    enforced at write time by the facility-scoped ``UNIQUE(facility_id,
    mrn)`` constraint; a rare collision here just surfaces the existing
    "already exists" 409 on create, unchanged.
    """
    date_part = datetime.now(UTC).strftime("%Y%m%d")
    suffix = secrets.token_hex(2).upper()
    return f"MRN-{date_part}-{suffix}"
