import pytest

from replenishment.agent.planner import run_agent
from replenishment.agent.tools import check_inventory
from replenishment.agent.validate import validate_plan
from replenishment.config import Settings
from replenishment.domain import PickListPlan
from replenishment.errors import PlanValidationError, ToolBudgetExceeded
from replenishment.workflow import run_ml
from tests.conftest import ConstantForecaster


def _has_budget_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ToolBudgetExceeded):
            return True
        current = current.__cause__ or current.__context__
    return False


def test_agent_matches_the_rule_engine(demo_repo, settings):
    forecaster = ConstantForecaster()
    agent_pick, trace = run_agent(demo_repo, forecaster, settings, "store-100", "run-agent")
    ml_pick, _ml_trace = run_ml(demo_repo, forecaster, settings, "store-100", "run-ml")
    assert "ValidatePlan" in trace
    assert agent_pick.status == "pending_approval"
    assert agent_pick.source == "agent"
    assert [(line.sku, line.quantity, line.priority, line.substituted_for) for line in agent_pick.lines] == [
        (line.sku, line.quantity, line.priority, line.substituted_for) for line in ml_pick.lines
    ]
    assert [(block.sku, block.rule) for block in agent_pick.blocked] == [
        (block.sku, block.rule) for block in ml_pick.blocked
    ]
    assert "milk-1l" in agent_pick.explanation


def test_changed_quantity_is_rejected(demo_repo, settings):
    forecaster = ConstantForecaster()
    pick, _trace = run_ml(demo_repo, forecaster, settings, "store-100", "run-base")
    plan = PickListPlan(
        store_id=pick.store_id,
        run_id="other",
        lines=pick.lines,
        blocked=pick.blocked,
        explanation=pick.explanation,
    )
    plan.lines[0].quantity += 1
    with pytest.raises(PlanValidationError):
        validate_plan(plan, demo_repo, forecaster, settings)


def test_tool_refuses_a_different_store(demo_repo, settings):
    from replenishment.agent.context import RunContext, push_context, reset_context

    token = push_context(RunContext(demo_repo, ConstantForecaster(), settings, "store-100", "run-ctx"))
    try:
        with pytest.raises(ValueError):
            check_inventory("store-200")
    finally:
        reset_context(token)


def test_tool_budget_stops_before_a_pick_list_is_written(demo_repo, settings):
    tight = Settings(data_dir=settings.data_dir, max_tool_calls=2)
    with pytest.raises(Exception) as caught:
        run_agent(demo_repo, ConstantForecaster(), tight, "store-100", "run-budget")
    assert _has_budget_error(caught.value)
    assert demo_repo.get_pick_list("store-100", "run-budget") is None
