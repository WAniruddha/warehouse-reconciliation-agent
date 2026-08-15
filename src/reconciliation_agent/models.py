"""Validated contracts for simulation events and delayed warehouse snapshots."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and accidental mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class EventType(StrEnum):
    SIMULATION_INITIALIZED = "SIMULATION_INITIALIZED"
    INBOUND_DELIVERY_EXPECTED = "INBOUND_DELIVERY_EXPECTED"
    ORDER_RECEIVED = "ORDER_RECEIVED"
    STOCK_ADJUSTED = "STOCK_ADJUSTED"
    FULFILLMENT_DECIDED = "FULFILLMENT_DECIDED"
    SHIPMENT_PLANNED = "SHIPMENT_PLANNED"


class DecisionAction(StrEnum):
    DISPATCH = "DISPATCH"
    BACKORDER = "BACKORDER"


class ReconciliationVerdict(StrEnum):
    CONFIRMED_SOUND = "CONFIRMED_SOUND"
    SOUND_WITH_DATA_GAP = "SOUND_WITH_DATA_GAP"
    WOULD_CHANGE_REVIEW_REQUIRED = "WOULD_CHANGE_REVIEW_REQUIRED"
    INDETERMINATE_REVIEW_REQUIRED = "INDETERMINATE_REVIEW_REQUIRED"
    SOURCE_INCONSISTENT_REVIEW_REQUIRED = "SOURCE_INCONSISTENT_REVIEW_REQUIRED"


class PromotionStatus(StrEnum):
    AUTO_PROMOTE = "AUTO_PROMOTE"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"


class UnitOfMeasure(StrEnum):
    CASE = "CASE"
    EACH = "EACH"
    PALLET = "PALLET"


class InventorySource(StrEnum):
    SIMULATION_MODEL = "SIMULATION_MODEL"
    WAREHOUSE_SNAPSHOT = "WAREHOUSE_SNAPSHOT"


class InboundDeliveryStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    RECEIVED = "RECEIVED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class InventoryPosition(StrictModel):
    sku: str = Field(min_length=1)
    on_hand: NonNegativeInt
    reserved: NonNegativeInt = 0
    quarantined: NonNegativeInt = 0
    unit_of_measure: UnitOfMeasure
    source: InventorySource

    @property
    def available_to_promise(self) -> int:
        return self.on_hand - self.reserved - self.quarantined

    @model_validator(mode="after")
    def available_inventory_must_not_be_negative(self) -> InventoryPosition:
        if self.available_to_promise < 0:
            raise ValueError(
                f"SKU {self.sku} has negative available-to-promise inventory: "
                f"{self.available_to_promise}"
            )
        return self


class SimulationInitializedPayload(StrictModel):
    warehouse_id: str = Field(min_length=1)
    inventory: tuple[InventoryPosition, ...] = Field(min_length=1)

    @field_validator("inventory")
    @classmethod
    def inventory_skus_must_be_unique(
        cls, inventory: tuple[InventoryPosition, ...]
    ) -> tuple[InventoryPosition, ...]:
        skus = [item.sku for item in inventory]
        if len(skus) != len(set(skus)):
            raise ValueError("Inventory contains duplicate SKUs")
        if any(item.source is not InventorySource.SIMULATION_MODEL for item in inventory):
            raise ValueError("Initialization inventory must come from SIMULATION_MODEL")
        return inventory


class InboundDeliveryExpectedPayload(StrictModel):
    delivery_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    expected_quantity: PositiveInt
    unit_of_measure: UnitOfMeasure
    expected_at: AwareDatetime
    status: Literal[InboundDeliveryStatus.SCHEDULED]
    source: Literal["carrier_schedule"]


class OrderReceivedPayload(StrictModel):
    order_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    quantity: PositiveInt
    unit_of_measure: UnitOfMeasure


class StockAdjustedPayload(StrictModel):
    sku: str = Field(min_length=1)
    delta_on_hand: int
    unit_of_measure: UnitOfMeasure
    reason: str = Field(min_length=1)

    @field_validator("delta_on_hand")
    @classmethod
    def adjustment_must_change_inventory(cls, delta: int) -> int:
        if delta == 0:
            raise ValueError("A stock adjustment cannot have a zero delta")
        return delta


class FulfillmentDecidedPayload(StrictModel):
    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    action: DecisionAction
    policy_version: Literal["fulfilment-policy-v1"]


class ShipmentPlannedPayload(StrictModel):
    shipment_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    quantity: PositiveInt
    unit_of_measure: UnitOfMeasure


class BaseEvent(StrictModel):
    event_id: str = Field(min_length=1)
    simulation_id: str = Field(min_length=1)
    sequence_number: PositiveInt
    occurred_at: AwareDatetime
    received_at: AwareDatetime
    entity_id: str = Field(min_length=1)
    correlation_id: str | None = None
    causation_id: str | None = None
    schema_version: Literal[1] = 1

    @model_validator(mode="after")
    def receipt_cannot_precede_occurrence(self) -> BaseEvent:
        if self.received_at < self.occurred_at:
            raise ValueError("received_at cannot precede occurred_at")
        return self


class SimulationInitializedEvent(BaseEvent):
    event_type: Literal[EventType.SIMULATION_INITIALIZED]
    payload: SimulationInitializedPayload


class InboundDeliveryExpectedEvent(BaseEvent):
    event_type: Literal[EventType.INBOUND_DELIVERY_EXPECTED]
    payload: InboundDeliveryExpectedPayload


class OrderReceivedEvent(BaseEvent):
    event_type: Literal[EventType.ORDER_RECEIVED]
    payload: OrderReceivedPayload


class StockAdjustedEvent(BaseEvent):
    event_type: Literal[EventType.STOCK_ADJUSTED]
    payload: StockAdjustedPayload


class FulfillmentDecidedEvent(BaseEvent):
    event_type: Literal[EventType.FULFILLMENT_DECIDED]
    payload: FulfillmentDecidedPayload


class ShipmentPlannedEvent(BaseEvent):
    event_type: Literal[EventType.SHIPMENT_PLANNED]
    payload: ShipmentPlannedPayload


SimulationEvent = Annotated[
    SimulationInitializedEvent
    | InboundDeliveryExpectedEvent
    | OrderReceivedEvent
    | StockAdjustedEvent
    | FulfillmentDecidedEvent
    | ShipmentPlannedEvent,
    Field(discriminator="event_type"),
]


class EventBatch(StrictModel):
    events: tuple[SimulationEvent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_stream_semantics(self) -> EventBatch:
        events = self.events
        simulation_ids = {event.simulation_id for event in events}
        if len(simulation_ids) != 1:
            raise ValueError("An event batch must contain exactly one simulation_id")

        event_ids = [event.event_id for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Event IDs must be unique within a batch")

        expected_sequences = list(range(1, len(events) + 1))
        actual_sequences = [event.sequence_number for event in events]
        if actual_sequences != expected_sequences:
            raise ValueError(
                "Events must be supplied in contiguous sequence order beginning at 1"
            )

        occurred_times = [event.occurred_at for event in events]
        if occurred_times != sorted(occurred_times):
            raise ValueError("Event occurred_at values must not move backwards")

        if not isinstance(events[0], SimulationInitializedEvent):
            raise ValueError("The first event must be SIMULATION_INITIALIZED")
        if sum(isinstance(event, SimulationInitializedEvent) for event in events) != 1:
            raise ValueError("A batch must contain exactly one SIMULATION_INITIALIZED event")

        initialization = events[0]
        if initialization.entity_id != initialization.payload.warehouse_id:
            raise ValueError("Initialization entity_id must equal its warehouse_id")

        known_skus = {item.sku for item in initialization.payload.inventory}
        known_units = {
            item.sku: item.unit_of_measure for item in initialization.payload.inventory
        }
        inbound_deliveries: dict[str, InboundDeliveryExpectedEvent] = {}
        orders: dict[str, OrderReceivedEvent] = {}
        decisions: dict[str, FulfillmentDecidedEvent] = {}

        for event in events[1:]:
            if isinstance(event, InboundDeliveryExpectedEvent):
                payload = event.payload
                if payload.sku not in known_skus:
                    raise ValueError(
                        f"Inbound delivery {payload.delivery_id} references unknown SKU"
                    )
                if payload.unit_of_measure is not known_units[payload.sku]:
                    raise ValueError(
                        f"Inbound delivery {payload.delivery_id} uses a different unit "
                        "from its SKU"
                    )
                if payload.delivery_id in inbound_deliveries:
                    raise ValueError(
                        f"Inbound delivery {payload.delivery_id} was expected more than once"
                    )
                if (
                    event.entity_id != payload.delivery_id
                    or event.correlation_id != payload.delivery_id
                ):
                    raise ValueError(
                        f"Inbound delivery {payload.delivery_id} must use its delivery_id "
                        "as entity_id and correlation_id"
                    )
                if payload.expected_at < event.occurred_at:
                    raise ValueError("An expected delivery cannot be scheduled in the past")
                inbound_deliveries[payload.delivery_id] = event

            elif isinstance(event, StockAdjustedEvent):
                if event.payload.sku not in known_skus:
                    raise ValueError(
                        f"Stock adjustment references unknown SKU {event.payload.sku}"
                    )
                if event.payload.unit_of_measure is not known_units[event.payload.sku]:
                    raise ValueError("Stock adjustment uses a different unit from its SKU")
                if event.entity_id != event.payload.sku:
                    raise ValueError("Stock adjustment entity_id must equal its SKU")

            elif isinstance(event, OrderReceivedEvent):
                order_id = event.payload.order_id
                if event.payload.sku not in known_skus:
                    raise ValueError(f"Order {order_id} references unknown SKU")
                if event.payload.unit_of_measure is not known_units[event.payload.sku]:
                    raise ValueError(f"Order {order_id} uses a different unit from its SKU")
                if order_id in orders:
                    raise ValueError(f"Order {order_id} was received more than once")
                if event.entity_id != order_id or event.correlation_id != order_id:
                    raise ValueError(
                        f"Order {order_id} must use its order_id as entity_id and correlation_id"
                    )
                orders[order_id] = event

            elif isinstance(event, FulfillmentDecidedEvent):
                order_id = event.payload.order_id
                order = orders.get(order_id)
                if order is None:
                    raise ValueError(f"Decision references unseen order {order_id}")
                if event.payload.decision_id in decisions:
                    raise ValueError(
                        f"Decision {event.payload.decision_id} was emitted more than once"
                    )
                if event.entity_id != event.payload.decision_id:
                    raise ValueError("Decision entity_id must equal its decision_id")
                if event.correlation_id != order_id or event.causation_id != order.event_id:
                    raise ValueError(
                        f"Decision for {order_id} must correlate to the order and be caused by "
                        "its ORDER_RECEIVED event"
                    )
                decisions[event.payload.decision_id] = event

            elif isinstance(event, ShipmentPlannedEvent):
                payload = event.payload
                decision = decisions.get(payload.decision_id)
                order = orders.get(payload.order_id)
                if decision is None or order is None:
                    raise ValueError("Shipment references an unseen decision or order")
                if event.causation_id != decision.event_id:
                    raise ValueError(
                        f"Shipment {payload.shipment_id} must be caused by its decision event"
                    )
                if event.correlation_id != payload.order_id:
                    raise ValueError(
                        f"Shipment {payload.shipment_id} must correlate to its order"
                    )
                if event.entity_id != payload.shipment_id:
                    raise ValueError("Shipment entity_id must equal its shipment_id")
                if (
                    payload.order_id != decision.payload.order_id
                    or payload.sku != order.payload.sku
                    or payload.quantity != order.payload.quantity
                    or payload.unit_of_measure is not order.payload.unit_of_measure
                ):
                    raise ValueError(
                        f"Shipment {payload.shipment_id} does not match its order and decision"
                    )
                if decision.payload.action is not DecisionAction.DISPATCH:
                    raise ValueError("A shipment can only be planned for a DISPATCH decision")

        return self

    @property
    def simulation_id(self) -> str:
        return self.events[0].simulation_id

    @property
    def warehouse_id(self) -> str:
        initialization = self.events[0]
        assert isinstance(initialization, SimulationInitializedEvent)
        return initialization.payload.warehouse_id


class WarehouseInboundDelivery(StrictModel):
    delivery_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    expected_quantity: PositiveInt
    received_quantity: NonNegativeInt
    unit_of_measure: UnitOfMeasure
    expected_at: AwareDatetime
    status: InboundDeliveryStatus
    source: Literal["warehouse_management_system"]

    @model_validator(mode="after")
    def received_status_requires_received_stock(self) -> WarehouseInboundDelivery:
        if self.status is InboundDeliveryStatus.RECEIVED and self.received_quantity == 0:
            raise ValueError("A RECEIVED delivery must have a positive received_quantity")
        return self


class WarehouseSnapshot(StrictModel):
    snapshot_id: str = Field(min_length=1)
    simulation_id: str = Field(min_length=1)
    warehouse_id: str = Field(min_length=1)
    as_of: AwareDatetime
    received_at: AwareDatetime
    inventory: tuple[InventoryPosition, ...] = Field(min_length=1)
    inbound_deliveries: tuple[WarehouseInboundDelivery, ...] = ()
    schema_version: Literal[1] = 1

    @field_validator("inventory")
    @classmethod
    def snapshot_skus_must_be_unique(
        cls, inventory: tuple[InventoryPosition, ...]
    ) -> tuple[InventoryPosition, ...]:
        skus = [item.sku for item in inventory]
        if len(skus) != len(set(skus)):
            raise ValueError("Warehouse snapshot contains duplicate SKUs")
        if any(item.source is not InventorySource.WAREHOUSE_SNAPSHOT for item in inventory):
            raise ValueError("Snapshot inventory must come from WAREHOUSE_SNAPSHOT")
        return inventory

    @field_validator("inbound_deliveries")
    @classmethod
    def inbound_delivery_ids_must_be_unique(
        cls, deliveries: tuple[WarehouseInboundDelivery, ...]
    ) -> tuple[WarehouseInboundDelivery, ...]:
        delivery_ids = [delivery.delivery_id for delivery in deliveries]
        if len(delivery_ids) != len(set(delivery_ids)):
            raise ValueError("Warehouse snapshot contains duplicate delivery IDs")
        return deliveries

    @model_validator(mode="after")
    def snapshot_cannot_arrive_before_its_effective_time(self) -> WarehouseSnapshot:
        if self.received_at < self.as_of:
            raise ValueError("Snapshot received_at cannot precede as_of")
        return self


class ScenarioBundle(StrictModel):
    event_batch: EventBatch
    warehouse_snapshot: WarehouseSnapshot

    @model_validator(mode="after")
    def validate_cross_source_contract(self) -> ScenarioBundle:
        batch = self.event_batch
        snapshot = self.warehouse_snapshot

        if snapshot.simulation_id != batch.simulation_id:
            raise ValueError("Snapshot and events must have the same simulation_id")
        if snapshot.warehouse_id != batch.warehouse_id:
            raise ValueError("Snapshot and events must have the same warehouse_id")

        initialization = batch.events[0]
        if snapshot.as_of < initialization.occurred_at:
            raise ValueError("Snapshot as_of cannot predate simulation initialization")

        processed_before_arrival = sum(
            event.received_at < snapshot.received_at for event in batch.events
        )
        if processed_before_arrival < 2:
            raise ValueError("Snapshot must arrive after at least two simulation events")

        if len(batch.events) < 5:
            raise ValueError("The demonstration must contain at least five events")
        if len({event.event_type for event in batch.events}) < 3:
            raise ValueError("The demonstration must contain at least three event types")

        return self


class ExpectedDecisionOutcome(StrictModel):
    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    order_quantity: PositiveInt
    unit_of_measure: UnitOfMeasure
    original_action: DecisionAction
    corrected_action: DecisionAction
    original_base_available_to_promise: NonNegativeInt
    original_expected_inbound: NonNegativeInt
    original_decision_available_to_promise: NonNegativeInt
    corrected_available_to_promise: NonNegativeInt
    policy_consistent_at_time: bool
    assumption_event_ids: tuple[str, ...] = ()
    invalidated_assumption_event_ids: tuple[str, ...] = ()
    verdict: ReconciliationVerdict
    invalidated_output_event_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_expected_decision_math(self) -> ExpectedDecisionOutcome:
        projected = (
            self.original_base_available_to_promise + self.original_expected_inbound
        )
        if self.original_decision_available_to_promise != projected:
            raise ValueError(
                "Original decision availability must equal base availability plus "
                "expected inbound"
            )

        policy_action = (
            DecisionAction.DISPATCH
            if projected >= self.order_quantity
            else DecisionAction.BACKORDER
        )
        if self.policy_consistent_at_time != (self.original_action is policy_action):
            raise ValueError("policy_consistent_at_time does not match the original facts")

        corrected_policy_action = (
            DecisionAction.DISPATCH
            if self.corrected_available_to_promise >= self.order_quantity
            else DecisionAction.BACKORDER
        )
        if self.corrected_action is not corrected_policy_action:
            raise ValueError("corrected_action does not match corrected availability")

        invalidated_assumptions = set(self.invalidated_assumption_event_ids)
        if not invalidated_assumptions.issubset(self.assumption_event_ids):
            raise ValueError("Invalidated assumptions must be listed as original assumptions")

        if (
            self.verdict is ReconciliationVerdict.WOULD_CHANGE_REVIEW_REQUIRED
            and self.original_action is self.corrected_action
        ):
            raise ValueError("A WOULD_CHANGE verdict requires different actions")
        if (
            self.original_action is not self.corrected_action
            and not self.invalidated_output_event_ids
        ):
            raise ValueError("A changed decision must identify invalidated output events")
        return self


class ExpectedOutcomes(StrictModel):
    simulation_id: str = Field(min_length=1)
    decisions: tuple[ExpectedDecisionOutcome, ...] = Field(min_length=1)
    original_final_available_to_promise: dict[str, NonNegativeInt]
    candidate_final_available_to_promise: dict[str, NonNegativeInt]
    promotion_status: PromotionStatus

    @field_validator(
        "original_final_available_to_promise",
        "candidate_final_available_to_promise",
    )
    @classmethod
    def state_maps_must_not_be_empty(
        cls, state: dict[str, NonNegativeInt]
    ) -> dict[str, NonNegativeInt]:
        if not state:
            raise ValueError("Expected final state cannot be empty")
        return state

    @model_validator(mode="after")
    def expected_outcomes_must_be_complete_and_unique(self) -> ExpectedOutcomes:
        decision_ids = [decision.decision_id for decision in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("Expected outcomes contain duplicate decision IDs")
        if set(self.original_final_available_to_promise) != set(
            self.candidate_final_available_to_promise
        ):
            raise ValueError("Original and candidate final states must cover the same SKUs")
        return self


def count_events_received_before(
    event_batch: EventBatch, timestamp: datetime
) -> int:
    """Return the number of events received strictly before a timestamp."""

    return sum(event.received_at < timestamp for event in event_batch.events)
