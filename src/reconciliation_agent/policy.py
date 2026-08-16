"""Deterministic fulfilment policy used by both simulation branches."""

from __future__ import annotations

from .models import DecisionAction


def evaluate_fulfilment(
    available_quantity: int, requested_quantity: int
) -> DecisionAction:
    """Dispatch only when projected availability covers the complete order."""

    return (
        DecisionAction.DISPATCH
        if available_quantity >= requested_quantity
        else DecisionAction.BACKORDER
    )
