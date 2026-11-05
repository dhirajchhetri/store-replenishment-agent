"""Records that move through ingest, scoring, and the pick list."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def ensure_aware(ts: datetime) -> datetime:
    """Reject naive timestamps so SQLite ordering stays in UTC."""

    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts


class Store(BaseModel):
    store_id: str
    region: str
    open_hour: int = 8
    close_hour: int = 21


class SkuSpec(BaseModel):
    sku: str
    name: str
    category: str
    pack_size: int
    shelf_id: str
    shelf_capacity: int
    base_hourly: float


class ShelfEvent(BaseModel):
    event_id: str
    store_id: str
    shelf_id: str
    sku: str
    event_type: Literal["pos_sale", "rfid_count"]
    ts: datetime
    quantity: float


class Signal(BaseModel):
    store_id: str
    ts: datetime
    foot_traffic: float
    temp_c: float
    precip_mm: float
    promo_skus: list[str] = Field(default_factory=list)


class InventoryPosition(BaseModel):
    store_id: str
    sku: str
    shelf_id: str
    on_shelf: int
    backroom: int
    shelf_capacity: int
    reserved_units: int
    reserved_for_store: str | None = None
    updated_at: datetime


class InboundRecord(BaseModel):
    """One Kinesis or IoT payload. ``kind`` selects the stored record."""

    kind: Literal["pos_sale", "rfid_count", "signal"]
    store_id: str
    ts: datetime
    shelf_id: str = ""
    sku: str = ""
    quantity: float = 0
    foot_traffic: float = 0
    temp_c: float = 0
    precip_mm: float = 0
    promo_skus: list[str] = Field(default_factory=list)


class PickLine(BaseModel):
    """One warehouse line. Quantity is a rule-engine output, not a model guess."""

    shelf_id: str = Field(description="Shelf that receives the units")
    sku: str = Field(description="SKU to pick")
    quantity: int = Field(description="Units to pick. Copy this from draft_pick_list.")
    priority: int = Field(description="1 is the most urgent line")
    reason: str = Field(description="Short factual reason copied from draft_pick_list")
    forecast_units: float = Field(description="Forecast units from now until close")
    on_shelf: int = Field(description="Units already on the destination shelf")
    hours_of_cover: float = Field(description="Hours the original shelf can cover at the forecast rate")
    substituted_for: str = Field(
        default="",
        description="Original SKU when this line is a substitute, otherwise empty",
    )


class BlockedLine(BaseModel):
    """A shelf the rule engine could not replenish."""

    shelf_id: str
    sku: str
    rule: str = Field(description="shelf_capacity, reserved_stock, insufficient_stock, or fail_closed")
    reason: str
    attempts: int = Field(description="Substitute attempts already made")
    escalated: bool = Field(description="True when nobody should pick this shelf")


class PickListPlan(BaseModel):
    """Prioritised pick list for one store run.

    Copy quantities, priorities, forecasts, and blocked rules from draft_pick_list.
    The explanation may be written in your own words, but it must describe those lines.
    """

    store_id: str
    run_id: str
    lines: list[PickLine]
    blocked: list[BlockedLine]
    explanation: str = Field(description="Why the lines are in this order")


class StoredPickList(PickListPlan):
    source: Literal["ml", "agent"]
    status: Literal["pending_approval", "published", "rejected"]
    store_region: str
    agent_region: str
    as_of: datetime
    created_at: datetime


class WarehouseOrder(BaseModel):
    idempotency_key: str
    store_id: str
    run_id: str
    lines: list[PickLine]
    published_at: datetime


class Escalation(BaseModel):
    escalation_id: str
    store_id: str
    run_id: str
    shelf_id: str
    sku: str
    rule: str
    reason: str
    ts: datetime


class AuditRecord(BaseModel):
    audit_id: str
    store_id: str
    run_id: str
    outcome: str
    detail: dict[str, Any]
    ts: datetime
