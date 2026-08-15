"""Contract tests for the committed end-to-end demonstration fixtures."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from reconciliation_agent.models import (
    EventBatch,
    EventType,
    ExpectedOutcomes,
    FulfillmentDecidedEvent,
    InboundDeliveryExpectedEvent,
    InboundDeliveryStatus,
    InventorySource,
    ReconciliationVerdict,
    ScenarioBundle,
    UnitOfMeasure,
    WarehouseSnapshot,
    count_events_received_before,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"


def load_json(name: str) -> object:
    with (DEMO / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def test_demo_fixtures_satisfy_assessment_contract() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    snapshot = WarehouseSnapshot.model_validate(load_json("warehouse_snapshot.json"))
    ScenarioBundle(event_batch=events, warehouse_snapshot=snapshot)

    assert len(events.events) == 12
    assert len({event.event_type for event in events.events}) == 6
    assert len(
        [event for event in events.events if isinstance(event, FulfillmentDecidedEvent)]
    ) == 3
    assert count_events_received_before(events, snapshot.received_at) == 12


def test_demo_contains_state_decision_and_output_events() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    event_types = {event.event_type for event in events.events}

    assert EventType.SIMULATION_INITIALIZED in event_types
    assert EventType.INBOUND_DELIVERY_EXPECTED in event_types
    assert EventType.STOCK_ADJUSTED in event_types
    assert EventType.FULFILLMENT_DECIDED in event_types
    assert EventType.SHIPMENT_PLANNED in event_types


def test_golden_outcomes_cover_all_required_verdicts() -> None:
    expected = ExpectedOutcomes.model_validate(load_json("expected_outcomes.json"))
    verdicts = Counter(item.verdict for item in expected.decisions)

    assert verdicts[ReconciliationVerdict.CONFIRMED_SOUND] == 1
    assert verdicts[ReconciliationVerdict.SOUND_WITH_DATA_GAP] == 1
    assert verdicts[ReconciliationVerdict.WOULD_CHANGE_REVIEW_REQUIRED] == 1


def test_changed_decision_invalidates_its_output() -> None:
    expected = ExpectedOutcomes.model_validate(load_json("expected_outcomes.json"))
    changed = next(item for item in expected.decisions if item.decision_id == "decision-300")

    assert changed.invalidated_output_event_ids == ("evt-012",)


def test_snapshot_replaces_only_the_pre_snapshot_baseline_and_assumption() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    snapshot = WarehouseSnapshot.model_validate(load_json("warehouse_snapshot.json"))

    at_or_before_snapshot = [
        event.event_id for event in events.events if event.occurred_at <= snapshot.as_of
    ]
    after_snapshot = [
        event.event_id for event in events.events if event.occurred_at > snapshot.as_of
    ]

    assert at_or_before_snapshot == ["evt-001", "evt-002"]
    assert after_snapshot == [f"evt-{number:03d}" for number in range(3, 13)]


def test_expected_delivery_maps_to_snapshot_and_changed_decision() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    snapshot = WarehouseSnapshot.model_validate(load_json("warehouse_snapshot.json"))
    expected = ExpectedOutcomes.model_validate(load_json("expected_outcomes.json"))

    delivery_event = next(
        event
        for event in events.events
        if isinstance(event, InboundDeliveryExpectedEvent)
    )
    delivery_observation = snapshot.inbound_deliveries[0]
    changed = next(item for item in expected.decisions if item.decision_id == "decision-300")

    assert delivery_event.event_id == "evt-002"
    assert delivery_event.payload.delivery_id == delivery_observation.delivery_id
    assert delivery_event.payload.sku == delivery_observation.sku == changed.sku
    assert delivery_event.payload.expected_quantity == 4
    assert delivery_observation.expected_quantity == 4
    assert delivery_observation.received_quantity == 0
    assert delivery_observation.status is InboundDeliveryStatus.DELAYED
    assert changed.original_base_available_to_promise == 1
    assert changed.original_expected_inbound == 4
    assert changed.original_decision_available_to_promise == 5
    assert changed.corrected_available_to_promise == 1
    assert changed.assumption_event_ids == ("evt-002",)
    assert changed.invalidated_assumption_event_ids == ("evt-002",)


def test_demo_uses_cases_consistently_and_identifies_each_source() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    snapshot = WarehouseSnapshot.model_validate(load_json("warehouse_snapshot.json"))
    initialization = events.events[0]

    assert all(
        item.unit_of_measure is UnitOfMeasure.CASE
        and item.source is InventorySource.SIMULATION_MODEL
        for item in initialization.payload.inventory
    )
    assert all(
        item.unit_of_measure is UnitOfMeasure.CASE
        and item.source is InventorySource.WAREHOUSE_SNAPSHOT
        for item in snapshot.inventory
    )
    assert all(
        delivery.unit_of_measure is UnitOfMeasure.CASE
        for delivery in snapshot.inbound_deliveries
    )
