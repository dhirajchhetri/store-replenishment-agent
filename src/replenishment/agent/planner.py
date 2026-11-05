"""Run the Strands planner and persist a pending pick list when validation passes."""

from __future__ import annotations

import logging
import os

from replenishment.agent.context import RunContext, push_context, reset_context
from replenishment.agent.stub_model import SYSTEM_PROMPT, StubReplenishmentModel
from replenishment.agent.tools import TOOLS
from replenishment.agent.validate import validate_plan
from replenishment.domain import PickListPlan
from replenishment.errors import NotReadyError, PlanValidationError
from replenishment.workflow import default_run_id, persist_pending, resolve_as_of

logger = logging.getLogger(__name__)


def build_model(settings, *, max_tool_calls: int | None = None):
    limit = settings.max_tool_calls if max_tool_calls is None else max_tool_calls
    if settings.llm_mode == "stub":
        return StubReplenishmentModel(max_tool_calls=limit)
    if settings.llm_mode != "bedrock":
        raise ValueError(settings.llm_mode)
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
        max_tokens=2000,
        temperature=0.0,
    )


def run_agent(repo, forecaster, settings, store_id: str, run_id: str | None = None):
    if forecaster is None:
        raise NotReadyError("no forecaster is loaded")
    if repo.get_store(store_id) is None:
        raise KeyError(store_id)
    as_of = resolve_as_of(repo)
    chosen = run_id or default_run_id(store_id, as_of)
    existing = repo.get_pick_list(store_id, chosen)
    if existing is not None:
        return existing, ["ExistingPickList"]

    from strands import Agent

    ctx = RunContext(
        repo=repo,
        forecaster=forecaster,
        settings=settings,
        store_id=store_id,
        run_id=chosen,
    )
    token = push_context(ctx)
    try:
        agent = Agent(
            model=build_model(settings),
            tools=TOOLS,
            system_prompt=SYSTEM_PROMPT,
            callback_handler=lambda **_kwargs: None,
        )
        prompt = f"Plan replenishment for store_id={store_id} run_id={chosen} as_of={as_of.isoformat()}."
        result = agent(prompt, structured_output_model=PickListPlan)
        plan = result.structured_output
        if not isinstance(plan, PickListPlan):
            raise PlanValidationError("model returned no pick list")
        if plan.store_id != store_id or plan.run_id != chosen:
            raise PlanValidationError("model changed the store or the run id")
        validate_plan(plan, repo, forecaster, settings)
    finally:
        reset_context(token)

    saved, created = persist_pending(
        repo,
        settings,
        store_id=store_id,
        run_id=chosen,
        source="agent",
        as_of=as_of,
        lines=plan.lines,
        blocked=plan.blocked,
        explanation=plan.explanation,
        detail={"source": "agent", "model": os.environ.get("REPLENISHMENT_LLM", settings.llm_mode)},
    )
    logger.info("agent run store=%s run=%s created=%s", store_id, chosen, created)
    return saved, ["AgentLoop", "ValidatePlan", "SavePendingPickList" if created else "ExistingPickList"]
