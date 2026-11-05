from datetime import UTC, datetime, timedelta

import pytest

from replenishment.guardrails import evaluate_guardrails

UTC = UTC
AS_OF = datetime(2026, 11, 2, 16, tzinfo=UTC)
STALE = timedelta(hours=6)


def check(**overrides):
    values = dict(
        quantity=2,
        on_shelf=10,
        shelf_capacity=12,
        backroom=5,
        reserved_units=0,
        forecast_units=3.0,
        updated_at=AS_OF,
        as_of=AS_OF,
        stale_after=STALE,
    )
    values.update(overrides)
    return evaluate_guardrails(**values)


def test_capacity_boundary():
    assert check(quantity=2).passed
    assert check(quantity=1).passed
    blocked = check(quantity=3)
    assert not blocked.passed
    assert blocked.rule == "shelf_capacity"


def test_reserved_stock_blocks_partial_pulls():
    blocked = check(backroom=5, reserved_units=5, quantity=1)
    assert blocked.rule == "reserved_stock"
    assert check(backroom=5, reserved_units=4, quantity=1).passed
    assert check(backroom=5, reserved_units=4, quantity=2).rule == "reserved_stock"


@pytest.mark.parametrize(
    ("overrides", "rule"),
    [
        ({"shelf_capacity": None}, "fail_closed"),
        ({"on_shelf": None}, "fail_closed"),
        ({"backroom": None}, "fail_closed"),
        ({"reserved_units": None}, "fail_closed"),
        ({"forecast_units": None}, "fail_closed"),
        ({"updated_at": None}, "fail_closed"),
        ({"updated_at": AS_OF - STALE - timedelta(seconds=1)}, "fail_closed"),
        ({"quantity": -1}, "negative_quantity"),
        ({"on_shelf": 13, "shelf_capacity": 12, "quantity": 0}, "fail_closed"),
        ({"reserved_units": 6, "backroom": 5}, "fail_closed"),
    ],
)
def test_fail_closed(overrides, rule):
    assert check(**overrides).rule == rule


def test_exact_staleness_window_is_still_fresh():
    assert check(updated_at=AS_OF - STALE).passed
