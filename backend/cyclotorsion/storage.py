"""Best-effort, non-blocking persistence of aggregate result metadata.

Per TDD Section 2.1/3.4, the analytics write is deliberately decoupled from the
critical path: a failure here must never block or corrupt the clinical result
returned to the surgeon. The writer surfaces failures as a ``logging_warning``
field rather than a request failure.

Two backends:

  - ``BigQueryWriter``: production (writes to the ``results`` table in
    ``asia-south1``). Stubbed to avoid requiring GCP credentials in local/dev
    runs — the schema and insert contract match TDD Section 3.2.
  - ``InMemoryWriter``: local/dev fallback that keeps rows in memory so the
    ``/stats`` endpoint works end to end without BigQuery.
"""

from __future__ import annotations

import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

DEFAULT_TABLE = "cyclotorsion_check.results"
DEFAULT_CASES_TABLE = "cyclotorsion_check.cases"

# Defense-in-depth for the f-string table-name interpolation in
# BigQueryWriter.read_rows (staff review Minor): the table name is always
# operator-controlled config (CC_BIGQUERY_TABLE), never per-request input, so
# it is not exploitable today — but this enforces that invariant at
# construction time instead of relying on "nothing calls this differently".
_VALID_TABLE_RE = re.compile(r"^[\w.-]+$")

logger = logging.getLogger("cyclotorsion.storage")


@dataclass(frozen=True)
class ResultRow:
    """A row in the ``results`` table (mirrors TDD Section 3.2 schema).

    Note: there is deliberately NO patient-identifiable field here — only
    ``test_id`` (random UUID), ``user_uid`` (Firebase UID of the operating
    clinician, not a patient id), numeric angle, landmark descriptions, and
    pass/fail flags.

    ``case_ref`` (PRD Epic 6) is a facility-controlled **pseudonymous** code
    that the facility alone can map back to its own patient chart — it is
    meaningless (and therefore non-identifying) to this system, exactly like
    the case context itself. The case-level context (eye laterality, planned
    target axis) lives in the separate ``cases`` table, linked by ``case_ref``,
    per the one-to-many modeling decision in TDD Section 3.2.
    """

    test_id: str
    timestamp: str
    angle_deg: float
    upright_landmark: str
    rotated_landmark: str
    passed_sanity_check: bool
    sanity_flags: str
    facility_id: str | None = None
    user_uid: str | None = None
    case_ref: str | None = None


@dataclass(frozen=True)
class CaseRow:
    """A row in the ``cases`` table (PRD Epic 6 / TDD Section 3.2).

    Keyed by the facility's own pseudonymous ``case_ref``. This table holds the
    *case-level* context — which eye (OD/OS) and the planned toric-IOL target
    axis from the patient's own pre-op biometry — so that context is not
    duplicated across every detection attempt (one case -> many results rows).

    ``case_ref`` is a facility-generated code, **never** a patient identity
    field (PRD US-6.4). ``created_by`` is the Firebase UID of the surgeon, not
    a patient identifier.
    """

    case_ref: str
    created_by: str
    created_at: str
    eye_laterality: str | None = None
    target_axis_deg: float | None = None
    iol_model: str | None = None
    facility_id: str | None = None
    # PRD Epic 7 (TDD 3.2a): logical FK to Cloud SQL patients.patient_id.
    # Validated by the application layer to exist in the same facility before
    # the case is written (there is no enforced FK across the two stores).
    patient_id: str | None = None


class ResultWriter(ABC):
    """Interface every persistence backend implements."""

    @abstractmethod
    def write_row(self, row: ResultRow) -> Exception | None:
        """Persist ``row``; return the exception on failure, else None."""

    @abstractmethod
    def upsert_case(self, case: CaseRow) -> Exception | None:
        """Create-or-replace ``case`` (keyed by ``case_ref``); return error.

        A retry of the same case (one case -> many results rows) updates the
        existing case row rather than duplicating it (TDD Section 3.2).
        """

    @abstractmethod
    def read_rows(
        self, facility_id: str | None = None
    ) -> tuple[list[ResultRow], Exception | None]:
        """Return persisted rows (optionally filtered to ``facility_id``) plus
        any read error.

        ``(rows, error)``: on success ``error is None``; on failure rows is
        empty and ``error`` identifies the cause so callers can distinguish
        "no data" from "analytics degraded" (architecture review Major) instead
        of silently reporting zero tests. Backends must return rows
        de-identified by construction (see ``ResultRow``).
        """

    @abstractmethod
    def case_refs_for_patient(
        self, patient_id: str, facility_id: str
    ) -> tuple[list[str], Exception | None]:
        """Return the ``case_ref`` values whose case row links to ``patient_id``
        (PRD Epic 7 / TDD Section 3.2a cascading erasure step 1). Facility
        scoped. ``([], error)`` on failure so the erasure handler can retry."""

    @abstractmethod
    def delete_cases_for_patient(
        self, patient_id: str, facility_id: str
    ) -> tuple[int, int, Exception | None]:
        """Delete all ``results`` then ``cases`` rows linked to ``patient_id``
        (TDD Section 3.2a cascading erasure step 2). Returns
        ``(cases_deleted, results_deleted, error)``. Best-effort is NOT
        acceptable here (an incomplete erasure is a DPDP compliance failure), so
        callers must retry until ``error is None``."""


