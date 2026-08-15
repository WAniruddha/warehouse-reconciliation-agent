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
    ScenarioBundle,
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

    verdict_counts = Counter(item.verdict.value for item in expected.decisions)
    verdict_summary = ", ".join(
        f"{verdict}={count}" for verdict, count in sorted(verdict_counts.items())
    )

    print("Demo fixtures are valid.")
    print(f"Events: {len(event_batch.events)}")
    print(f"Event types: {len({event.event_type for event in event_batch.events})}")
    print(f"Decisions: {len(decisions)}")
    print(
        "Events processed before snapshot arrival: "
        f"{count_events_received_before(event_batch, snapshot.received_at)}"
    )
    print(f"Expected verdicts: {verdict_summary}")


if __name__ == "__main__":
    main()

