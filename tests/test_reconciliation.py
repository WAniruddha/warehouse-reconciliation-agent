"""End-to-end tests for state processing, replay, reasoning, and persistence."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from reconciliation_agent.cli import run_reconciliation
from reconciliation_agent.models import (
    DecisionAction,
    EventBatch,
    ExpectedOutcomes,
    PromotionStatus,
    ReconciliationVerdict,
    WarehouseSnapshot,
)
from reconciliation_agent.policy import evaluate_fulfilment
from reconciliation_agent.reconciliation import reconcile
from reconciliation_agent.report import DecisionBranch, ReconciliationReport
from reconciliation_agent.store import AuditIntegrityError, AuditStore

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"


def load_json(name: str) -> object:
    with (DEMO / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_demo() -> tuple[EventBatch, WarehouseSnapshot, ExpectedOutcomes]:
    return (
        EventBatch.model_validate(load_json("events.json")),
        WarehouseSnapshot.model_validate(load_json("warehouse_snapshot.json")),
        ExpectedOutcomes.model_validate(load_json("expected_outcomes.json")),
    )


def test_engine_generates_the_golden_outcomes_without_reading_the_answer_key() -> None:
    events, snapshot, expected = load_demo()

    report = reconcile(events, snapshot)
    findings = {finding.decision_id: finding for finding in report.findings}

    assert report.original_final_available_to_promise == (
        expected.original_final_available_to_promise
    )
    assert report.candidate_final_available_to_promise == (
        expected.candidate_final_available_to_promise
    )
    assert report.promotion_status is expected.promotion_status

    for expected_decision in expected.decisions:
        finding = findings[expected_decision.decision_id]
        original = finding.original_trace
        corrected = finding.corrected_trace

        assert corrected is not None
        assert finding.order_id == expected_decision.order_id
        assert finding.sku == expected_decision.sku
        assert original.order_quantity == expected_decision.order_quantity
        assert original.unit_of_measure is expected_decision.unit_of_measure
        assert original.action is expected_decision.original_action
        assert corrected.action is expected_decision.corrected_action
        assert (
            original.base_available_to_promise
            == expected_decision.original_base_available_to_promise
        )
        assert original.expected_inbound == expected_decision.original_expected_inbound
        assert (
            original.decision_available_to_promise
            == expected_decision.original_decision_available_to_promise
        )
        assert (
            corrected.decision_available_to_promise
            == expected_decision.corrected_available_to_promise
        )
        assert original.policy_consistent is expected_decision.policy_consistent_at_time
        assert original.assumption_event_ids == expected_decision.assumption_event_ids
        assert (
            finding.invalidated_assumption_event_ids
            == expected_decision.invalidated_assumption_event_ids
        )
        assert finding.verdict is expected_decision.verdict
        assert (
            finding.invalidated_output_event_ids
            == expected_decision.invalidated_output_event_ids
        )


def test_each_decision_has_a_self_contained_written_reasoning_trace() -> None:
    events, snapshot, _ = load_demo()
    report = reconcile(events, snapshot)

    assert len(report.findings) == 3
    for finding in report.findings:
        trace = finding.original_trace
        assert trace.order_id in trace.explanation
        assert trace.policy_version in trace.explanation
        assert trace.action.value in trace.explanation
        assert str(trace.order_quantity) in trace.explanation
        assert trace.evidence_ids
        assert finding.reasoning_gap
        assert finding.audit_explanation

    changed = next(item for item in report.findings if item.decision_id == "decision-300")
    assert "delivery-300" in changed.original_trace.explanation
    assert "not warehouse-confirmed" in changed.original_trace.explanation
    assert changed.invalidated_assumption_event_ids == ("evt-002",)
    assert changed.invalidated_output_event_ids == ("evt-012",)
    assert changed.requires_human_review is True


def test_snapshot_boundary_exposes_inventory_divergence() -> None:
    events, snapshot, _ = load_demo()
    report = reconcile(events, snapshot)
    divergences = {item.sku: item for item in report.inventory_divergences}

    assert report.divergence_detected is True
    assert divergences["BEV-SPARKLING-750"].diverged is False
    assert divergences["BEV-TONIC-200"].warehouse_minus_simulation == -2
    assert divergences["BEV-GINGER-330"].simulation_expected_inbound == 4
    assert divergences["BEV-GINGER-330"].warehouse_minus_simulation == -4


def test_sqlite_ledger_persists_both_branches_and_is_idempotent(
    tmp_path: Path,
) -> None:
    events, snapshot, _ = load_demo()
    database = tmp_path / "audit.db"

    with AuditStore(database) as store:
        first_report = reconcile(events, snapshot, store)
        second_report = reconcile(events, snapshot, store)

        assert first_report == second_report
        assert store.table_count("events") == 12
        assert store.table_count("warehouse_snapshots") == 1
        assert store.table_count("decision_traces") == 6
        assert store.table_count("reconciliation_reports") == 1
        assert store.table_count("reconciliation_findings") == 3

        original = store.load_decision_trace(
            events.simulation_id, DecisionBranch.ORIGINAL, "decision-300"
        )
        corrected = store.load_decision_trace(
            events.simulation_id, DecisionBranch.CORRECTED, "decision-300"
        )
        assert original is not None and original.action is DecisionAction.DISPATCH
        assert corrected is not None and corrected.action is DecisionAction.BACKORDER
        assert original.explanation
        assert corrected.explanation

        conflicting_event = events.events[0].model_copy(
            update={"received_at": events.events[0].received_at + timedelta(seconds=1)}
        )
        with pytest.raises(AuditIntegrityError, match="cannot be rewritten"):
            store.save_event(conflicting_event)


def test_cli_writes_database_and_standalone_reports(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    markdown = tmp_path / "report.md"
    json_report = tmp_path / "report.json"

    report = run_reconciliation(
        events_path=DEMO / "events.json",
        snapshot_path=DEMO / "warehouse_snapshot.json",
        database_path=database,
        markdown_report_path=markdown,
        json_report_path=json_report,
    )

    assert database.is_file()
    assert markdown.is_file()
    assert json_report.is_file()
    assert "decision-300" in markdown.read_text(encoding="utf-8")
    assert "WOULD_CHANGE_REVIEW_REQUIRED" in markdown.read_text(encoding="utf-8")
    loaded_report = ReconciliationReport.model_validate_json(
        json_report.read_text(encoding="utf-8")
    )
    assert loaded_report == report


def test_fully_received_delivery_confirms_the_original_assumption() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    snapshot_data = cast(dict[str, Any], load_json("warehouse_snapshot.json"))
    ginger_inventory = next(
        item
        for item in snapshot_data["inventory"]
        if item["sku"] == "BEV-GINGER-330"
    )
    ginger_inventory["on_hand"] = 5
    delivery = snapshot_data["inbound_deliveries"][0]
    delivery["received_quantity"] = 4
    delivery["status"] = "RECEIVED"
    snapshot = WarehouseSnapshot.model_validate(snapshot_data)

    report = reconcile(events, snapshot)
    changed = next(item for item in report.findings if item.decision_id == "decision-300")

    assert changed.verdict is ReconciliationVerdict.CONFIRMED_SOUND
    assert changed.corrected_trace is not None
    assert changed.corrected_trace.action is DecisionAction.DISPATCH
    assert changed.invalidated_assumption_event_ids == ()
    assert changed.invalidated_output_event_ids == ()
    assert changed.requires_human_review is False
    assert report.promotion_status is PromotionStatus.AUTO_PROMOTE


def test_policy_dispatches_at_the_boundary_and_backorders_below_it() -> None:
    assert evaluate_fulfilment(3, 3).action is DecisionAction.DISPATCH
    assert evaluate_fulfilment(2, 3).action is DecisionAction.BACKORDER
