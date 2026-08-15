"""Validate the committed demonstration fixtures without running reconciliation."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from reconciliation_agent.models import (  # noqa: E402
    EventBatch,
    ExpectedOutcomes,
    FulfillmentDecidedEvent,
    InboundDeliveryExpectedEvent,
    InboundDeliveryStatus,
    OrderReceivedEvent,
    ScenarioBundle,
    ShipmentPlannedEvent,
    WarehouseSnapshot,
    count_events_received_before,
)


def load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    demo_directory = REPOSITORY_ROOT / "data" / "demo"
    event_batch = EventBatch.model_validate(load_json(demo_directory / "events.json"))
    snapshot = WarehouseSnapshot.model_validate(
        load_json(demo_directory / "warehouse_snapshot.json")
    )
    expected = ExpectedOutcomes.model_validate(
        load_json(demo_directory / "expected_outcomes.json")
    )
    ScenarioBundle(event_batch=event_batch, warehouse_snapshot=snapshot)

    if expected.simulation_id != event_batch.simulation_id:
        raise ValueError("Expected outcomes belong to a different simulation")

    decisions = [
        event for event in event_batch.events if isinstance(event, FulfillmentDecidedEvent)
    ]
    decision_ids = {event.payload.decision_id for event in decisions}
    expected_decision_ids = {item.decision_id for item in expected.decisions}
    if decision_ids != expected_decision_ids:
        raise ValueError("Expected outcomes do not cover exactly the emitted decisions")

    events_by_id = {event.event_id: event for event in event_batch.events}
    orders_by_id = {
        event.payload.order_id: event
        for event in event_batch.events
        if isinstance(event, OrderReceivedEvent)
    }
    deliveries_by_id = {
        event.payload.delivery_id: event
        for event in event_batch.events
        if isinstance(event, InboundDeliveryExpectedEvent)
    }
    snapshot_deliveries_by_id = {
        delivery.delivery_id: delivery for delivery in snapshot.inbound_deliveries
    }

    for delivery_id, delivery_event in deliveries_by_id.items():
        if delivery_event.payload.expected_at > snapshot.as_of:
            continue
        observation = snapshot_deliveries_by_id.get(delivery_id)
        if observation is None:
            raise ValueError(
                f"Snapshot does not report expected delivery {delivery_id}"
            )
        expected_identity = (
            delivery_event.payload.sku,
            delivery_event.payload.expected_quantity,
            delivery_event.payload.unit_of_measure,
            delivery_event.payload.expected_at,
        )
        observed_identity = (
            observation.sku,
            observation.expected_quantity,
            observation.unit_of_measure,
            observation.expected_at,
        )
        if expected_identity != observed_identity:
            raise ValueError(
                f"Expected delivery {delivery_id} does not map cleanly to the snapshot"
            )

    invalidated_assumption_ids: set[str] = set()
    for expected_decision in expected.decisions:
        decision_event = next(
            event
            for event in decisions
            if event.payload.decision_id == expected_decision.decision_id
        )
        order_event = orders_by_id[decision_event.payload.order_id]
        if (
            expected_decision.order_id != order_event.payload.order_id
            or expected_decision.sku != order_event.payload.sku
            or expected_decision.order_quantity != order_event.payload.quantity
            or expected_decision.unit_of_measure
            is not order_event.payload.unit_of_measure
            or expected_decision.original_action is not decision_event.payload.action
        ):
            raise ValueError(
                f"Expected outcome {expected_decision.decision_id} does not map to its "
                "order and decision events"
            )

        for assumption_event_id in expected_decision.assumption_event_ids:
            assumption_event = events_by_id.get(assumption_event_id)
            if not isinstance(assumption_event, InboundDeliveryExpectedEvent):
                raise ValueError(
                    f"Assumption {assumption_event_id} is not an expected-delivery event"
                )

        for assumption_event_id in expected_decision.invalidated_assumption_event_ids:
            assumption_event = events_by_id[assumption_event_id]
            assert isinstance(assumption_event, InboundDeliveryExpectedEvent)
            observation = snapshot_deliveries_by_id.get(
                assumption_event.payload.delivery_id
            )
            if observation is None:
                raise ValueError(
                    f"Invalidated assumption {assumption_event_id} has no snapshot evidence"
                )
            if (
                observation.status is InboundDeliveryStatus.RECEIVED
                and observation.received_quantity
                >= assumption_event.payload.expected_quantity
            ):
                raise ValueError(
                    f"Assumption {assumption_event_id} is marked invalidated but was fulfilled"
                )
            invalidated_assumption_ids.add(assumption_event_id)

        for output_event_id in expected_decision.invalidated_output_event_ids:
            if not isinstance(events_by_id.get(output_event_id), ShipmentPlannedEvent):
                raise ValueError(
                    f"Invalidated output {output_event_id} is not a shipment event"
                )

    verdict_counts = Counter(item.verdict.value for item in expected.decisions)
    verdict_summary = ", ".join(
        f"{verdict}={count}" for verdict, count in sorted(verdict_counts.items())
    )

    print("Demo fixtures are valid.")
    print(f"Events: {len(event_batch.events)}")
    print(f"Event types: {len({event.event_type for event in event_batch.events})}")
    print(f"Decisions: {len(decisions)}")
    print(f"Expected inbound deliveries: {len(deliveries_by_id)}")
    print(f"Invalidated assumptions: {len(invalidated_assumption_ids)}")
    print(
        "Events processed before snapshot arrival: "
        f"{count_events_received_before(event_batch, snapshot.received_at)}"
    )
    print(f"Expected verdicts: {verdict_summary}")


if __name__ == "__main__":
    main()
