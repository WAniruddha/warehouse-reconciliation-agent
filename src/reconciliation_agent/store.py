"""SQLite-backed immutable audit ledger for events, reasoning, and reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType

from .models import SimulationEvent, WarehouseSnapshot
from .report import DecisionBranch, DecisionTrace, ReconciliationReport


class AuditIntegrityError(RuntimeError):
    """Raised when an existing audit identity is reused with different contents."""


class AuditStore:
    """Persist the evidence needed to audit a reconciliation after the run."""

    _COUNTABLE_TABLES = {
        "events",
        "warehouse_snapshots",
        "decision_traces",
        "reconciliation_reports",
        "reconciliation_findings",
    }

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def __enter__(self) -> AuditStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def save_event(self, event: SimulationEvent) -> None:
        raw_json = event.model_dump_json()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, simulation_id, sequence_number, event_type,
                    occurred_at, received_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.simulation_id,
                    event.sequence_number,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                    event.received_at.isoformat(),
                    raw_json,
                ),
            )
            row = self.connection.execute(
                "SELECT raw_json FROM events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if row is None or row["raw_json"] != raw_json:
                raise AuditIntegrityError(
                    f"Event identity {event.event_id} cannot be rewritten"
                )

    def save_snapshot(self, snapshot: WarehouseSnapshot) -> None:
        raw_json = snapshot.model_dump_json()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO warehouse_snapshots (
                    snapshot_id, simulation_id, warehouse_id, as_of,
                    received_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.simulation_id,
                    snapshot.warehouse_id,
                    snapshot.as_of.isoformat(),
                    snapshot.received_at.isoformat(),
                    raw_json,
                ),
            )
            row = self.connection.execute(
                "SELECT raw_json FROM warehouse_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if row is None or row["raw_json"] != raw_json:
                raise AuditIntegrityError(
                    f"Snapshot identity {snapshot.snapshot_id} cannot be rewritten"
                )

    def save_decision_trace(self, trace: DecisionTrace) -> None:
        trace_json = trace.model_dump_json()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO decision_traces (
                    simulation_id, branch, decision_id, order_id, decision_event_id,
                    occurred_at, action, policy_action, policy_consistent,
                    explanation, trace_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.simulation_id,
                    trace.branch.value,
                    trace.decision_id,
                    trace.order_id,
                    trace.decision_event_id,
                    trace.occurred_at.isoformat(),
                    trace.action.value,
                    trace.policy_action.value,
                    int(trace.policy_consistent),
                    trace.explanation,
                    trace_json,
                ),
            )
            row = self.connection.execute(
                """
                SELECT trace_json
                FROM decision_traces
                WHERE simulation_id = ? AND branch = ? AND decision_id = ?
                """,
                (trace.simulation_id, trace.branch.value, trace.decision_id),
            ).fetchone()
            if row is None or row["trace_json"] != trace_json:
                raise AuditIntegrityError(
                    f"Decision trace {trace.branch.value}/{trace.decision_id} "
                    "cannot be rewritten"
                )

    def save_report(self, report: ReconciliationReport) -> None:
        report_json = report.model_dump_json()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO reconciliation_reports (
                    report_id, simulation_id, snapshot_id, generated_at,
                    promotion_status, report_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.simulation_id,
                    report.snapshot_id,
                    report.generated_at.isoformat(),
                    report.promotion_status.value,
                    report_json,
                ),
            )
            row = self.connection.execute(
                "SELECT report_json FROM reconciliation_reports WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if row is None or row["report_json"] != report_json:
                raise AuditIntegrityError(
                    f"Reconciliation report {report.report_id} cannot be rewritten"
                )

            for finding in report.findings:
                finding_json = finding.model_dump_json()
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO reconciliation_findings (
                        report_id, decision_id, verdict, requires_human_review,
                        audit_explanation, finding_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report.report_id,
                        finding.decision_id,
                        finding.verdict.value,
                        int(finding.requires_human_review),
                        finding.audit_explanation,
                        finding_json,
                    ),
                )
                finding_row = self.connection.execute(
                    """
                    SELECT finding_json
                    FROM reconciliation_findings
                    WHERE report_id = ? AND decision_id = ?
                    """,
                    (report.report_id, finding.decision_id),
                ).fetchone()
                if finding_row is None or finding_row["finding_json"] != finding_json:
                    raise AuditIntegrityError(
                        f"Finding {report.report_id}/{finding.decision_id} "
                        "cannot be rewritten"
                    )

    def table_count(self, table_name: str) -> int:
        if table_name not in self._COUNTABLE_TABLES:
            raise ValueError(f"Unsupported audit table {table_name}")
        row = self.connection.execute(
            f"SELECT COUNT(*) AS row_count FROM {table_name}"  # noqa: S608
        ).fetchone()
        assert row is not None
        return int(row["row_count"])

    def load_decision_trace(
        self,
        simulation_id: str,
        branch: DecisionBranch,
        decision_id: str,
    ) -> DecisionTrace | None:
        row = self.connection.execute(
            """
            SELECT trace_json
            FROM decision_traces
            WHERE simulation_id = ? AND branch = ? AND decision_id = ?
            """,
            (simulation_id, branch.value, decision_id),
        ).fetchone()
        if row is None:
            return None
        return DecisionTrace.model_validate_json(row["trace_json"])

    def _create_schema(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    simulation_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    UNIQUE(simulation_id, sequence_number)
                );

                CREATE TABLE IF NOT EXISTS warehouse_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    simulation_id TEXT NOT NULL,
                    warehouse_id TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decision_traces (
                    simulation_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    decision_event_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    policy_action TEXT NOT NULL,
                    policy_consistent INTEGER NOT NULL CHECK(policy_consistent IN (0, 1)),
                    explanation TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    PRIMARY KEY(simulation_id, branch, decision_id)
                );

                CREATE TABLE IF NOT EXISTS reconciliation_reports (
                    report_id TEXT PRIMARY KEY,
                    simulation_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    promotion_status TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES warehouse_snapshots(snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS reconciliation_findings (
                    report_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    requires_human_review INTEGER NOT NULL
                        CHECK(requires_human_review IN (0, 1)),
                    audit_explanation TEXT NOT NULL,
                    finding_json TEXT NOT NULL,
                    PRIMARY KEY(report_id, decision_id),
                    FOREIGN KEY(report_id) REFERENCES reconciliation_reports(report_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_events_simulation_time
                    ON events(simulation_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_decisions_simulation
                    ON decision_traces(simulation_id, branch);
                PRAGMA user_version = 1;
                """
            )