class InMemoryWriter(ResultWriter):
    """Local/dev backend. Rows live in memory for the lifetime of the process."""

    def __init__(self) -> None:
        self.rows: list[ResultRow] = []
        self.cases: dict[str, CaseRow] = {}

    def write_row(self, row: ResultRow) -> None:
        self.rows.append(row)
        return None

    def upsert_case(self, case: CaseRow) -> None:
        # One case -> many results rows: a repeated case_ref updates in place.
        self.cases[case.case_ref] = case
        return None

    def read_rows(
        self, facility_id: str | None = None
    ) -> tuple[list[ResultRow], Exception | None]:
        if facility_id is None:
            return list(self.rows), None
        return (
            [r for r in self.rows if r.facility_id == facility_id],
            None,
        )

    def case_refs_for_patient(
        self, patient_id: str, facility_id: str
    ) -> tuple[list[str], Exception | None]:
        # patient_id is a globally-unique UUID owned by exactly one facility, so
        # it is a sufficient key for finding linked cases. The facility_id param
        # keeps the signature identical to the BigQuery backend (which scopes in
        # SQL); in local/dev the caller's effective facility may differ from the
        # None stamped on the case row, so we deliberately match on patient_id.
        refs = [c.case_ref for c in self.cases.values() if c.patient_id == patient_id]
        return refs, None

    def delete_cases_for_patient(
        self, patient_id: str, facility_id: str
    ) -> tuple[int, int, Exception | None]:
        refs, err = self.case_refs_for_patient(patient_id, facility_id)
        if err is not None:
            return 0, 0, err
        before_cases = len(self.cases)
        self.cases = {k: v for k, v in self.cases.items() if v.patient_id != patient_id}
        cases_deleted = before_cases - len(self.cases)
        ref_set = set(refs)
        before_results = len(self.rows)
        self.rows = [r for r in self.rows if r.case_ref not in ref_set]
        results_deleted = before_results - len(self.rows)
        return cases_deleted, results_deleted, None


