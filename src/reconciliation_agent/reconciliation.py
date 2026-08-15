"""Reconcile immutable simulation decisions against a delayed warehouse snapshot."""

from __future__ import annotations

from collections import Counter

from .engine import SimulationEngine
from .models import (
    EventBatch,
    InboundDeliveryExpectedEvent,
    InboundDeliveryStatus,
    PromotionStatus,
    ReconciliationVerdict,
    ScenarioBundle,
    WarehouseSnapshot,
)
from .report import (
    DecisionBranch,
    DecisionTrace,
    InventoryDivergence,
    ReconciliationFinding,
    ReconciliationReport,
)
from .store import AuditStore


def reconcile(
    event_batch: EventBatch,
    snapshot: WarehouseSnapshot,
    store: AuditStore | None = None,
) -> ReconciliationReport:
    """Process the live branch, replay a corrected branch, and audit every decision."""

    ScenarioBundle(event_batch=event_batch, warehouse_snapshot=snapshot)
    events_before_snapshot = tuple(
        event
        for event in event_batch.events
        if event.received_at < snapshot.received_at
    )

    if store is not None:
        for event in events_before_snapshot:
            store.save_event(event)
        store.save_snapshot(snapshot)

    original = SimulationEngine(event_batch.simulation_id, DecisionBranch.ORIGINAL)
    for event in events_before_snapshot:
        trace = original.process(event)
        if trace is not None and store is not None:
            store.save_decision_trace(trace)

    simulation_at_snapshot = SimulationEngine(
        event_batch.simulation_id, DecisionBranch.ORIGINAL
    )
    for event in events_before_snapshot:
        if event.occurred_at > snapshot.as_of:
            break
        simulation_at_snapshot.process(event)

    corrected = SimulationEngine.from_warehouse_snapshot(snapshot)
    for event in events_before_snapshot:
        if event.occurred_at <= snapshot.as_of:
            continue
        trace = corrected.process(event)
        if trace is not None and store is not None:
            store.save_decision_trace(trace)

    inventory_divergences = _compare_inventory_at_snapshot(
        simulation_at_snapshot, snapshot
    )
    event_by_id = {event.event_id: event for event in events_before_snapshot}
    findings = tuple(
        _audit_decision(
            original_trace=trace,
            corrected_trace=corrected.decision_traces.get(trace.decision_id),
            corrected_engine=corrected,
            event_by_id=event_by_id,
            snapshot=snapshot,
        )
        for trace in original.decision_traces.values()
    )

    verdict_counts = Counter(finding.verdict.value for finding in findings)
    requires_review = any(finding.requires_human_review for finding in findings)
    promotion_status = (
        PromotionStatus.PENDING_HUMAN_REVIEW
        if requires_review
        else PromotionStatus.AUTO_PROMOTE
    )
    report = ReconciliationReport(
        report_id=f"report-{event_batch.simulation_id}-{snapshot.snapshot_id}",
        simulation_id=event_batch.simulation_id,
        warehouse_id=snapshot.warehouse_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_as_of=snapshot.as_of,
        snapshot_received_at=snapshot.received_at,
        generated_at=snapshot.received_at,
        events_processed_before_snapshot=len(events_before_snapshot),
        decisions_audited=len(findings),
        divergence_detected=any(item.diverged for item in inventory_divergences),
        inventory_divergences=inventory_divergences,
        findings=findings,
        original_final_available_to_promise=original.final_available_to_promise(),
        candidate_final_available_to_promise=corrected.final_available_to_promise(),
        verdict_counts=dict(sorted(verdict_counts.items())),
        promotion_status=promotion_status,
        summary=_report_summary(findings, promotion_status),
    )
    if store is not None:
        store.save_report(report)
    return report


