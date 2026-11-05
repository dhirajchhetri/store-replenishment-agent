"""Deterministic model used when Bedrock is not configured.

It walks the planner tools in a fixed order and copies the rule engine's
draft into the structured output. It does not call a foundation model.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any, override
from uuid import uuid4

from strands.models import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from replenishment.errors import PlanValidationError, ToolBudgetExceeded

SYSTEM_PROMPT = """You are the replenishment planner for one store.
Keep shelves stocked through close without over-ordering.
Call read_shelf_velocity, then check_inventory, then predict_burn_rate, then draft_pick_list.
Quantities, priorities, and blocked rules come from draft_pick_list. Copy them.
You may phrase the explanation, but do not change a number.
Finish by calling PickListPlan.
"""

_ORDER = (
    "read_shelf_velocity",
    "check_inventory",
    "predict_burn_rate",
    "draft_pick_list",
)


class StubReplenishmentModel(Model):
    """Strands model provider that replays the replenishment tool order."""

    def __init__(self, max_tool_calls: int = 40) -> None:
        self._config: dict[str, Any] = {
            "model_id": "stub-replenishment",
            "max_tool_calls": max_tool_calls,
        }

    @override
    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    @override
    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    @override
    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        del system_prompt
        name, payload = self._next_call(messages, tool_specs or [])
        tool_use_id = f"tooluse_{uuid4().hex[:12]}"
        body = json.dumps(payload)
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}},
            }
        }
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": body}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }

    @override
    async def structured_output(
        self,
        output_model: type,
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        from strands.event_loop import streaming
        from strands.tools.structured_output.structured_output_utils import (
            convert_pydantic_to_tool_spec,
        )

        tool_spec = convert_pydantic_to_tool_spec(output_model)
        response = self.stream(
            prompt,
            [tool_spec],
            system_prompt,
            tool_choice={"any": {}},
            structured_output_model=output_model,
        )
        async for event in streaming.process_stream(response):
            yield event
        stop_reason, message, _usage, _metrics = event["stop"]
        if stop_reason != "tool_use":
            raise ValueError(f"stub model stopped with {stop_reason}")
        for block in message["content"]:
            tool_use = block.get("toolUse")
            if tool_use and tool_use["name"] == tool_spec["name"]:
                yield {"output": output_model(**tool_use["input"])}
                return
        raise ValueError("stub model did not call the structured output tool")

    def _next_call(self, messages: Messages, tool_specs: list[ToolSpec]) -> tuple[str, dict]:
        store_id = _field(messages, "store_id")
        run_id = _field(messages, "run_id")
        results = _tool_results(messages)
        failed = [item for item in results if item["name"] == "PickListPlan" and item["status"] != "success"]
        if failed:
            raise PlanValidationError(f"structured output was rejected: {failed[-1]['text']}")
        succeeded = {item["name"] for item in results if item["status"] == "success"}
        domain_calls = sum(1 for item in results if item["name"] not in {None, "PickListPlan"})
        limit = int(self._config["max_tool_calls"])
        for name in _ORDER:
            if name not in succeeded:
                if domain_calls >= limit:
                    raise ToolBudgetExceeded(f"stopped after {domain_calls} tool calls, before {name}")
                return name, {"store_id": store_id}
        draft = next(item for item in results if item["name"] == "draft_pick_list" and item["status"] == "success")
        payload = {
            "store_id": store_id,
            "run_id": run_id,
            "lines": draft["data"]["lines"],
            "blocked": draft["data"]["blocked"],
            "explanation": draft["data"]["explanation"],
        }
        known = {spec["name"] for spec in tool_specs}
        if "PickListPlan" not in known and known:
            # The structured-output tool is added by the SDK under the schema name.
            structured = [spec["name"] for spec in tool_specs if spec["name"] not in _ORDER]
            if not structured:
                raise PlanValidationError("no structured output tool is available")
            return structured[-1], payload
        return "PickListPlan", payload


def _field(messages: Messages, name: str) -> str:
    needle = f"{name}="
    for message in messages:
        if message.get("role") != "user":
            continue
        for block in message.get("content") or []:
            text = block.get("text") or ""
            start = text.find(needle)
            if start < 0:
                continue
            token = text[start + len(needle) :].split()[0].strip(".,")
            return token
    raise PlanValidationError(f"prompt is missing {name}")


def _tool_results(messages: Messages) -> list[dict]:
    uses: dict[str, dict] = {}
    results: list[dict] = []
    for message in messages:
        for block in message.get("content") or []:
            if "toolUse" in block:
                use = block["toolUse"]
                uses[use["toolUseId"]] = use
            if "toolResult" in block:
                result = block["toolResult"]
                use = uses.get(result["toolUseId"], {})
                text = "".join(part.get("text", "") for part in result.get("content") or [])
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw": text}
                results.append(
                    {
                        "name": use.get("name"),
                        "status": result.get("status"),
                        "text": text,
                        "data": data,
                    }
                )
    return results
