"""Event-driven simulation state and decision-reasoning engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import (
    DecisionAction,
    FulfillmentDecidedEvent,
    InboundDeliveryExpectedEvent,
    OrderReceivedEvent,
    ShipmentPlannedEvent,
    SimulationEvent,
    SimulationInitializedEvent,
    StockAdjustedEvent,
    UnitOfMeasure,
    WarehouseSnapshot,
)
from .policy import evaluate_fulfilment
from .report import DecisionBranch, DecisionTrace


class StateTransitionError(ValueError):
    """Raised when a valid event cannot be applied to the current state."""


@dataclass(slots=True)
class InventoryState:
    sku: str
    on_hand: int
    reserved: int
    quarantined: int
    unit_of_measure: UnitOfMeasure

    @property
    def base_available_to_promise(self) -> int:
        return self.on_hand - self.reserved - self.quarantined


@dataclass(frozen=True, slots=True)
class ExpectedDelivery:
    event_id: str
    delivery_id: str
    sku: str
    quantity: int
    unit_of_measure: UnitOfMeasure
    expected_at: datetime


class SimulationEngine:
    """Apply events in order and capture a complete trace at every decision."""

    def __init__(self, simulation_id: str, branch: DecisionBranch) -> None:
        self.simulation_id = simulation_id
        self.branch = branch
        self.warehouse_id: str | None = None
        self.source_snapshot_id: str | None = None
        self.inventory: dict[str, InventoryState] = {}
        self.expected_deliveries: dict[str, ExpectedDelivery] = {}
        self.orders: dict[str, OrderReceivedEvent] = {}
        self.decision_traces: dict[str, DecisionTrace] = {}
        self.output_event_ids_by_decision: dict[str, list[str]] = {}
        self.invalidated_output_event_ids_by_decision: dict[str, list[str]] = {}
        self.evidence_ids_by_sku: dict[str, list[str]] = {}
        self.last_occurred_at: datetime | None = None
        self.initialized = False

    @classmethod
    def from_warehouse_snapshot(
        cls, snapshot: WarehouseSnapshot
    ) -> SimulationEngine:
        """Start a corrected branch from authoritative warehouse state."""

        engine = cls(snapshot.simulation_id, DecisionBranch.CORRECTED)
        engine.warehouse_id = snapshot.warehouse_id
        engine.source_snapshot_id = snapshot.snapshot_id
        engine.inventory = {
            item.sku: InventoryState(
                sku=item.sku,
                on_hand=item.on_hand,
                reserved=item.reserved,
                quarantined=item.quarantined,
                unit_of_measure=item.unit_of_measure,
            )
            for item in snapshot.inventory
        }
        engine.evidence_ids_by_sku = {
            item.sku: [snapshot.snapshot_id] for item in snapshot.inventory
        }
        engine.last_occurred_at = snapshot.as_of
        engine.initialized = True
        return engine

    def process(self, event: SimulationEvent) -> DecisionTrace | None:
        """Apply one event and return a trace when the event is a decision."""

        if event.simulation_id != self.simulation_id:
            raise StateTransitionError(
                f"Event {event.event_id} belongs to a different simulation"
            )
        if self.last_occurred_at is not None and event.occurred_at < self.last_occurred_at:
            raise StateTransitionError("Events cannot be applied out of occurred_at order")

        self.last_occurred_at = event.occurred_at

        if isinstance(event, SimulationInitializedEvent):
            self._initialize(event)
        elif not self.initialized:
            raise StateTransitionError("State must be initialized before applying events")
        elif isinstance(event, InboundDeliveryExpectedEvent):
            self._expect_delivery(event)
        elif isinstance(event, OrderReceivedEvent):
            self._receive_order(event)
        elif isinstance(event, StockAdjustedEvent):
            self._adjust_stock(event)
        elif isinstance(event, FulfillmentDecidedEvent):
            return self._record_decision(event)
        elif isinstance(event, ShipmentPlannedEvent):
            self._plan_shipment(event)
        else:  # pragma: no cover - the discriminated event union prevents this
            raise StateTransitionError(f"Unsupported event {event.event_type}")
        return None

    def availability_at(self, sku: str, at_time: datetime) -> tuple[int, int, int]:
        """Return base, eligible expected inbound, and projected availability."""

        inventory = self._inventory_for(sku)
        eligible_deliveries = self._eligible_deliveries(sku, at_time)
        expected_inbound = sum(delivery.quantity for delivery in eligible_deliveries)
        base = inventory.base_available_to_promise
        return base, expected_inbound, base + expected_inbound

    def final_available_to_promise(self) -> dict[str, int]:
        """Return the final projected availability for every known SKU."""

        if self.last_occurred_at is None:
            return {}
        return {
            sku: self.availability_at(sku, self.last_occurred_at)[2]
            for sku in sorted(self.inventory)
        }

    def _initialize(self, event: SimulationInitializedEvent) -> None:
        if self.initialized:
            raise StateTransitionError("Simulation state was initialized more than once")
        if self.branch is DecisionBranch.CORRECTED:
            raise StateTransitionError(
                "A corrected branch must be initialized from a warehouse snapshot"
            )

        self.warehouse_id = event.payload.warehouse_id
        self.inventory = {
            item.sku: InventoryState(
                sku=item.sku,
                on_hand=item.on_hand,
                reserved=item.reserved,
                quarantined=item.quarantined,
                unit_of_measure=item.unit_of_measure,
            )
            for item in event.payload.inventory
        }
        self.evidence_ids_by_sku = {
            item.sku: [event.event_id] for item in event.payload.inventory
        }
        self.initialized = True

    def _expect_delivery(self, event: InboundDeliveryExpectedEvent) -> None:
        payload = event.payload
        inventory = self._inventory_for(payload.sku)
        self._require_matching_unit(
            inventory.unit_of_measure,
            payload.unit_of_measure,
            f"expected delivery {payload.delivery_id}",
        )
        self.expected_deliveries[payload.delivery_id] = ExpectedDelivery(
            event_id=event.event_id,
            delivery_id=payload.delivery_id,
            sku=payload.sku,
            quantity=payload.expected_quantity,
            unit_of_measure=payload.unit_of_measure,
            expected_at=payload.expected_at,
        )
        self.evidence_ids_by_sku[payload.sku].append(event.event_id)

    def _receive_order(self, event: OrderReceivedEvent) -> None:
        payload = event.payload
        inventory = self._inventory_for(payload.sku)
        self._require_matching_unit(
            inventory.unit_of_measure,
            payload.unit_of_measure,
            f"order {payload.order_id}",
        )
        self.orders[payload.order_id] = event

    def _adjust_stock(self, event: StockAdjustedEvent) -> None:
        payload = event.payload
        inventory = self._inventory_for(payload.sku)
        self._require_matching_unit(
            inventory.unit_of_measure,
            payload.unit_of_measure,
            f"stock adjustment {event.event_id}",
        )
        adjusted_on_hand = inventory.on_hand + payload.delta_on_hand
        if adjusted_on_hand < 0:
            raise StateTransitionError(
                f"Stock adjustment {event.event_id} would make on_hand negative"
            )
        inventory.on_hand = adjusted_on_hand
        self.evidence_ids_by_sku[payload.sku].append(event.event_id)

    def _record_decision(self, event: FulfillmentDecidedEvent) -> DecisionTrace:
        order = self.orders.get(event.payload.order_id)
        if order is None:
            raise StateTransitionError(
                f"Decision {event.payload.decision_id} references an unknown order"
            )

        sku = order.payload.sku
        base, expected_inbound, projected = self.availability_at(sku, event.occurred_at)
        eligible_deliveries = self._eligible_deliveries(sku, event.occurred_at)
        policy = evaluate_fulfilment(projected, order.payload.quantity)
        action = (
            event.payload.action
            if self.branch is DecisionBranch.ORIGINAL
            else policy.action
        )
        assumption_event_ids = tuple(
            delivery.event_id for delivery in eligible_deliveries
        )
        evidence_ids = tuple(
            dict.fromkeys([*self.evidence_ids_by_sku[sku], order.event_id])
        )
        policy_consistent = action is policy.action
        explanation = self._decision_explanation(
            event=event,
            order=order,
            base=base,
            expected_inbound=expected_inbound,
            projected=projected,
            eligible_deliveries=eligible_deliveries,
            action=action,
            policy_action=policy.action,
        )

        trace = DecisionTrace(
            simulation_id=self.simulation_id,
            branch=self.branch,
            decision_event_id=event.event_id,
            decision_id=event.payload.decision_id,
            order_id=order.payload.order_id,
            occurred_at=event.occurred_at,
            sku=sku,
            order_quantity=order.payload.quantity,
            unit_of_measure=order.payload.unit_of_measure,
            base_available_to_promise=base,
            expected_inbound=expected_inbound,
            decision_available_to_promise=projected,
            action=action,
            policy_action=policy.action,
            policy_version=event.payload.policy_version,
            policy_consistent=policy_consistent,
            evidence_ids=evidence_ids,
            assumption_event_ids=assumption_event_ids,
            explanation=explanation,
        )
        self.decision_traces[trace.decision_id] = trace

        if self.branch is DecisionBranch.CORRECTED and action is DecisionAction.DISPATCH:
            self.inventory[sku].reserved += order.payload.quantity
            self.evidence_ids_by_sku[sku].append(event.event_id)
        return trace

    def _plan_shipment(self, event: ShipmentPlannedEvent) -> None:
        payload = event.payload
        trace = self.decision_traces.get(payload.decision_id)
        if trace is None:
            raise StateTransitionError(
                f"Shipment {payload.shipment_id} references an unknown decision"
            )

        self.output_event_ids_by_decision.setdefault(payload.decision_id, []).append(
            event.event_id
        )
        if self.branch is DecisionBranch.CORRECTED:
            if trace.action is DecisionAction.BACKORDER:
                self.invalidated_output_event_ids_by_decision.setdefault(
                    payload.decision_id, []
                ).append(event.event_id)
            return

        if trace.action is DecisionAction.DISPATCH:
            inventory = self._inventory_for(payload.sku)
            inventory.reserved += payload.quantity
            self.evidence_ids_by_sku[payload.sku].append(event.event_id)

    def _eligible_deliveries(
        self, sku: str, at_time: datetime
    ) -> tuple[ExpectedDelivery, ...]:
        return tuple(
            delivery
            for delivery in self.expected_deliveries.values()
            if delivery.sku == sku and delivery.expected_at <= at_time
        )

    def _inventory_for(self, sku: str) -> InventoryState:
        try:
            return self.inventory[sku]
        except KeyError as error:
            raise StateTransitionError(f"No inventory state is available for SKU {sku}") from error

    @staticmethod
    def _require_matching_unit(
        state_unit: UnitOfMeasure,
        event_unit: UnitOfMeasure,
        subject: str,
    ) -> None:
        if state_unit is not event_unit:
            raise StateTransitionError(
                f"Unit mismatch for {subject}: state={state_unit}, event={event_unit}"
            )

    def _decision_explanation(
        self,
        *,
        event: FulfillmentDecidedEvent,
        order: OrderReceivedEvent,
        base: int,
        expected_inbound: int,
        projected: int,
        eligible_deliveries: tuple[ExpectedDelivery, ...],
        action: DecisionAction,
        policy_action: DecisionAction,
    ) -> str:
        unit = order.payload.unit_of_measure.value
        if eligible_deliveries:
            delivery_details = ", ".join(
                f"{delivery.delivery_id} ({delivery.quantity} {unit}, {delivery.event_id})"
                for delivery in eligible_deliveries
            )
            availability_sentence = (
                f"Base available-to-promise was {base} {unit}. Scheduled inbound "
                f"contributed {expected_inbound} {unit} from {delivery_details}, giving "
                f"projected availability of {projected} {unit}. The inbound quantity was "
                "an explicit assumption and was not warehouse-confirmed."
            )
        else:
            availability_sentence = (
                f"Available-to-promise was {base} {unit}; no expected inbound delivery "
                "was used."
            )

        comparison = ">=" if projected >= order.payload.quantity else "<"
        policy_sentence = (
            f"Order {order.payload.order_id} requested {order.payload.quantity} {unit}. "
            f"Under {event.payload.policy_version}, {projected} {comparison} "
            f"{order.payload.quantity}, so the policy action was {policy_action.value}."
        )

        if self.branch is DecisionBranch.ORIGINAL:
            consistency = "matched" if action is policy_action else "did not match"
            action_sentence = (
                f"The recorded action was {action.value}, which {consistency} the policy "
                "using the information available at that time."
            )
        else:
            action_sentence = (
                f"After starting from warehouse snapshot {self.source_snapshot_id} and "
                f"replaying later facts, the candidate action is {action.value}."
            )

        return " ".join(
            [
                f"At {event.occurred_at.isoformat()}, {availability_sentence}",
                policy_sentence,
                action_sentence,
            ]
        )