def _compare_inventory_at_snapshot(
    simulation: SimulationEngine,
    snapshot: WarehouseSnapshot,
) -> tuple[InventoryDivergence, ...]:
    warehouse_inventory = {item.sku: item for item in snapshot.inventory}
    all_skus = sorted(set(simulation.inventory) | set(warehouse_inventory))
    comparisons: list[InventoryDivergence] = []

    for sku in all_skus:
        simulated = simulation.inventory.get(sku)
        warehouse = warehouse_inventory.get(sku)
        if simulated is None:
            comparisons.append(
                InventoryDivergence(
                    sku=sku,
                    unit_of_measure=warehouse.unit_of_measure if warehouse else None,
                    simulation_base_available_to_promise=None,
                    simulation_expected_inbound=None,
                    simulation_projected_available_to_promise=None,
                    warehouse_available_to_promise=(
                        warehouse.available_to_promise if warehouse else None
                    ),
                    warehouse_minus_simulation=None,
                    diverged=True,
                    explanation=(
                        f"Warehouse snapshot contains {sku}, but the simulation had no "
                        "matching inventory state."
                    ),
                )
            )
            continue
        if warehouse is None:
            base, expected, projected = simulation.availability_at(sku, snapshot.as_of)
            comparisons.append(
                InventoryDivergence(
                    sku=sku,
                    unit_of_measure=simulated.unit_of_measure,
                    simulation_base_available_to_promise=base,
                    simulation_expected_inbound=expected,
                    simulation_projected_available_to_promise=projected,
                    warehouse_available_to_promise=None,
                    warehouse_minus_simulation=None,
                    diverged=True,
                    explanation=(
                        f"Simulation contains {sku}, but the warehouse snapshot omitted it; "
                        "the value is unknown rather than zero."
                    ),
                )
            )
            continue

        base, expected, projected = simulation.availability_at(sku, snapshot.as_of)
        if simulated.unit_of_measure is not warehouse.unit_of_measure:
            comparisons.append(
                InventoryDivergence(
                    sku=sku,
                    unit_of_measure=None,
                    simulation_base_available_to_promise=base,
                    simulation_expected_inbound=expected,
                    simulation_projected_available_to_promise=projected,
                    warehouse_available_to_promise=warehouse.available_to_promise,
                    warehouse_minus_simulation=None,
                    diverged=True,
                    explanation=(
                        f"Units do not match for {sku}: simulation uses "
                        f"{simulated.unit_of_measure.value}, warehouse uses "
                        f"{warehouse.unit_of_measure.value}."
                    ),
                )
            )
            continue

        delta = warehouse.available_to_promise - projected
        comparisons.append(
            InventoryDivergence(
                sku=sku,
                unit_of_measure=simulated.unit_of_measure,
                simulation_base_available_to_promise=base,
                simulation_expected_inbound=expected,
                simulation_projected_available_to_promise=projected,
                warehouse_available_to_promise=warehouse.available_to_promise,
                warehouse_minus_simulation=delta,
                diverged=delta != 0,
                explanation=(
                    f"At {snapshot.as_of.isoformat()}, simulation base availability was "
                    f"{base} {simulated.unit_of_measure.value} with {expected} expected "
                    f"inbound, giving {projected}; warehouse availability was "
                    f"{warehouse.available_to_promise}. Difference (warehouse - "
                    f"simulation) was {delta}."
                ),
            )
        )
    return tuple(comparisons)


