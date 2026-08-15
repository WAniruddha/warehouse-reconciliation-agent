# LEC Simulation Reconciliation Agent

An auditable event-driven agent that records why simulation decisions were made,
receives delayed warehouse truth, reconstructs a corrected branch, and explains whether
each historical decision remains sound or requires human review.

The core is deterministic and runs locally without an LLM or API key.

## What works now

- ingests and validates six types of timestamped simulation events;
- applies events in sequence to an inventory state;
- evaluates every recorded fulfilment decision against a versioned policy;
- captures the facts, assumptions, evidence IDs, calculation, and written explanation
  available at decision time;
- stores raw events and decision reasoning in a persistent SQLite audit ledger;
- receives a delayed warehouse snapshot and detects inventory divergence;
- starts a separate corrected branch from the snapshot and replays post-snapshot facts;
- re-evaluates all auditable decisions without rewriting historical events;
- generates deterministic verdicts and explains the reasoning gap;
- writes standalone Markdown and JSON reports for review without rerunning the agent.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
```

Activate the environment, then install and run:

```bash
python -m pip install -r requirements-dev.txt
python scripts/run_demo.py
```

The command prints the complete audit and creates:

```text
.runtime/reconciliation_audit.db
.runtime/reconciliation_report.md
.runtime/reconciliation_report.json
```

These runtime files are intentionally ignored by Git.

The same command is available after installation as:

```bash
lec-reconcile
```

Custom input and output paths can be supplied with:

```bash
lec-reconcile \
  --events data/demo/events.json \
  --snapshot data/demo/warehouse_snapshot.json \
  --database .runtime/audit.db \
  --report .runtime/report.md \
  --json-report .runtime/report.json
```

## How reconciliation works

1. The original branch processes events exactly in sequence.
2. Inventory state is updated by initialization, stock-adjustment, and shipment events.
3. An expected inbound delivery contributes to projected availability only after its
   scheduled arrival time. It remains explicitly labelled as an assumption.
4. At every `FULFILLMENT_DECIDED` event, the agent stores what was known, the arithmetic,
   policy result, recorded action, evidence IDs, assumptions, and written explanation.
5. When the warehouse snapshot arrives, its `as_of` time becomes the corrected replay
   boundary.
6. The corrected branch starts from authoritative warehouse inventory and replays only
   later facts and orders. Historical decision and shipment events remain immutable audit
   evidence.
7. Candidate decisions are recomputed with the same versioned policy.
8. The agent compares original and corrected decisions and produces one of five verdicts.

For this single-snapshot lifecycle, the demo assumes that state-changing events after the
snapshot's `as_of` time are complete and replayable. Because no later delivery-confirmation
event exists, the delivery reported as delayed at the snapshot boundary remains excluded
from corrected availability.

| Verdict | Meaning | Automatic handling |
|---|---|---|
| `CONFIRMED_SOUND` | Warehouse truth confirms both facts and action | Accept |
| `SOUND_WITH_DATA_GAP` | Facts changed but the action remains correct | Accept with documented gap |
| `WOULD_CHANGE_REVIEW_REQUIRED` | Corrected action differs | Human review |
| `INDETERMINATE_REVIEW_REQUIRED` | Evidence cannot support safe replay | Human review |
| `SOURCE_INCONSISTENT_REVIEW_REQUIRED` | Recorded action conflicts with its policy | Human review |

The corrected branch is automatically promoted only when no finding requires human
review.

## Demonstration dataset

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

### Event order and ID mapping

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

### Generated decision audit

| Decision | Original basis | Warehouse-corrected basis | Generated verdict |
|---|---|---|---|
| `decision-100` | 10 available; order 4 | 10 available; order 4 | `CONFIRMED_SOUND` |
| `decision-200` | 8 - 1 damaged = 7; order 4 | 6 - 1 damaged = 5; order 4 | `SOUND_WITH_DATA_GAP` |
| `decision-300` | 1 base + 4 expected = 5; order 3 | 1 base + 0 received = 1; order 3 | `WOULD_CHANGE_REVIEW_REQUIRED` |

For `decision-300`, dispatch was policy-consistent using the information available at the
time. Warehouse evidence later invalidates the scheduled-delivery assumption, so the
candidate action becomes backorder. `evt-012` remains in history but is marked invalid in
the corrected branch.

`data/demo/expected_outcomes.json` is a golden test oracle. The reconciliation engine does
not read it when producing its report.

## Persistent audit ledger

The SQLite database stores:

| Table | Stored evidence |
|---|---|
| `events` | Complete immutable event JSON and timestamps |
| `warehouse_snapshots` | Authoritative snapshot input |
| `decision_traces` | Original and corrected facts, actions, evidence and explanations |
| `reconciliation_reports` | Complete machine-readable report |
| `reconciliation_findings` | One searchable verdict and explanation per decision |

Writes are idempotent, so an exact rerun does not duplicate records. Reusing an event,
snapshot, decision, or report identity with different contents raises an integrity error
instead of silently rewriting audit history.

## Test the project

Validate only the committed fixture contract:

```bash
python scripts/validate_demo.py
```

Run the complete automated suite:

```bash
python -m pytest -q
python -m ruff check .
```

The tests independently check the generated report against the golden outcomes, both
reasoning branches, SQLite persistence and idempotency, report export, policy boundaries,
the delayed-delivery failure, and a second case where the warehouse fully confirms the
expected delivery.

## Project structure

```text
src/reconciliation_agent/
  models.py           Input and golden-outcome contracts
  policy.py           Deterministic fulfilment policy
  engine.py           Event-driven state and decision reasoning
  reconciliation.py   Snapshot comparison, replay and verdict classification
  report.py           Structured trace and report contracts
  store.py            SQLite audit ledger
  render.py           Terminal and Markdown reports
  cli.py              Command-line entry point
data/demo/             Events, delayed snapshot and golden expected outcomes
scripts/               Demo runner and fixture validator
tests/                 Contract and end-to-end tests
```

## Design decisions

- **Deterministic core:** inventory arithmetic and verdicts are testable and reproducible.
- **Immutable history:** reconciliation never edits recorded decisions or output events.
- **Separate corrected branch:** warehouse truth seeds a candidate state, avoiding accidental
  corruption of the live simulation record.
- **Explicit assumptions:** scheduled inbound stock is never disguised as confirmed stock.
- **Human-review boundary:** changed, unresolved, or internally inconsistent decisions are
  not automatically promoted.
- **No required LLM:** an LLM may improve wording later, but cannot decide inventory or the
  audit verdict.

## What I would add with more time

- FastAPI ingestion endpoints and a Streamlit audit dashboard;
- additional malformed, missing-SKU, unit-mismatch, duplicate, out-of-order, and
  pre-snapshot-decision fixtures;
- transactional event ingestion with optimistic concurrency and idempotency keys;
- Docker packaging and GitHub Actions checks;
- pluggable policy versions and configurable unit conversion;
- optional schema-constrained LLM narration with deterministic text as the fallback;
- signed report hashes and richer reviewer approval history.
