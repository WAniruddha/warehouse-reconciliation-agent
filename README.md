# LEC Simulation Reconciliation Agent

An auditable event-driven agent that records simulation decisions, receives a delayed
warehouse snapshot, reconstructs a warehouse-corrected branch, and reports whether each
historical decision remains sound or requires human review.

## Current implementation checkpoint

This checkpoint contains the validated data contract and demonstration scenario:

- strict Pydantic contracts for six state, assumption, decision, and output event types;
- cross-event validation for ordering, units, correlation, and causation;
- a timestamped 12-event London beverage-distribution scenario;
- a delayed warehouse snapshot with inventory and inbound-delivery evidence;
- a golden expected-outcome fixture covering all three requested audit results.

The state engine, persistent decision ledger, reconciliation logic, and interface are the
next implementation checkpoint. The current validator proves that the inputs and expected
results map together; it does not pretend that reconciliation has already run.

## Validate the demonstration data

From the repository root:

```bash
python scripts/validate_demo.py
```

Expected result:

```text
Demo fixtures are valid.
Events: 12
Event types: 6
Decisions: 3
Expected inbound deliveries: 1
Invalidated assumptions: 1
Events processed before snapshot arrival: 12
Expected verdicts: CONFIRMED_SOUND=1, SOUND_WITH_DATA_GAP=1, WOULD_CHANGE_REVIEW_REQUIRED=1
```

## Dataset at a glance

All quantities are **cases**. Keeping the unit on inventory, order, adjustment, delivery,
and shipment records prevents a silent case-versus-bottle comparison.

| Demo product | SKU | Simulation opening cases | Warehouse cases at 09:00 |
|---|---|---:|---:|
| Sparkling water 750 ml | `BEV-SPARKLING-750` | 10 | 10 |
| Tonic water 200 ml | `BEV-TONIC-200` | 8 | 6 |
| Ginger beer 330 ml | `BEV-GINGER-330` | 1 | 1 |

The simulation also receives `evt-002`: carrier schedule `delivery-300` says four cases of
ginger beer are expected at 08:55. It is an explicit assumption, not confirmed stock.
The 09:00 warehouse snapshot later reports that the same delivery is `DELAYED`, with zero
cases received.

## Event order and ID mapping

The event number is its processing sequence. `causation_id` connects each decision to its
order and each shipment to its decision.

| Sequence | Event ID | Meaning | Related chain |
|---:|---|---|---|
| 1 | `evt-001` | Initialize the London warehouse model | Simulation baseline |
| 2 | `evt-002` | Expect four ginger-beer cases from `delivery-300` | Assumption used later |
| 3 | `evt-003` | Receive sparkling-water `order-100` for four cases | Order 100 |
| 4 | `evt-004` | Record `decision-100`: dispatch | `evt-003` -> decision |
| 5 | `evt-005` | Plan `shipment-100` | `evt-004` -> output |
| 6 | `evt-006` | Remove one damaged tonic-water case | State change |
| 7 | `evt-007` | Receive tonic-water `order-200` for four cases | Order 200 |
| 8 | `evt-008` | Record `decision-200`: dispatch | `evt-007` -> decision |
| 9 | `evt-009` | Plan `shipment-200` | `evt-008` -> output |
| 10 | `evt-010` | Receive ginger-beer `order-300` for three cases | Order 300 |
| 11 | `evt-011` | Record `decision-300`: dispatch | `evt-010` + assumption `evt-002` |
| 12 | `evt-012` | Plan `shipment-300` | `evt-011` -> output |

The warehouse snapshot is a separate input, not event 13. It is authoritative **as of
09:00** but arrives at **09:05**, after all 12 events have been processed.

## How the two branches map

The original branch uses events 1-12 and preserves exactly what the simulation believed.
The corrected branch uses the warehouse snapshot as the 09:00 baseline. Events 1 and 2
are therefore superseded by warehouse facts; post-snapshot state changes and orders from
events 3-12 are applied in order. Recorded decisions and shipments remain immutable audit
evidence, while candidate decisions are recomputed from the corrected state.

| Decision | What the simulation knew | Warehouse-corrected view | Expected audit result |
|---|---|---|---|
| `decision-100` | 10 available, order 4 | 10 available, order 4 | `CONFIRMED_SOUND` |
| `decision-200` | 8 - 1 damaged = 7; order 4 | 6 - 1 damaged = 5; order 4 | `SOUND_WITH_DATA_GAP` |
| `decision-300` | 1 base + 4 expected = 5; order 3 | 1 base + 0 received = 1; order 3 | `WOULD_CHANGE_REVIEW_REQUIRED` |

For `decision-300`, dispatch was consistent with the information and policy at the time,
but the action would change to backorder once the delayed-delivery evidence is known.
`evt-012` is retained in history and marked as an invalidated output in the corrected
branch rather than deleted.

## Scenario assumptions

- The warehouse snapshot is authoritative as of its `as_of` timestamp.
- The snapshot represents warehouse facts at or before `as_of`.
- Missing snapshot values mean unknown, not zero.
- Historical events, decisions, and outputs are immutable.
- Reconciliation creates a separate candidate branch and never rewrites history.
- `available_to_promise = on_hand - reserved - quarantined`.
- Projected decision availability may include an expected inbound delivery only after its
  scheduled arrival time; the reasoning trace must identify that assumption.
- Fulfilment policy v1 dispatches only when decision availability covers the order quantity.

## Why the core does not need an LLM or API key

Inventory arithmetic, causal replay, and verdict classification must be deterministic and
testable. An optional local or hosted LLM may later turn structured evidence into smoother
prose, but it will not calculate stock, choose the verdict, or be required to run the demo.
