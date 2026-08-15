"""Command-line entry point for the end-to-end reconciliation demo."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .models import EventBatch, WarehouseSnapshot
from .reconciliation import reconcile
from .render import render_markdown_report, render_text_report
from .report import ReconciliationReport
from .store import AuditStore


def run_reconciliation(
    *,
    events_path: Path,
    snapshot_path: Path,
    database_path: Path,
    markdown_report_path: Path,
    json_report_path: Path,
) -> ReconciliationReport:
    """Load inputs, run reconciliation, and persist all audit artefacts."""

    event_batch = EventBatch.model_validate(_load_json(events_path))
    snapshot = WarehouseSnapshot.model_validate(_load_json(snapshot_path))
    with AuditStore(database_path) as store:
        report = reconcile(event_batch, snapshot, store)

    markdown_report_path.parent.mkdir(parents=True, exist_ok=True)
    json_report_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_report_path.write_text(
        render_markdown_report(report), encoding="utf-8"
    )
    json_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile simulation decisions against a delayed warehouse snapshot."
        )
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=Path("data/demo/events.json"),
        help="Path to the simulation event batch JSON file.",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=Path("data/demo/warehouse_snapshot.json"),
        help="Path to the delayed warehouse snapshot JSON file.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(".runtime/reconciliation_audit.db"),
        help="Path for the SQLite audit ledger.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".runtime/reconciliation_report.md"),
        help="Path for the human-readable Markdown report.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=Path(".runtime/reconciliation_report.json"),
        help="Path for the machine-readable JSON report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_reconciliation(
        events_path=args.events,
        snapshot_path=args.snapshot,
        database_path=args.database,
        markdown_report_path=args.report,
        json_report_path=args.json_report,
    )
    print(render_text_report(report))
    print()
    print(f"SQLite audit ledger: {args.database}")
    print(f"Markdown audit report: {args.report}")
    print(f"JSON audit report: {args.json_report}")
    return 0


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    raise SystemExit(main())
