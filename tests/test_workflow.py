from datetime import timedelta

import pytest

from replenishment.catalog import AS_OF
from replenishment.routing import HashRing
from replenishment.workflow import approve, run_ml
from tests.conftest import ConstantForecaster


def test_ml_run_waits_for_approval_and_publishes_once(demo_repo, settings):
    pick, trace = run_ml(demo_repo, ConstantForecaster(), settings, "store-100", "run-ml")
    assert trace == ["ScoreStore", "DecideStore", "GuardrailStore", "SavePendingPickList"]
    assert pick.status == "pending_approval"
    assert pick.agent_region == HashRing().owner("store-100")
    assert demo_repo.get_warehouse("store-100", "run-ml") is None

    published = approve(demo_repo, settings, "store-100", "run-ml", "approve")
    again = approve(demo_repo, settings, "store-100", "run-ml", "approve")
    assert published.status == "published"
    assert again.model_dump() == published.model_dump()
    assert demo_repo.get_warehouse("store-100", "run-ml") is not None


def test_reject_writes_no_warehouse_order(demo_repo, settings):
    run_ml(demo_repo, ConstantForecaster(), settings, "store-100", "run-no")
    rejected = approve(demo_repo, settings, "store-100", "run-no", "reject")
    assert rejected.status == "rejected"
    assert demo_repo.get_warehouse("store-100", "run-no") is None


def test_reduce_rechecks_guardrails(demo_repo, settings):
    pick, _trace = run_ml(demo_repo, ConstantForecaster(), settings, "store-100", "run-edit")
    milk = next(line for line in pick.lines if line.sku == "milk-1l")
    with pytest.raises(Exception):
        approve(
            demo_repo,
            settings,
            "store-100",
            "run-edit",
            "reduce",
            [{"priority": milk.priority, "quantity": milk.quantity + 5}],
        )
    reduced = approve(
        demo_repo,
        settings,
        "store-100",
        "run-edit",
        "reduce",
        [{"priority": milk.priority, "quantity": 2}],
    )
    assert reduced.status == "published"
    assert next(line.quantity for line in reduced.lines if line.sku == "milk-1l") == 2


def test_stale_inventory_fails_closed(demo_repo, settings):
    for position in demo_repo.list_inventory("store-100"):
        position.updated_at = AS_OF - timedelta(hours=7)
        demo_repo.upsert_inventory(position)
    pick, _trace = run_ml(demo_repo, ConstantForecaster(), settings, "store-100", "run-stale")
    assert pick.lines == []
    assert pick.blocked
    assert all(block.rule == "fail_closed" for block in pick.blocked)
