import math

from replenishment.catalog import AS_OF
from replenishment.config import Settings
from replenishment.rules import cover_hours, plan_positions, safety_stock


def _forecasts(positions, value=8.0):
    return {(position.shelf_id, position.sku): value for position in positions}


def test_safety_stock_scales_with_the_hours_left():
    assert safety_stock(1.0, 5, 13, 1.65) == pytest_approx(1.65 * math.sqrt(5 / 13))
    assert safety_stock(1.0, 0, 13, 1.65) == 0


def pytest_approx(value):
    import pytest

    return pytest.approx(value)


def test_demo_snapshot_orders_milk_substitutes_water_and_escalates_chips(demo_repo, settings: Settings):
    positions = demo_repo.list_inventory("store-100")
    lines, blocked = plan_positions(
        positions,
        _forecasts(positions),
        settings=settings,
        as_of=AS_OF,
        residual_std=1.0,
    )
    safety = safety_stock(1.0, 5, 13, settings.safety_z)
    milk_need = math.ceil(8 + safety - 1)
    chips_need = math.ceil(8 + safety - 0)
    assert milk_need == 9
    assert chips_need == 10

    by_sku = {(line.substituted_for, line.sku): line for line in lines}
    milk = by_sku[("", "milk-1l")]
    assert milk.quantity == 9
    assert milk.priority == 1
    assert milk.hours_of_cover == cover_hours(1, 8, 5)

    water = by_sku[("water-1500", "water-500")]
    assert water.quantity == 9
    assert water.shelf_id == "B1"
    assert water.priority == 2

    assert [block.sku for block in blocked] == ["chips-large"]
    assert blocked[0].rule == "reserved_stock"
    assert blocked[0].attempts == 1
    assert blocked[0].escalated
    assert "milk-2l" not in {line.sku for line in lines if not line.substituted_for}


def test_cap_uses_unreserved_backroom(demo_repo, settings):
    position = demo_repo.get_inventory("store-100", "milk-1l")
    position.backroom = 4
    lines, blocked = plan_positions(
        [position],
        {(position.shelf_id, position.sku): 8.0},
        settings=settings,
        as_of=AS_OF,
        residual_std=1.0,
    )
    assert blocked == []
    assert lines[0].quantity == 4
    assert "capped" in lines[0].reason.lower() or "4" in lines[0].reason


def test_shared_stock_does_not_double_allocate(demo_repo, settings):
    primary = demo_repo.get_inventory("store-100", "water-500")
    primary.on_shelf = 1
    primary.backroom = 5
    primary.reserved_units = 0
    blocked_sku = demo_repo.get_inventory("store-100", "water-1500")
    lines, blocked = plan_positions(
        [primary, blocked_sku],
        {
            (primary.shelf_id, primary.sku): 8.0,
            (blocked_sku.shelf_id, blocked_sku.sku): 8.0,
        },
        settings=settings,
        as_of=AS_OF,
        residual_std=1.0,
    )
    water_lines = [line for line in lines if line.sku == "water-500"]
    assert sum(line.quantity for line in water_lines) == 5
