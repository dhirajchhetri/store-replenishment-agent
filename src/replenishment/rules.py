"""Reorder quantities, substitute selection, and pick-list priority.

Safety stock uses a policy z-score times the training residual, scaled by
the share of the selling day still ahead. It is a rule, not a fitted
service level from a live store.
"""

from __future__ import annotations

import math
from datetime import timedelta

from replenishment.catalog import SKU_BY_ID, SKUS
from replenishment.config import Settings
from replenishment.domain import BlockedLine, InventoryPosition, PickLine, SkuSpec
from replenishment.features import hours_until_close
from replenishment.guardrails import evaluate_guardrails


def safety_stock(residual_std: float, hours_left: float, open_span: int, z: float) -> float:
    if hours_left <= 0 or residual_std <= 0 or open_span <= 0:
        return 0.0
    return z * residual_std * math.sqrt(hours_left / open_span)


def cover_hours(on_shelf: int, forecast_units: float, hours_left: float) -> float:
    if forecast_units <= 1e-6 or hours_left <= 0:
        return 999.99
    rate = forecast_units / hours_left
    return round(min(999.99, on_shelf / rate), 2)


def _need(forecast_units: float, on_shelf: int, safety: float) -> int:
    gap = forecast_units + safety - on_shelf
    if gap <= 1e-6:
        return 0
    return math.ceil(gap - 1e-9)


def substitute_candidates(sku: str, limit: int) -> list[SkuSpec]:
    spec = SKU_BY_ID[sku]
    others = [item for item in SKUS if item.category == spec.category and item.sku != sku]
    others.sort(key=lambda item: (abs(item.pack_size - spec.pack_size), item.sku))
    return others[:limit]


def explain_plan(store_id: str, lines: list[PickLine], blocked: list[BlockedLine]) -> str:
    parts = [f"Store {store_id}."]
    if not lines:
        parts.append("No pick lines passed the guardrails.")
    else:
        parts.append("Lines are ordered by stockout risk, then by hours of cover.")
        for line in lines:
            destination = f"{line.quantity} units of {line.sku} for shelf {line.shelf_id}"
            if line.substituted_for:
                destination += f" as a substitute for {line.substituted_for}"
            parts.append(
                f"Priority {line.priority}: {destination}. "
                f"Forecast {line.forecast_units:.2f}, on shelf {line.on_shelf}, "
                f"hours of cover {line.hours_of_cover:.2f}. {line.reason}"
            )
    for block in blocked:
        parts.append(
            f"Blocked {block.sku} on {block.shelf_id} by {block.rule} "
            f"after {block.attempts} substitute attempts. {block.reason}"
        )
    return " ".join(parts)


class _Draft:
    def __init__(self, position: InventoryPosition, forecast_units: float, settings: Settings, hours_left: float):
        self.position = position
        self.forecast_units = max(0.0, float(forecast_units))
        self.settings = settings
        self.hours_left = hours_left
        open_span = settings.close_hour - settings.open_hour
        self.safety = safety_stock(0.0, hours_left, open_span, settings.safety_z)
        self.need = 0
        self.action = "skip"
        self.rule = ""
        self.reason = ""
        self.quantity = 0

    def bind_residual(self, residual_std: float) -> None:
        open_span = self.settings.close_hour - self.settings.open_hour
        self.safety = safety_stock(residual_std, self.hours_left, open_span, self.settings.safety_z)
        self.need = _need(self.forecast_units, self.position.on_shelf, self.safety)


def _check(position: InventoryPosition, quantity: int, forecast: float, as_of, settings: Settings):
    return evaluate_guardrails(
        quantity=quantity,
        on_shelf=position.on_shelf,
        shelf_capacity=position.shelf_capacity,
        backroom=position.backroom,
        reserved_units=position.reserved_units,
        forecast_units=forecast,
        updated_at=position.updated_at,
        as_of=as_of,
        stale_after=timedelta(minutes=settings.stale_after_minutes),
    )


def _classify_zero(position: InventoryPosition) -> tuple[str, str]:
    unreserved = position.backroom - position.reserved_units
    room = position.shelf_capacity - position.on_shelf
    if unreserved <= 0 and position.reserved_units > 0:
        return "reserved_stock", "backroom stock is reserved for another store"
    if unreserved <= 0:
        return "insufficient_stock", "no unreserved backroom stock"
    if room <= 0:
        return "shelf_capacity", "shelf has no open capacity"
    return "fail_closed", "quantity fell to zero without a capacity or stock cap"


