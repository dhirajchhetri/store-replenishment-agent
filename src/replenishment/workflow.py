"""Score a store, apply the rules, and save a pending pick list.

Warehouse publication is a separate approval step. A scheduled run never
writes a warehouse order by itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from replenishment.catalog import SKU_BY_ID
from replenishment.config import Settings
from replenishment.domain import (
    AuditRecord,
    BlockedLine,
    Escalation,
    StoredPickList,
    WarehouseOrder,
)
from replenishment.errors import ConflictError, NotReadyError, PlanValidationError
from replenishment.features import FEATURE_COLUMNS, scoring_frame
from replenishment.forecast import Forecaster
from replenishment.guardrails import evaluate_guardrails
from replenishment.repository import ReplenishmentRepo
from replenishment.routing import HashRing
from replenishment.rules import explain_plan, plan_positions

UTC = UTC


def resolve_as_of(repo: ReplenishmentRepo) -> datetime:
    raw = repo.get_meta("as_of")
    if raw:
        return datetime.fromisoformat(raw)
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def default_run_id(store_id: str, as_of: datetime) -> str:
    return f"{store_id}-{as_of.strftime('%Y%m%dT%H')}"


def score_store(
    repo: ReplenishmentRepo,
    forecaster: Forecaster,
    settings: Settings,
    store_id: str,
    as_of: datetime | None = None,
) -> list[dict]:
    store = repo.get_store(store_id)
    if store is None:
        raise KeyError(store_id)
    moment = as_of or resolve_as_of(repo)
    positions = repo.list_inventory(store_id)
    frame = pd.DataFrame(
        scoring_frame(
            positions,
            repo.list_events(store_id),
            repo.list_signals(store_id),
            as_of=moment,
            open_hour=store.open_hour,
            close_hour=store.close_hour,
        )
    )
    if frame.empty:
        return []
    predictions = forecaster.predict_frame(frame)
    scored = []
    for position, forecast in zip(positions, predictions, strict=True):
        scored.append(
            {
                "shelf_id": position.shelf_id,
                "sku": position.sku,
                "forecast_units": round(float(forecast), 4),
            }
        )
    return scored


def forecasts_from_rows(rows: list[dict]) -> dict[tuple[str, str], float]:
    return {(row["shelf_id"], row["sku"]): float(row["forecast_units"]) for row in rows}


def guardrail_lines(repo: ReplenishmentRepo, settings: Settings, store_id: str, lines, blocked, as_of: datetime):
    """Re-check every positive quantity. A failure moves the line to blocked."""

    from datetime import timedelta

    kept = []
    extra = list(blocked)
    for line in lines:
        position = repo.get_inventory(store_id, line.sku)
        if position is None:
            extra.append(_blocked(line, "fail_closed", "missing inventory for guardrail re-check"))
            continue
        result = evaluate_guardrails(
            quantity=line.quantity,
            on_shelf=position.on_shelf,
            shelf_capacity=position.shelf_capacity,
            backroom=position.backroom,
            reserved_units=position.reserved_units,
            forecast_units=line.forecast_units,
            updated_at=position.updated_at,
            as_of=as_of,
            stale_after=timedelta(minutes=settings.stale_after_minutes),
        )
        if result.passed:
            kept.append(line)
        else:
            extra.append(_blocked(line, result.rule, result.reason))
    extra.sort(key=lambda block: (block.shelf_id, block.sku))
    return kept, extra


def _blocked(line, rule: str, reason: str) -> BlockedLine:
    return BlockedLine(
        shelf_id=line.shelf_id,
        sku=line.sku,
        rule=rule,
        reason=reason,
        attempts=0,
        escalated=True,
    )


def build_plan(repo, forecaster, settings, store_id: str, as_of: datetime | None = None):
    moment = as_of or resolve_as_of(repo)
    rows = score_store(repo, forecaster, settings, store_id, moment)
    lines, blocked = plan_positions(
        repo.list_inventory(store_id),
        forecasts_from_rows(rows),
        settings=settings,
        as_of=moment,
        residual_std=forecaster.residual_std,
    )
    lines, blocked = guardrail_lines(repo, settings, store_id, lines, blocked, moment)
    for index, line in enumerate(lines, start=1):
        line.priority = index
    return moment, rows, lines, blocked


def persist_pending(
    repo: ReplenishmentRepo,
    settings: Settings,
    *,
    store_id: str,
    run_id: str,
    source: str,
    as_of: datetime,
    lines,
    blocked,
    explanation: str,
    detail: dict | None = None,
) -> tuple[StoredPickList, bool]:
    store = repo.get_store(store_id)
    if store is None:
        raise KeyError(store_id)
    ring = HashRing()
    pick = StoredPickList(
        store_id=store_id,
        run_id=run_id,
        lines=lines,
        blocked=blocked,
        explanation=explanation,
        source=source,
        status="pending_approval",
        store_region=store.region,
        agent_region=ring.owner(store_id),
        as_of=as_of,
        created_at=datetime.now(UTC),
    )
    saved, created = repo.insert_pick_list(pick)
    if not created:
        return saved, False
    for block in blocked:
        if block.escalated:
            repo.add_escalation(
                Escalation(
                    escalation_id=f"{run_id}:{block.shelf_id}:{block.sku}",
                    store_id=store_id,
                    run_id=run_id,
                    shelf_id=block.shelf_id,
                    sku=block.sku,
                    rule=block.rule,
                    reason=block.reason,
                    ts=datetime.now(UTC),
                )
            )
    repo.add_audit(
        AuditRecord(
            audit_id=f"{run_id}:draft",
            store_id=store_id,
            run_id=run_id,
            outcome="pending_approval",
            detail=detail or {"source": source, "lines": len(lines), "blocked": len(blocked)},
            ts=datetime.now(UTC),
        )
    )
    return saved, True


def run_ml(
    repo: ReplenishmentRepo,
    forecaster: Forecaster | None,
    settings: Settings,
    store_id: str,
    run_id: str | None = None,
) -> tuple[StoredPickList, list[str]]:
    if forecaster is None:
        raise NotReadyError("no forecaster is loaded")
    if repo.get_store(store_id) is None:
        raise KeyError(store_id)
    as_of_for_id = resolve_as_of(repo)
    chosen = run_id or default_run_id(store_id, as_of_for_id)
    existing = repo.get_pick_list(store_id, chosen)
    if existing is not None:
        return existing, ["ExistingPickList"]
    trace = ["ScoreStore", "DecideStore", "GuardrailStore"]
    as_of, _rows, lines, blocked = build_plan(repo, forecaster, settings, store_id)
    explanation = explain_plan(store_id, lines, blocked)
    saved, created = persist_pending(
        repo,
        settings,
        store_id=store_id,
        run_id=chosen,
        source="ml",
        as_of=as_of,
        lines=lines,
        blocked=blocked,
        explanation=explanation,
    )
    trace.append("SavePendingPickList" if created else "ExistingPickList")
    return saved, trace


def approve(
    repo: ReplenishmentRepo,
    settings: Settings,
    store_id: str,
    run_id: str,
    action: str,
    lines: list[dict] | None = None,
) -> StoredPickList:
    """Approve, reject, or reduce a pending list. Publish only after a pass."""

    pick = repo.get_pick_list(store_id, run_id)
    if pick is None:
        raise KeyError(run_id)
    if action == "approve" and pick.status == "published":
        return pick
    if pick.status != "pending_approval":
        raise ConflictError(f"pick list is {pick.status}")
    if action not in {"approve", "reject", "reduce"}:
        raise ValueError("action must be approve, reject, or reduce")
    if action == "reject":
        pick.status = "rejected"
        repo.update_pick_list(pick)
        repo.add_audit(
            AuditRecord(
                audit_id=f"{run_id}:reject",
                store_id=store_id,
                run_id=run_id,
                outcome="rejected",
                detail={},
                ts=datetime.now(UTC),
            )
        )
        return pick

    updated = list(pick.lines)
    if action == "reduce":
        if not lines:
            raise ValueError("reduce requires line quantities")
        requested = {int(item["priority"]): int(item["quantity"]) for item in lines}
        rewritten = []
        for line in updated:
            if line.priority not in requested:
                rewritten.append(line)
                continue
            quantity = requested[line.priority]
            if quantity > line.quantity or quantity < 0:
                raise PlanValidationError("a reduced quantity must stay between 0 and the drafted quantity")
            if quantity == 0:
                continue
            line.quantity = quantity
            rewritten.append(line)
        updated = rewritten
        if not updated:
            pick.status = "rejected"
            pick.lines = []
            repo.update_pick_list(pick)
            return pick

    checked, _blocked_extra = guardrail_lines(repo, settings, store_id, updated, [], pick.as_of)
    if len(checked) != len(updated):
        raise PlanValidationError("the edited list failed a guardrail check and was not published")
    for index, line in enumerate(checked, start=1):
        line.priority = index
    pick.lines = checked
    pick.status = "published"
    order = WarehouseOrder(
        idempotency_key=f"{store_id}#{run_id}",
        store_id=store_id,
        run_id=run_id,
        lines=checked,
        published_at=datetime.now(UTC),
    )
    repo.insert_warehouse(order)
    repo.update_pick_list(pick)
    repo.add_audit(
        AuditRecord(
            audit_id=f"{run_id}:publish",
            store_id=store_id,
            run_id=run_id,
            outcome="published",
            detail={"lines": len(checked)},
            ts=datetime.now(UTC),
        )
    )
    return pick


def velocity_report(repo: ReplenishmentRepo, store_id: str, as_of: datetime) -> list[dict]:
    """Read the velocity counters, not the raw event log."""

    from datetime import timedelta

    rows = repo.list_velocity(store_id)
    positions = repo.list_inventory(store_id)
    report = []
    for position in positions:
        spec = SKU_BY_ID[position.sku]
        matched = [row for row in rows if row["shelf_id"] == position.shelf_id and row["sku"] == position.sku]

        def units_between(start: datetime, end: datetime) -> float:
            total = 0.0
            for row in matched:
                bucket = datetime.fromisoformat(row["bucket"])
                if start <= bucket < end:
                    total += row["units"]
            return total

        report.append(
            {
                "shelf_id": position.shelf_id,
                "sku": spec.sku,
                "units_last_hour": units_between(as_of - timedelta(hours=1), as_of),
                "units_last_3h": units_between(as_of - timedelta(hours=3), as_of),
                "units_today": units_between(
                    as_of.replace(hour=0, minute=0, second=0, microsecond=0),
                    as_of,
                ),
            }
        )
    return report


def require_columns(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in FEATURE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"feature frame is missing {missing}")
    return frame
