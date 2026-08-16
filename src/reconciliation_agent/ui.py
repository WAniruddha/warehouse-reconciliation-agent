"""Streamlit interface for stepping through the delayed-snapshot lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from .engine import SimulationEngine
from .models import (
    EventBatch,
    ReconciliationVerdict,
    SimulationEvent,
    WarehouseSnapshot,
)
from .reconciliation import reconcile
from .render import render_markdown_report
from .report import DecisionBranch, DecisionTrace, ReconciliationFinding
from .store import AuditStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIRECTORY = REPOSITORY_ROOT / "data" / "demo"
UI_DATABASE = REPOSITORY_ROOT / ".runtime" / "streamlit_audit.db"


def main() -> None:
    st.set_page_config(
        page_title="Warehouse Reconciliation Agent",
        page_icon="🔎",
        layout="wide",
    )
    _initialize_session()
    event_batch, snapshot = load_demo_scenario()

    st.title("Warehouse Reconciliation Agent")
    st.caption(
        "Deterministic decision audit · delayed warehouse truth · no LLM or API key"
    )
    st.write(
        "Process the simulation stream first. The warehouse snapshot remains hidden until "
        "the live decisions and their reasoning have been recorded."
    )

    processed_count = int(st.session_state.processed_count)
    snapshot_received = bool(st.session_state.snapshot_received)
    processed_events = event_batch.events[:processed_count]
    engine, traces = build_partial_state(event_batch, processed_count)
    persist_original_history(processed_events, traces)

    _display_status(processed_count, len(event_batch.events), traces, snapshot_received)
    _display_controls(event_batch)

    processed_count = int(st.session_state.processed_count)
    snapshot_received = bool(st.session_state.snapshot_received)
    processed_events = event_batch.events[:processed_count]
    engine, traces = build_partial_state(event_batch, processed_count)

    st.divider()
    _display_stream(processed_events, event_batch, engine, traces)

    st.divider()
    if not snapshot_received:
        _display_waiting_snapshot(snapshot, processed_count, len(event_batch.events))
        return

    _display_reconciliation(event_batch, snapshot)


def load_demo_scenario() -> tuple[EventBatch, WarehouseSnapshot]:
    with (DEMO_DIRECTORY / "events.json").open(encoding="utf-8") as handle:
        event_batch = EventBatch.model_validate(json.load(handle))
    with (DEMO_DIRECTORY / "warehouse_snapshot.json").open(
        encoding="utf-8"
    ) as handle:
        snapshot = WarehouseSnapshot.model_validate(json.load(handle))
    return event_batch, snapshot


def build_partial_state(
    event_batch: EventBatch, processed_count: int
) -> tuple[SimulationEngine | None, tuple[DecisionTrace, ...]]:
    if processed_count == 0:
        return None, ()
    engine = SimulationEngine(event_batch.simulation_id, DecisionBranch.ORIGINAL)
    traces: list[DecisionTrace] = []
    for event in event_batch.events[:processed_count]:
        trace = engine.process(event)
        if trace is not None:
            traces.append(trace)
    return engine, tuple(traces)


def persist_original_history(
    processed_events: tuple[SimulationEvent, ...],
    traces: tuple[DecisionTrace, ...],
    database_path: str | Path = UI_DATABASE,
) -> None:
    """Persist only information already seen by the live simulation branch."""

    if not processed_events:
        return
    with AuditStore(database_path) as store:
        for event in processed_events:
            store.save_event(event)
        for trace in traces:
            store.save_decision_trace(trace)


def _initialize_session() -> None:
    if "processed_count" not in st.session_state:
        st.session_state.processed_count = 0
    if "snapshot_received" not in st.session_state:
        st.session_state.snapshot_received = False


def _display_status(
    processed_count: int,
    total_events: int,
    traces: tuple[DecisionTrace, ...],
    snapshot_received: bool,
) -> None:
    first, second, third = st.columns(3)
    first.metric("Events processed", f"{processed_count} / {total_events}")
    second.metric("Decision traces stored", len(traces))
    third.metric("Warehouse snapshot", "Received" if snapshot_received else "Waiting")
    st.progress(
        processed_count / total_events,
        text=f"Simulation stream progress: {processed_count} of {total_events} events",
    )


def _display_controls(event_batch: EventBatch) -> None:
    total_events = len(event_batch.events)
    processed_count = int(st.session_state.processed_count)
    snapshot_received = bool(st.session_state.snapshot_received)
    first, second, third, fourth = st.columns(4)

    if first.button(
        "Process next event",
        key="process_next",
        type="primary",
        width="stretch",
        disabled=processed_count >= total_events or snapshot_received,
    ):
        st.session_state.processed_count += 1
        st.rerun()

    if second.button(
        "Process remaining events",
        key="process_all",
        width="stretch",
        disabled=processed_count >= total_events or snapshot_received,
    ):
        st.session_state.processed_count = total_events
        st.rerun()

    if third.button(
        "Receive warehouse snapshot",
        key="receive_snapshot",
        width="stretch",
        disabled=processed_count < total_events or snapshot_received,
    ):
        st.session_state.snapshot_received = True
        st.rerun()

    if fourth.button(
        "Reset demonstration",
        key="reset_demo",
        width="stretch",
        disabled=processed_count == 0 and not snapshot_received,
    ):
        st.session_state.processed_count = 0
        st.session_state.snapshot_received = False
        st.rerun()


def _display_stream(
    processed_events: tuple[SimulationEvent, ...],
    event_batch: EventBatch,
    engine: SimulationEngine | None,
    traces: tuple[DecisionTrace, ...],
) -> None:
    state_tab, stream_tab = st.tabs(["Current simulation state", "Event stream"])
    with state_tab:
        if engine is None or engine.last_occurred_at is None:
            st.info("Process the initialization event to create warehouse state.")
        else:
            st.dataframe(
                _state_rows(engine),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Projected ATP = on-hand − reserved − quarantined + eligible expected "
                "inbound."
            )

        if processed_events:
            latest = processed_events[-1]
            st.markdown(
                f"**Latest event:** `{latest.event_id}` · `{latest.event_type.value}`"
            )
            with st.expander("Inspect latest event JSON"):
                st.json(latest.model_dump(mode="json"))

    with stream_tab:
        st.dataframe(
            [
                {
                    "Status": (
                        "Processed"
                        if index <= len(processed_events)
                        else "Waiting"
                    ),
                    "Sequence": event.sequence_number,
                    "Event ID": event.event_id,
                    "Time": event.occurred_at.strftime("%H:%M:%S"),
                    "Type": event.event_type.value,
                    "Entity": event.entity_id,
                }
                for index, event in enumerate(event_batch.events, start=1)
            ],
            hide_index=True,
            width="stretch",
        )

    st.subheader("Reasoning captured before warehouse truth")
    if not traces:
        st.info("No decision event has been processed yet.")
    for trace in traces:
        with st.expander(
            f"{trace.decision_id} · {trace.action.value} · "
            f"{trace.decision_available_to_promise} available",
            expanded=trace is traces[-1],
        ):
            first, second, third = st.columns(3)
            first.metric("Base ATP", trace.base_available_to_promise)
            second.metric("Expected inbound", trace.expected_inbound)
            third.metric("Decision ATP", trace.decision_available_to_promise)
            st.write(trace.explanation)
            st.caption(
                f"Evidence: {', '.join(trace.evidence_ids)} · Assumptions: "
                f"{', '.join(trace.assumption_event_ids) or 'none'}"
            )


def _state_rows(engine: SimulationEngine) -> list[dict[str, object]]:
    assert engine.last_occurred_at is not None
    rows: list[dict[str, object]] = []
    for sku in sorted(engine.inventory):
        inventory = engine.inventory[sku]
        base, expected, projected = engine.availability_at(
            sku, engine.last_occurred_at
        )
        rows.append(
            {
                "SKU": sku,
                "On hand": inventory.on_hand,
                "Reserved": inventory.reserved,
                "Quarantined": inventory.quarantined,
                "Base ATP": base,
                "Eligible inbound": expected,
                "Projected ATP": projected,
                "Unit": inventory.unit_of_measure.value,
            }
        )
    return rows


def _display_waiting_snapshot(
    snapshot: WarehouseSnapshot, processed_count: int, total_events: int
) -> None:
    st.subheader("Delayed warehouse snapshot")
    if processed_count < total_events:
        st.info(
            "The snapshot has not arrived. Continue processing simulation events first."
        )
    else:
        st.warning(
            "All simulation events and their decision-time reasoning are stored. The "
            "warehouse snapshot can now arrive."
        )
    st.caption(
        f"Snapshot `{snapshot.snapshot_id}` will arrive at "
        f"{snapshot.received_at.isoformat()}; its inventory contents remain hidden."
    )


def _display_reconciliation(
    event_batch: EventBatch, snapshot: WarehouseSnapshot
) -> None:
    UI_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with AuditStore(UI_DATABASE) as store:
        report = reconcile(event_batch, snapshot, store)

    st.subheader("Warehouse snapshot received")
    st.success(
        f"Snapshot {snapshot.snapshot_id} arrived at "
        f"{snapshot.received_at.isoformat()} and is authoritative as of "
        f"{snapshot.as_of.isoformat()}."
    )

    inventory_tab, divergence_tab = st.tabs(
        ["Warehouse truth", "Inventory divergence"]
    )
    with inventory_tab:
        st.dataframe(
            [
                {
                    "SKU": item.sku,
                    "On hand": item.on_hand,
                    "Reserved": item.reserved,
                    "Quarantined": item.quarantined,
                    "Available": item.available_to_promise,
                    "Unit": item.unit_of_measure.value,
                }
                for item in snapshot.inventory
            ],
            hide_index=True,
            width="stretch",
        )
        st.dataframe(
            [
                {
                    "Delivery": item.delivery_id,
                    "SKU": item.sku,
                    "Expected": item.expected_quantity,
                    "Received": item.received_quantity,
                    "Status": item.status.value,
                }
                for item in snapshot.inbound_deliveries
            ],
            hide_index=True,
            width="stretch",
        )
    with divergence_tab:
        st.dataframe(
            [
                {
                    "SKU": item.sku,
                    "Simulation base": item.simulation_base_available_to_promise,
                    "Expected inbound": item.simulation_expected_inbound,
                    "Simulation projected": (
                        item.simulation_projected_available_to_promise
                    ),
                    "Warehouse": item.warehouse_available_to_promise,
                    "Warehouse − simulation": item.warehouse_minus_simulation,
                    "Status": "DIVERGED" if item.diverged else "CONFIRMED",
                }
                for item in report.inventory_divergences
            ],
            hide_index=True,
            width="stretch",
        )

    st.subheader("Decision reconciliation findings")
    for finding in report.findings:
        _display_finding(finding)

    st.subheader("Candidate branch")
    first, second = st.columns(2)
    first.metric("Decisions audited", report.decisions_audited)
    second.metric(
        "Human reviews required",
        sum(item.requires_human_review for item in report.findings),
    )
    st.markdown(f"**Promotion status:** `{report.promotion_status.value}`")
    if any(item.requires_human_review for item in report.findings):
        st.warning(
            "Automatic promotion is blocked until the flagged decision is reviewed."
        )
    else:
        st.success("All decisions are safe for automatic promotion.")

    markdown_report = render_markdown_report(report)
    first_download, second_download = st.columns(2)
    first_download.download_button(
        "Download audit report (Markdown)",
        data=markdown_report,
        file_name="reconciliation_report.md",
        mime="text/markdown",
        width="stretch",
    )
    second_download.download_button(
        "Download audit report (JSON)",
        data=report.model_dump_json(indent=2),
        file_name="reconciliation_report.json",
        mime="application/json",
        width="stretch",
    )
    database_display_path = UI_DATABASE.relative_to(REPOSITORY_ROOT).as_posix()
    st.caption(f"Persistent SQLite audit ledger: `{database_display_path}`")


def _display_finding(finding: ReconciliationFinding) -> None:
    with st.container(border=True):
        st.markdown(
            f"### {finding.decision_id} · {finding.original_trace.sku}"
        )
        if finding.verdict is ReconciliationVerdict.CONFIRMED_SOUND:
            st.success(f"{finding.verdict.value} · accepted")
        elif finding.verdict is ReconciliationVerdict.SOUND_WITH_DATA_GAP:
            st.info(f"{finding.verdict.value} · accepted with documented gap")
        else:
            st.error(f"{finding.verdict.value} · human review required")

        corrected = finding.corrected_trace
        corrected_action = corrected.action.value if corrected else "UNKNOWN"
        corrected_atp = (
            corrected.decision_available_to_promise if corrected else "UNKNOWN"
        )
        unit = finding.original_trace.unit_of_measure.value
        st.markdown(
            "| Decision branch | Action | Available-to-promise |\n"
            "|---|---|---:|\n"
            f"| Original | `{finding.original_trace.action.value}` | "
            f"{finding.original_trace.decision_available_to_promise} {unit} |\n"
            f"| Warehouse-corrected | `{corrected_action}` | {corrected_atp} {unit} |"
        )

        with st.expander("Original decision-time reasoning"):
            st.write(finding.original_trace.explanation)
            st.caption(
                "Evidence IDs: "
                + ", ".join(finding.original_trace.evidence_ids)
            )
        st.markdown("**Reconciliation conclusion**")
        st.write(finding.audit_explanation)
        st.markdown("**Reasoning gap**")
        st.write(finding.reasoning_gap)

        if finding.invalidated_assumption_event_ids:
            st.warning(
                "Invalidated assumptions: "
                + ", ".join(finding.invalidated_assumption_event_ids)
            )
        if finding.invalidated_output_event_ids:
            st.warning(
                "Invalidated historical outputs: "
                + ", ".join(finding.invalidated_output_event_ids)
            )