def plan_positions(
    positions: list[InventoryPosition],
    forecasts: dict[tuple[str, str], float],
    *,
    settings: Settings,
    as_of,
    residual_std: float,
) -> tuple[list[PickLine], list[BlockedLine]]:
    """Turn forecasts and on-hand counts into prioritised lines and blocks."""

    hours_left = hours_until_close(as_of, settings.close_hour)
    by_sku = {position.sku: position for position in positions}
    drafts: list[_Draft] = []
    for position in sorted(positions, key=lambda item: (item.shelf_id, item.sku)):
        forecast = forecasts.get((position.shelf_id, position.sku))
        draft = _Draft(position, forecast or 0.0, settings, hours_left)
        draft.bind_residual(residual_std)
        if forecast is None or not math.isfinite(forecast):
            draft.action = "blocked"
            draft.rule = "fail_closed"
            draft.reason = "missing forecast"
            drafts.append(draft)
            continue
        guard = _check(position, 0, draft.forecast_units, as_of, settings)
        if not guard.passed:
            draft.action = "blocked"
            draft.rule = guard.rule
            draft.reason = guard.reason
            drafts.append(draft)
            continue
        if hours_left <= 0 or draft.need <= 0:
            draft.action = "skip"
            draft.reason = "on shelf covers the reorder point" if hours_left > 0 else "store is closed"
            drafts.append(draft)
            continue
        room = position.shelf_capacity - position.on_shelf
        unreserved = position.backroom - position.reserved_units
        quantity = min(draft.need, max(room, 0), max(unreserved, 0))
        if quantity <= 0:
            draft.action = "blocked"
            draft.rule, draft.reason = _classify_zero(position)
            drafts.append(draft)
            continue
        confirmed = _check(position, quantity, draft.forecast_units, as_of, settings)
        if not confirmed.passed:
            draft.action = "blocked"
            draft.rule = confirmed.rule
            draft.reason = confirmed.reason
            drafts.append(draft)
            continue
        draft.action = "order"
        draft.quantity = quantity
        reorder = draft.forecast_units + draft.safety
        draft.reason = (
            f"Forecast {draft.forecast_units:.2f} plus safety stock {draft.safety:.2f} "
            f"sets a reorder point of {reorder:.2f}, against {position.on_shelf} on the shelf."
        )
        if quantity < draft.need:
            draft.reason += f" Quantity capped at {quantity}."
        drafts.append(draft)

    remaining_back = {position.sku: position.backroom - position.reserved_units for position in positions}
    remaining_room = {position.sku: position.shelf_capacity - position.on_shelf for position in positions}
    urgency = []
    for draft in drafts:
        cover = cover_hours(draft.position.on_shelf, draft.forecast_units, hours_left)
        stockout = draft.position.on_shelf < draft.forecast_units and draft.need > 0
        urgency.append((not stockout, cover, -draft.forecast_units, draft.position.shelf_id, draft.position.sku))
    ordered = [draft for _, draft in sorted(zip(urgency, drafts, strict=True), key=lambda item: item[0])]

    ranked: list[tuple[tuple, PickLine]] = []
    blocked: list[BlockedLine] = []
    for draft in ordered:
        position = draft.position
        cover = cover_hours(position.on_shelf, draft.forecast_units, hours_left)
        at_risk = position.on_shelf < draft.forecast_units and draft.need > 0
        rank = (
            not at_risk,
            cover,
            -draft.forecast_units,
            position.shelf_id,
            position.sku,
        )
        if draft.action == "skip":
            continue
        if draft.action == "order":
            quantity = min(draft.quantity, remaining_back[position.sku], remaining_room[position.sku])
            if quantity <= 0:
                draft.action = "blocked"
                draft.rule, draft.reason = _classify_zero(position)
            else:
                remaining_back[position.sku] -= quantity
                remaining_room[position.sku] -= quantity
                ranked.append(
                    (
                        rank,
                        PickLine(
                            shelf_id=position.shelf_id,
                            sku=position.sku,
                            quantity=quantity,
                            priority=0,
                            reason=draft.reason,
                            forecast_units=round(draft.forecast_units, 2),
                            on_shelf=position.on_shelf,
                            hours_of_cover=cover,
                            substituted_for="",
                        ),
                    )
                )
                continue
        if draft.rule == "fail_closed":
            blocked.append(
                BlockedLine(
                    shelf_id=position.shelf_id,
                    sku=position.sku,
                    rule=draft.rule,
                    reason=draft.reason,
                    attempts=0,
                    escalated=True,
                )
            )
            continue
        placed = False
        attempts = 0
        for candidate in substitute_candidates(position.sku, settings.max_substitute_attempts):
            attempts += 1
            dest = by_sku.get(candidate.sku)
            if dest is None:
                continue
            quantity = min(draft.need, remaining_back[candidate.sku], remaining_room[candidate.sku])
            if quantity <= 0:
                continue
            confirmed = _check(dest, quantity, draft.forecast_units, as_of, settings)
            if not confirmed.passed:
                continue
            remaining_back[candidate.sku] -= quantity
            remaining_room[candidate.sku] -= quantity
            ranked.append(
                (
                    rank,
                    PickLine(
                        shelf_id=dest.shelf_id,
                        sku=dest.sku,
                        quantity=quantity,
                        priority=0,
                        reason=(
                            f"Substitute for {position.sku}, blocked by {draft.rule}. "
                            f"Original shelf has {position.on_shelf} on hand and needs {draft.need}."
                        ),
                        forecast_units=round(draft.forecast_units, 2),
                        on_shelf=dest.on_shelf,
                        hours_of_cover=cover,
                        substituted_for=position.sku,
                    ),
                )
            )
            placed = True
            break
        if not placed:
            blocked.append(
                BlockedLine(
                    shelf_id=position.shelf_id,
                    sku=position.sku,
                    rule=draft.rule,
                    reason=f"{draft.reason} No substitute could cover the gap.",
                    attempts=attempts,
                    escalated=True,
                )
            )

    ranked.sort(key=lambda item: item[0])
    lines = [line for _, line in ranked]
    for index, line in enumerate(lines, start=1):
        line.priority = index
    blocked.sort(key=lambda block: (block.shelf_id, block.sku))
    return lines, blocked
