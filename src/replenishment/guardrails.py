"""Deterministic checks that run after a quantity is chosen.

The forecast and the planner can propose a number. These checks decide
whether that number is allowed to become a pick line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    rule: str
    reason: str


def evaluate_guardrails(
    *,
    quantity: int,
    on_shelf: int | None,
    shelf_capacity: int | None,
    backroom: int | None,
    reserved_units: int | None,
    forecast_units: float | None,
    updated_at: datetime | None,
    as_of: datetime,
    stale_after: timedelta,
) -> GuardrailResult:
    """Return the first failing rule, or a pass.

    A quantity of zero is not a pull. Missing or stale inputs fail closed
    even when the quantity is zero, because the decision itself is untrusted.
    """

    if shelf_capacity is None:
        return GuardrailResult(False, "fail_closed", "missing shelf capacity")
    if on_shelf is None:
        return GuardrailResult(False, "fail_closed", "missing on-shelf count")
    if backroom is None:
        return GuardrailResult(False, "fail_closed", "missing backroom count")
    if reserved_units is None:
        return GuardrailResult(False, "fail_closed", "missing reservation count")
    if forecast_units is None or not math.isfinite(forecast_units):
        return GuardrailResult(False, "fail_closed", "missing forecast")
    if updated_at is None:
        return GuardrailResult(False, "fail_closed", "inventory timestamp missing")
    if as_of - updated_at > stale_after:
        return GuardrailResult(False, "fail_closed", "inventory data is stale")
    if min(on_shelf, backroom, reserved_units, shelf_capacity) < 0:
        return GuardrailResult(False, "fail_closed", "inventory counts must be non-negative")
    if on_shelf > shelf_capacity:
        return GuardrailResult(False, "fail_closed", "on-shelf count is above shelf capacity")
    if reserved_units > backroom:
        return GuardrailResult(False, "fail_closed", "reserved units exceed backroom stock")
    if quantity < 0:
        return GuardrailResult(False, "negative_quantity", "quantity must be zero or positive")
    if quantity == 0:
        return GuardrailResult(True, "", "no units requested")
    if on_shelf + quantity > shelf_capacity:
        return GuardrailResult(
            False,
            "shelf_capacity",
            "on-shelf units plus the order exceed shelf capacity",
        )
    unreserved = backroom - reserved_units
    if quantity > unreserved:
        if reserved_units > 0:
            return GuardrailResult(
                False,
                "reserved_stock",
                "order would pull units reserved for another store",
            )
        return GuardrailResult(False, "insufficient_stock", "order exceeds unreserved backroom stock")
    return GuardrailResult(True, "", "within shelf capacity and unreserved stock")