def _audit_decision(
    *,
    original_trace: DecisionTrace,
    corrected_trace: DecisionTrace | None,
    corrected_engine: SimulationEngine,
    event_by_id: dict[str, object],
    snapshot: WarehouseSnapshot,
) -> ReconciliationFinding:
    invalidated, unresolved = _audit_assumptions(
        original_trace, event_by_id, snapshot
    )
    invalidated_outputs = tuple(
        corrected_engine.invalidated_output_event_ids_by_decision.get(
            original_trace.decision_id, []
        )
    )

    if not original_trace.policy_consistent:
        verdict = ReconciliationVerdict.SOURCE_INCONSISTENT_REVIEW_REQUIRED
    elif corrected_trace is None or unresolved:
        verdict = ReconciliationVerdict.INDETERMINATE_REVIEW_REQUIRED
    elif original_trace.action is not corrected_trace.action:
        verdict = ReconciliationVerdict.WOULD_CHANGE_REVIEW_REQUIRED
    elif (
        original_trace.decision_available_to_promise
        == corrected_trace.decision_available_to_promise
        and not invalidated
    ):
        verdict = ReconciliationVerdict.CONFIRMED_SOUND
    else:
        verdict = ReconciliationVerdict.SOUND_WITH_DATA_GAP

    review_verdicts = {
        ReconciliationVerdict.WOULD_CHANGE_REVIEW_REQUIRED,
        ReconciliationVerdict.INDETERMINATE_REVIEW_REQUIRED,
        ReconciliationVerdict.SOURCE_INCONSISTENT_REVIEW_REQUIRED,
    }
    would_change = (
        None
        if corrected_trace is None
        else original_trace.action is not corrected_trace.action
    )
    reasoning_gap = _reasoning_gap(
        original_trace=original_trace,
        corrected_trace=corrected_trace,
        invalidated=invalidated,
        unresolved=unresolved,
    )
    audit_explanation = _audit_explanation(
        original_trace=original_trace,
        corrected_trace=corrected_trace,
        verdict=verdict,
        invalidated=invalidated,
        unresolved=unresolved,
        invalidated_outputs=invalidated_outputs,
    )
    return ReconciliationFinding(
        decision_id=original_trace.decision_id,
        order_id=original_trace.order_id,
        sku=original_trace.sku,
        original_trace=original_trace,
        corrected_trace=corrected_trace,
        verdict=verdict,
        would_change=would_change,
        requires_human_review=verdict in review_verdicts,
        invalidated_assumption_event_ids=invalidated,
        unresolved_assumption_event_ids=unresolved,
        invalidated_output_event_ids=invalidated_outputs,
        reasoning_gap=reasoning_gap,
        audit_explanation=audit_explanation,
    )


