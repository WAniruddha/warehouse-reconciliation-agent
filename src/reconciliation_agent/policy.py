"""Deterministic fulfilment policy used by both simulation branches."""

from __future__ import annotations

from dataclasses import dataclass

from .models import DecisionAction

POLICY_VERSION = "fulfilment-policy-v1"


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Result of applying the fulfilment policy to one order."""

    action: DecisionAction
    available_quantity: int
    requested_quantity: int

    @property
    def comparison(self) -> str:
        operator = ">=" if self.available_quantity >= self.requested_quantity else "<"
        return f"{self.available_quantity} {operator} {self.requested_quantity}"


def evaluate_fulfilment(
    available_quantity: int, requested_quantity: int
) -> PolicyEvaluation:
    """Dispatch only when projected availability covers the complete order."""

    action = (
        DecisionAction.DISPATCH
        if available_quantity >= requested_quantity
        else DecisionAction.BACKORDER
    )
    return PolicyEvaluation(
        action=action,
        available_quantity=available_quantity,
        requested_quantity=requested_quantity,
    )
