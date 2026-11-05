"""Tools the planner is allowed to call.

Quantities come from ``draft_pick_list``, which calls the rule engine.
The other tools are evidence: velocity counters, inventory, and the forecast.
"""

from __future__ import annotations

from strands import tool

from replenishment.agent.context import same_store
from replenishment.rules import explain_plan
from replenishment.workflow import build_plan, velocity_report


@tool
def read_shelf_velocity(store_id: str) -> dict:
    """Read units sold from the velocity counters for every shelf in the store.

    This does not scan the raw event log. Counters are updated when a sale is ingested.
    """

    ctx = same_store(store_id)
    as_of = _as_of(ctx)
    return {"store_id": store_id, "as_of": as_of.isoformat(), "positions": velocity_report(ctx.repo, store_id, as_of)}


@tool
def check_inventory(store_id: str) -> dict:
    """Read on-shelf counts, backroom stock, shelf capacity, and reservations."""

    ctx = same_store(store_id)
    positions = []
    for position in ctx.repo.list_inventory(store_id):
        positions.append(
            {
                "shelf_id": position.shelf_id,
                "sku": position.sku,
                "on_shelf": position.on_shelf,
                "backroom": position.backroom,
                "shelf_capacity": position.shelf_capacity,
                "reserved_units": position.reserved_units,
                "reserved_for_store": position.reserved_for_store or "",
                "updated_at": position.updated_at.isoformat(),
            }
        )
    return {"store_id": store_id, "positions": positions}


@tool
def predict_burn_rate(store_id: str) -> dict:
    """Forecast units sold from now until close for each shelf position."""

    ctx = same_store(store_id)
    as_of, rows, _lines, _blocked = build_plan(ctx.repo, ctx.forecaster, ctx.settings, store_id)
    return {
        "store_id": store_id,
        "as_of": as_of.isoformat(),
        "residual_std": ctx.forecaster.residual_std,
        "positions": rows,
    }


@tool
def draft_pick_list(store_id: str) -> dict:
    """Build the pick list with the rule engine.

    Copy quantities, priorities, and blocked rules from this result.
    Do not change a quantity to make the list look tidier.
    """

    ctx = same_store(store_id)
    as_of, _rows, lines, blocked = build_plan(ctx.repo, ctx.forecaster, ctx.settings, store_id)
    explanation = explain_plan(store_id, lines, blocked)
    return {
        "store_id": store_id,
        "run_id": ctx.run_id,
        "as_of": as_of.isoformat(),
        "lines": [line.model_dump(mode="json") for line in lines],
        "blocked": [block.model_dump(mode="json") for block in blocked],
        "explanation": explanation,
    }


def _as_of(ctx):
    from replenishment.workflow import resolve_as_of

    return resolve_as_of(ctx.repo)


TOOLS = [read_shelf_velocity, check_inventory, predict_burn_rate, draft_pick_list]
