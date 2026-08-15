"""Structured reasoning traces and reconciliation report contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, NonNegativeInt, model_validator

from .models import (
    DecisionAction,
    PromotionStatus,
    ReconciliationVerdict,
    StrictModel,
    UnitOfMeasure,
)


class DecisionBranch(StrEnum):
    ORIGINAL = "ORIGINAL"
    CORRECTED = "CORRECTED"


class DecisionTrace(StrictModel):
    """Facts, assumptions, policy result, and explanation captured at decision time."""

    simulation_id: str = Field(min_length=1)
    branch: DecisionBranch
    decision_event_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    occurred_at: AwareDatetime
    sku: str = Field(min_length=1)
    order_quantity: int = Field(gt=0)
    unit_of_measure: UnitOfMeasure
    base_available_to_promise: int
    expected_inbound: NonNegativeInt
    decision_available_to_promise: int
    action: DecisionAction
    policy_action: DecisionAction
    policy_version: str = Field(min_length=1)
    policy_consistent: bool
    evidence_ids: tuple[str, ...] = ()
    assumption_event_ids: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def availability_and_policy_flags_must_match(self) -> DecisionTrace:
        if self.decision_available_to_promise != (
            self.base_available_to_promise + self.expected_inbound
        ):
            raise ValueError("Decision availability must equal base plus expected inbound")
        if self.policy_consistent != (self.action is self.policy_action):
            raise ValueError("policy_consistent does not match action and policy_action")
        return self


class InventoryDivergence(StrictModel):
    """Simulation-versus-warehouse comparison at the snapshot boundary."""

    sku: str = Field(min_length=1)
    unit_of_measure: UnitOfMeasure | None
    simulation_base_available_to_promise: int | None
    simulation_expected_inbound: NonNegativeInt | None
    simulation_projected_available_to_promise: int | None
    warehouse_available_to_promise: int | None
    warehouse_minus_simulation: int | None
    diverged: bool
    explanation: str = Field(min_length=1)


class ReconciliationFinding(StrictModel):
    """Audit result for one immutable historical decision."""

    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    original_trace: DecisionTrace
    corrected_trace: DecisionTrace | None
    verdict: ReconciliationVerdict
    would_change: bool | None
    requires_human_review: bool
    invalidated_assumption_event_ids: tuple[str, ...] = ()
    unresolved_assumption_event_ids: tuple[str, ...] = ()
    invalidated_output_event_ids: tuple[str, ...] = ()
    reasoning_gap: str = Field(min_length=1)
    audit_explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def review_flag_and_change_flag_must_match(self) -> ReconciliationFinding:
        review_verdicts = {
            ReconciliationVerdict.WOULD_CHANGE_REVIEW_REQUIRED,
            ReconciliationVerdict.INDETERMINATE_REVIEW_REQUIRED,
            ReconciliationVerdict.SOURCE_INCONSISTENT_REVIEW_REQUIRED,
        }
        if self.requires_human_review != (self.verdict in review_verdicts):
            raise ValueError("requires_human_review does not match the verdict")
        if self.corrected_trace is None:
            if self.would_change is not None:
                raise ValueError("would_change must be unknown without a corrected trace")
        elif self.would_change != (
            self.original_trace.action is not self.corrected_trace.action
        ):
            raise ValueError("would_change does not match the two decision actions")
        return self


class ReconciliationReport(StrictModel):
    """Self-contained report that a reviewer can audit without rerunning the agent."""

    report_id: str = Field(min_length=1)
    simulation_id: str = Field(min_length=1)
    warehouse_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_as_of: AwareDatetime
    snapshot_received_at: AwareDatetime
    generated_at: AwareDatetime
    events_processed_before_snapshot: NonNegativeInt
    decisions_audited: NonNegativeInt
    divergence_detected: bool
    inventory_divergences: tuple[InventoryDivergence, ...]
    findings: tuple[ReconciliationFinding, ...]
    original_final_available_to_promise: dict[str, int]
    candidate_final_available_to_promise: dict[str, int]
    verdict_counts: dict[str, NonNegativeInt]
    promotion_status: PromotionStatus
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def counts_and_findings_must_match(self) -> ReconciliationReport:
        if self.decisions_audited != len(self.findings):
            raise ValueError("decisions_audited must equal the number of findings")
        if self.divergence_detected != any(
            item.diverged for item in self.inventory_divergences
        ):
            raise ValueError("divergence_detected does not match inventory comparisons")
        return self