class BigQueryWriter(ResultWriter):
    """Production backend targeting the BigQuery ``results`` table.

    Requires the ``google-cloud-bigquery`` package and default credentials.
    Any error (missing creds, network, schema drift) is caught and returned as
    a warning so the caller's critical path is never interrupted.
    """

    def __init__(
        self, table: str = DEFAULT_TABLE, cases_table: str = DEFAULT_CASES_TABLE
    ) -> None:
        if not _VALID_TABLE_RE.match(table):
            raise ValueError(
                "Invalid BigQuery table identifier "
                f"{table!r}: expected only letters, digits, '.', '-', '_' "
                "(CC_BIGQUERY_TABLE / DEFAULT_TABLE)."
            )
        if not _VALID_TABLE_RE.match(cases_table):
            raise ValueError(
                "Invalid BigQuery cases-table identifier "
                f"{cases_table!r}: expected only letters, digits, '.', '-', '_'."
            )
        self._table = table
        self._cases_table = cases_table
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google.cloud import bigquery

                self._client = bigquery.Client()
            except Exception as exc:  # noqa: BLE001 - best-effort by design
                logger.warning(
                    "storage.bigquery_client_init_failed",
                    extra={
                        "event": "storage.bigquery_client_init_failed",
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
                raise
        return self._client

    def write_row(self, row: ResultRow) -> Exception | None:
        try:
            client = self._get_client()
            insert_errors = client.insert_rows_json(self._table, [asdict(row)])
            # insert_rows_json() does NOT raise for a row-level failure (schema
            # mismatch, type coercion failure, etc.) — it returns a non-empty
            # list of per-row error dicts instead, with the call itself
            # completing "successfully". Treating an unchecked empty return as
            # success silently dropped every row once the table schema drifted
            # from ResultRow (confirmed against a live deployment: a missing
            # case_ref column made every write vanish with no exception, no
            # log line, nothing — the row was simply gone).
            if insert_errors:
                raise RuntimeError(f"BigQuery rejected row(s): {insert_errors!r}")
            return None
        except Exception as exc:  # noqa: BLE001 - intentionally broad
            logger.warning(
                "storage.bigquery_write_failed",
                extra={
                    "event": "storage.bigquery_write_failed",
                    "error_type": type(exc).__name__,
                    "table": self._table,
                },
                exc_info=True,
            )
            return exc

    def upsert_case(self, case: CaseRow) -> Exception | None:
        """Create-or-replace ``case`` in the ``cases`` table (TDD Section 3.2).

        A MERGE keyed on ``case_ref`` so a retry of the same case (one case ->
        many results rows) updates the existing row instead of duplicating
        case-level data across attempts. Best-effort like ``write_row``: any
        error (missing creds, network, schema drift) is returned, never raised
        into the caller's hot path.
        """
        from google.cloud import bigquery

        try:
            client = self._get_client()
            field = bigquery.ScalarQueryParameter
            row = asdict(case)
            params = [
                field("case_ref", "STRING", row["case_ref"]),
                field("eye_laterality", "STRING", row["eye_laterality"]),
                field("target_axis_deg", "FLOAT64", row["target_axis_deg"]),
                field("iol_model", "STRING", row["iol_model"]),
                field("created_by", "STRING", row["created_by"]),
                field("created_at", "STRING", row["created_at"]),
                field("facility_id", "STRING", row["facility_id"]),
                field("patient_id", "STRING", row["patient_id"]),
            ]
            job_config = bigquery.QueryJobConfig(
                query_parameters=params, use_legacy_sql=False
            )
            merge_sql = (
                f"MERGE `{self._cases_table}` T USING (SELECT"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                " @case_ref AS case_ref, @eye_laterality AS eye_laterality,"
                " @target_axis_deg AS target_axis_deg, @iol_model AS iol_model,"
                " @created_by AS created_by, @created_at AS created_at,"
                " @facility_id AS facility_id, @patient_id AS patient_id)"
                " S ON T.case_ref = S.case_ref"
                " WHEN MATCHED THEN UPDATE SET"
                " eye_laterality = S.eye_laterality,"
                " target_axis_deg = S.target_axis_deg,"
                " iol_model = S.iol_model, created_by = S.created_by,"
                " created_at = S.created_at, facility_id = S.facility_id,"
                " patient_id = S.patient_id"
                " WHEN NOT MATCHED THEN INSERT"
                " (case_ref, eye_laterality, target_axis_deg, iol_model,"
                "  created_by, created_at, facility_id, patient_id)"
                " VALUES (S.case_ref, S.eye_laterality, S.target_axis_deg,"
                "  S.iol_model, S.created_by, S.created_at, S.facility_id,"
                "  S.patient_id)"
            )
            client.query(merge_sql, job_config=job_config)
            return None
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            logger.warning(
                "storage.bigquery_case_upsert_failed",
                extra={
                    "event": "storage.bigquery_case_upsert_failed",
                    "error_type": type(exc).__name__,
                    "case_ref": case.case_ref,
                },
                exc_info=True,
            )
            return exc

    def read_rows(
        self, facility_id: str | None = None
    ) -> tuple[list[ResultRow], Exception | None]:
        """Run a SELECT over the ``results`` table and hydrate ``ResultRow``s.

        Facility scoping is pushed down into the SQL WHERE clause so only the
        authorized rows are transferred. Returns ``(rows, None)`` on success and
        ``([], error)`` on failure — the caller surfaces the error as an
        ``analytics_warning`` on ``/stats`` so a BigQuery outage is never
        silently misread as "no data" (architecture review Major).
        """
        try:
            from google.cloud import bigquery

            client = self._get_client()
            where = ""
            params = []
            if facility_id is not None:
                where = " WHERE facility_id = @facility_id"
                params = [
                    bigquery.ScalarQueryParameter("facility_id", "STRING", facility_id)
                ]
            job_config = bigquery.QueryJobConfig(
                query_parameters=params,
                use_legacy_sql=False,
            )
            query = (
                "SELECT test_id, timestamp, angle_deg, upright_landmark,"
                " rotated_landmark, passed_sanity_check, sanity_flags,"
                " facility_id, user_uid, case_ref"
                f" FROM `{self._table}`{where}"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE; facility_id is a bound query parameter
            )
            result = client.query(query, job_config=job_config)
            rows = [
                ResultRow(
                    test_id=str(r["test_id"]),
                    timestamp=str(r["timestamp"]),
                    angle_deg=float(r["angle_deg"]),
                    upright_landmark=str(r["upright_landmark"]),
                    rotated_landmark=str(r["rotated_landmark"]),
                    passed_sanity_check=bool(r["passed_sanity_check"]),
                    sanity_flags=str(r["sanity_flags"]),
                    facility_id=r.get("facility_id"),
                    user_uid=r.get("user_uid"),
                    case_ref=r.get("case_ref"),
                )
                for r in result
            ]
            return rows, None
        except Exception as exc:  # noqa: BLE001 - analytics read is best-effort
            logger.warning(
                "storage.bigquery_read_failed",
                extra={
                    "event": "storage.bigquery_read_failed",
                    "error_type": type(exc).__name__,
                    "table": self._table,
                },
                exc_info=True,
            )
            return [], exc

    def case_refs_for_patient(
        self, patient_id: str, facility_id: str
    ) -> tuple[list[str], Exception | None]:
        """Step 1 of the cascading erasure (TDD Section 3.2a): find every
        ``case_ref`` whose ``cases.patient_id`` matches, facility-scoped.

        Best-effort read: returns ``([], error)`` on failure so the erasure
        handler can retry rather than proceeding on incomplete data (an
        incomplete erasure is a DPDP compliance failure, not a tolerable gap).
        """
        try:
            from google.cloud import bigquery

            client = self._get_client()
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("patient_id", "STRING", patient_id),
                    bigquery.ScalarQueryParameter("facility_id", "STRING", facility_id),
                ],
                use_legacy_sql=False,
            )
            query = (
                f"SELECT case_ref FROM `{self._cases_table}`"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                " WHERE patient_id = @patient_id AND facility_id = @facility_id"
            )
            result = client.query(query, job_config=job_config)
            return [str(r["case_ref"]) for r in result], None
        except Exception as exc:  # noqa: BLE001 - surfaced so caller can retry
            logger.warning(
                "storage.bigquery_case_refs_failed",
                extra={
                    "event": "storage.bigquery_case_refs_failed",
                    "error_type": type(exc).__name__,
                    "patient_id": patient_id,
                },
                exc_info=True,
            )
            return [], exc

    def delete_cases_for_patient(
        self, patient_id: str, facility_id: str
    ) -> tuple[int, int, Exception | None]:
        """Step 2 of the cascading erasure (TDD Section 3.2a): delete every
        ``results`` then ``cases`` row linked to ``patient_id``.

        Deletes ``results`` first, then ``cases`` (child-first), scoped by
        ``patient_id`` and ``facility_id``. Returns
        ``(cases_deleted, results_deleted, None)`` on success or
        ``(0, 0, error)`` on failure. Unlike the analytics write, this must be
        retried to completion — an incomplete erasure is a DPDP compliance
        failure, not a tolerable analytics gap (TDD Section 3.2a note).
        """
        try:
            from google.cloud import bigquery

            client = self._get_client()
            params = [
                bigquery.ScalarQueryParameter("patient_id", "STRING", patient_id),
                bigquery.ScalarQueryParameter("facility_id", "STRING", facility_id),
            ]
            job_config = bigquery.QueryJobConfig(
                query_parameters=params, use_legacy_sql=False
            )
            results_res = client.query(
                f"DELETE FROM `{self._table}`"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                " WHERE case_ref IN ("
                f"  SELECT case_ref FROM `{self._cases_table}`"
                "  WHERE patient_id = @patient_id"
                "    AND facility_id = @facility_id"
                ")"
            )
            results_deleted = results_res.num_dml_affected_rows
            cases_res = client.query(
                f"DELETE FROM `{self._cases_table}`"  # nosec B608 - table name is operator-controlled config, validated by _VALID_TABLE_RE
                " WHERE patient_id = @patient_id AND facility_id = @facility_id",
                job_config=job_config,
            )
            cases_deleted = cases_res.num_dml_affected_rows
            return int(cases_deleted), int(results_deleted), None
        except Exception as exc:  # noqa: BLE001 - surfaced so caller can retry
            logger.warning(
                "storage.bigquery_erasure_failed",
                extra={
                    "event": "storage.bigquery_erasure_failed",
                    "error_type": type(exc).__name__,
                    "patient_id": patient_id,
                },
                exc_info=True,
            )
            return 0, 0, exc


def writer_from_config() -> ResultWriter:
    """Build a writer from the application config (config.Config).

    - CC_STORAGE_MODE=memory -> InMemoryWriter (local/dev, no GCP needed).
    - CC_STORAGE_MODE=bigquery -> BigQueryWriter (production).
    - CC_STORAGE_MODE=auto -> BigQuery when the SDK is importable, else memory.
    """
    from cyclotorsion.config import cfg

    if cfg.use_bigquery:
        return BigQueryWriter(table=cfg.bigquery_table)
    return InMemoryWriter()


def new_test_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
