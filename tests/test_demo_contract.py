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
    ReconciliationVerdict,
    ScenarioBundle,
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

    assert len(events.events) == 11
    assert len({event.event_type for event in events.events}) == 5
    assert len(
        [event for event in events.events if isinstance(event, FulfillmentDecidedEvent)]
    ) == 3
    assert count_events_received_before(events, snapshot.received_at) == 11


def test_demo_contains_state_decision_and_output_events() -> None:
    events = EventBatch.model_validate(load_json("events.json"))
    event_types = {event.event_type for event in events.events}

    assert EventType.SIMULATION_INITIALIZED in event_types
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

    assert changed.invalidated_output_event_ids == ("evt-011",)

