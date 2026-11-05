"""Per-run state visible to tools. The object is shared; the context var is not."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from replenishment.config import Settings
from replenishment.forecast import Forecaster
from replenishment.repository import ReplenishmentRepo

_CURRENT: ContextVar[RunContext | None] = ContextVar("replenishment_run", default=None)


@dataclass
class RunContext:
    repo: ReplenishmentRepo
    forecaster: Forecaster
    settings: Settings
    store_id: str
    run_id: str


def push_context(ctx: RunContext):
    return _CURRENT.set(ctx)


def reset_context(token) -> None:
    _CURRENT.reset(token)


def require_context() -> RunContext:
    ctx = _CURRENT.get()
    if ctx is None:
        raise RuntimeError("replenishment run context is not set")
    return ctx


def same_store(store_id: str) -> RunContext:
    ctx = require_context()
    if store_id != ctx.store_id:
        raise ValueError("store_id does not match this run")
    return ctx
