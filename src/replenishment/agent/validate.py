"""Check a model plan against a fresh rule-engine plan.

The comparison is on shelf, SKU, quantity, priority, and blocked rules.
Explanation wording is not compared, so a Bedrock phrasing can differ.
A mismatch writes nothing.
"""

from __future__ import annotations

from replenishment.domain import PickListPlan
from replenishment.errors import PlanValidationError
from replenishment.workflow import build_plan


def validate_plan(plan: PickListPlan, repo, forecaster, settings) -> None:
    if not plan.explanation or len(plan.explanation.strip()) < 20:
        raise PlanValidationError("explanation is missing")
    _as_of, _rows, lines, blocked = build_plan(repo, forecaster, settings, plan.store_id)
    if [(line.shelf_id, line.sku, line.quantity, line.priority, line.substituted_for) for line in plan.lines] != [
        (line.shelf_id, line.sku, line.quantity, line.priority, line.substituted_for) for line in lines
    ]:
        raise PlanValidationError("pick lines do not match the rule engine")
    if [(block.shelf_id, block.sku, block.rule, block.escalated, block.attempts) for block in plan.blocked] != [
        (block.shelf_id, block.sku, block.rule, block.escalated, block.attempts) for block in blocked
    ]:
        raise PlanValidationError("blocked lines do not match the rule engine")
    for actual, expected in zip(plan.lines, lines, strict=True):
        if abs(actual.forecast_units - expected.forecast_units) > 0.05:
            raise PlanValidationError(f"forecast for {actual.sku} was changed")
        if abs(actual.hours_of_cover - expected.hours_of_cover) > 0.05:
            raise PlanValidationError(f"hours of cover for {actual.sku} was changed")
        if actual.on_shelf != expected.on_shelf:
            raise PlanValidationError(f"on-shelf count for {actual.sku} was changed")
