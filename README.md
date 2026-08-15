# LEC Simulation Reconciliation Agent

An auditable event-driven agent that records simulation decisions, receives a delayed
warehouse snapshot, reconstructs a warehouse-corrected branch, and reports whether each
historical decision remains sound or requires human review.

## Current implementation checkpoint

This checkpoint contains:

- strict Pydantic contracts for state, decision, and output events;
- cross-event validation for ordering, correlation, and causation;
- a timestamped 11-event demonstration stream;
- a delayed warehouse snapshot;
- a golden expected-outcome fixture covering all three requested results.

The state engine, persistence, reconciliation logic, API, and interface are added in later
checkpoints.

## Validate the demonstration data

From the repository root:

```bash
python scripts/validate_demo.py
```

Expected result:

```text
Demo fixtures are valid.
Events: 11
Event types: 5
Decisions: 3
Events processed before snapshot arrival: 11
Expected verdicts: CONFIRMED_SOUND=1, SOUND_WITH_DATA_GAP=1, WOULD_CHANGE_REVIEW_REQUIRED=1
```

## Scenario assumptions

- The warehouse snapshot is authoritative as of its `as_of` timestamp.
- The snapshot represents state after all real events at or before `as_of`.
- Events after `as_of` must be replayable and causally linked.
- Missing snapshot values mean unknown, not zero.
- Historical decisions and outputs are immutable; reconciliation creates a separate branch.
- `available_to_promise = on_hand - reserved - quarantined`.
- Fulfilment policy v1 dispatches only when available-to-promise covers the order quantity.

