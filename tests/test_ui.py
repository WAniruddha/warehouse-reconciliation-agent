"""Smoke and interaction tests for the Streamlit demonstration."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from reconciliation_agent.report import DecisionBranch
from reconciliation_agent.store import AuditStore
from reconciliation_agent.ui import (
    build_partial_state,
    load_demo_scenario,
    persist_original_history,
)

ROOT = Path(__file__).resolve().parents[1]


def test_partial_stream_state_only_contains_processed_information() -> None:
    events, _ = load_demo_scenario()

    empty_engine, empty_traces = build_partial_state(events, 0)
    decision_engine, decision_traces = build_partial_state(events, 4)
    final_engine, final_traces = build_partial_state(events, 12)

    assert empty_engine is None
    assert empty_traces == ()
    assert decision_engine is not None
    assert decision_engine.branch is DecisionBranch.ORIGINAL
    assert [trace.decision_id for trace in decision_traces] == ["decision-100"]
    assert final_engine is not None
    assert len(final_traces) == 3
    assert final_engine.final_available_to_promise() == {
        "BEV-GINGER-330": 2,
        "BEV-SPARKLING-750": 6,
        "BEV-TONIC-200": 3,
    }


def test_original_reasoning_is_persisted_before_snapshot_arrives(tmp_path: Path) -> None:
    events, _ = load_demo_scenario()
    _, traces = build_partial_state(events, 12)
    database_path = tmp_path / "pre_snapshot_audit.db"

    persist_original_history(events.events, traces, database_path)

    with AuditStore(database_path) as store:
        assert store.table_count("events") == 12
        assert store.table_count("decision_traces") == 3
        assert store.table_count("warehouse_snapshots") == 0
        assert store.table_count("reconciliation_reports") == 0


def test_streamlit_initial_state_hides_snapshot_and_disables_reconciliation() -> None:
    app = AppTest.from_file(ROOT / "app.py").run(timeout=20)

    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Events processed"] == "0 / 12"
    assert metrics["Decision traces stored"] == "0"
    assert metrics["Warehouse snapshot"] == "Waiting"
    assert app.button(key="process_next").disabled is False
    assert app.button(key="receive_snapshot").disabled is True
    assert "contents remain hidden" in app.caption[-1].value


def test_streamlit_full_lifecycle_exposes_all_three_audit_outcomes() -> None:
    app = AppTest.from_file(ROOT / "app.py").run(timeout=20)
    app.button(key="process_all").click().run(timeout=20)

    assert not app.exception
    metrics = {item.label: item.value for item in app.metric[:3]}
    assert metrics == {
        "Events processed": "12 / 12",
        "Decision traces stored": "3",
        "Warehouse snapshot": "Waiting",
    }
    assert app.button(key="receive_snapshot").disabled is False

    app.button(key="receive_snapshot").click().run(timeout=20)

    assert not app.exception
    status_metrics = {item.label: item.value for item in app.metric[:3]}
    assert status_metrics["Warehouse snapshot"] == "Received"
    assert any("CONFIRMED_SOUND" in item.value for item in app.success)
    assert any("SOUND_WITH_DATA_GAP" in item.value for item in app.info)
    assert any("WOULD_CHANGE_REVIEW_REQUIRED" in item.value for item in app.error)
    assert any("evt-002" in item.value for item in app.warning)
    assert any("evt-012" in item.value for item in app.warning)
    rendered_markdown = [item.value for item in app.markdown]
    assert any("`BACKORDER`" in item for item in rendered_markdown)
    assert any(
        "Promotion status" in item and "PENDING_HUMAN_REVIEW" in item
        for item in rendered_markdown
    )
    assert any(
        item.value.endswith("`.runtime/streamlit_audit.db`")
        for item in app.caption
    )
    assert [item.label for item in app.download_button] == [
        "Download audit report (Markdown)",
        "Download audit report (JSON)",
    ]