def _audit_assumptions(
    trace: DecisionTrace,
    event_by_id: dict[str, object],
    snapshot: WarehouseSnapshot,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    observations = {
        delivery.delivery_id: delivery for delivery in snapshot.inbound_deliveries
    }
    invalidated: list[str] = []
    unresolved: list[str] = []

    for event_id in trace.assumption_event_ids:
        event = event_by_id.get(event_id)
        if not isinstance(event, InboundDeliveryExpectedEvent):
            unresolved.append(event_id)
            continue
        observation = observations.get(event.payload.delivery_id)
        if observation is None:
            unresolved.append(event_id)
            continue
        if (
            observation.sku != event.payload.sku
            or observation.unit_of_measure is not event.payload.unit_of_measure
            or observation.expected_quantity != event.payload.expected_quantity
        ):
            unresolved.append(event_id)
            continue
        if observation.status is InboundDeliveryStatus.UNKNOWN:
            unresolved.append(event_id)
            continue
        fully_received = (
            observation.status is InboundDeliveryStatus.RECEIVED
            and observation.received_quantity >= event.payload.expected_quantity
        )
        if not fully_received:
            invalidated.append(event_id)

    return tuple(invalidated), tuple(unresolved)


def _reasoning_gap(
    *,
    original_trace: DecisionTrace,
    corrected_trace: DecisionTrace | None,
    invalidated: tuple[str, ...],
    unresolved: tuple[str, ...],
) -> str:
    unit = original_trace.unit_of_measure.value
    if not original_trace.policy_consistent:
        return (
            f"The recorded action {original_trace.action.value} did not follow the declared "
            f"policy action {original_trace.policy_action.value}."
        )
    if corrected_trace is None:
        return (
            "The snapshot does not provide a safe replay boundary for this decision, so a "
            "corrected action cannot be established automatically."
        )
    if unresolved:
        return (
            f"Warehouse evidence could not resolve assumption events {', '.join(unresolved)}."
        )
    difference = (
        corrected_trace.decision_available_to_promise
        - original_trace.decision_available_to_promise
    )
    if invalidated:
        return (
            f"Assumption events {', '.join(invalidated)} were invalidated by warehouse "
            f"evidence, changing decision availability by {difference} {unit}."
        )
    if difference != 0:
        return (
            f"Warehouse truth changed decision availability by {difference} {unit}, but "
            "no explicit decision assumption was invalidated."
        )
    return "Warehouse truth confirmed the facts and policy used by the original decision."


def _audit_explanation(
    *,
    original_trace: DecisionTrace,
    corrected_trace: DecisionTrace | None,
    verdict: ReconciliationVerdict,
    invalidated: tuple[str, ...],
    unresolved: tuple[str, ...],
    invalidated_outputs: tuple[str, ...],
) -> str:
    unit = original_trace.unit_of_measure.value
    original_basis = (
        f"Originally, {original_trace.decision_available_to_promise} {unit} was available "
        f"for an order of {original_trace.order_quantity} {unit}; "
        f"{original_trace.action.value} was recorded"
    )
    if original_trace.policy_consistent:
        original_basis += " and was policy-consistent."
    else:
        original_basis += " but did not match the declared policy."

    if corrected_trace is None:
        corrected_basis = "A corrected decision could not be reconstructed safely."
    else:
        corrected_basis = (
            f"Warehouse-corrected replay produced "
            f"{corrected_trace.decision_available_to_promise} {unit} and action "
            f"{corrected_trace.action.value}."
        )

    evidence_notes: list[str] = []
    if invalidated:
        evidence_notes.append(
            f"Warehouse evidence invalidated assumption events {', '.join(invalidated)}."
        )
    if unresolved:
        evidence_notes.append(
            f"Assumption events {', '.join(unresolved)} remain unresolved."
        )
    if invalidated_outputs:
        evidence_notes.append(
            f"Historical output events {', '.join(invalidated_outputs)} remain in the "
            "ledger but are invalid in the candidate branch."
        )

    conclusion = {
        ReconciliationVerdict.CONFIRMED_SOUND: (
            "The original decision is accepted because both its facts and action were "
            "confirmed."
        ),
        ReconciliationVerdict.SOUND_WITH_DATA_GAP: (
            "The original decision is accepted because the corrected facts still lead to "
            "the same action."
        ),
        ReconciliationVerdict.WOULD_CHANGE_REVIEW_REQUIRED: (
            "The action would change, so the historical decision and downstream effects "
            "require human review."
        ),
        ReconciliationVerdict.INDETERMINATE_REVIEW_REQUIRED: (
            "Available evidence is insufficient for an automatic conclusion; human review "
            "is required."
        ),
        ReconciliationVerdict.SOURCE_INCONSISTENT_REVIEW_REQUIRED: (
            "The recorded decision conflicts with its declared policy; human review is "
            "required."
        ),
    }[verdict]
    return " ".join(
        [original_basis, corrected_basis, *evidence_notes, conclusion]
    )


def _report_summary(
    findings: tuple[ReconciliationFinding, ...],
    promotion_status: PromotionStatus,
) -> str:
    confirmed = sum(
        finding.verdict is ReconciliationVerdict.CONFIRMED_SOUND
        for finding in findings
    )
    sound_with_gap = sum(
        finding.verdict is ReconciliationVerdict.SOUND_WITH_DATA_GAP
        for finding in findings
    )
    review = sum(finding.requires_human_review for finding in findings)
    return (
        f"Audited {len(findings)} decisions: {confirmed} confirmed sound, "
        f"{sound_with_gap} sound despite a data gap, and {review} requiring human "
        f"review. Candidate promotion status is {promotion_status.value}."
    )
