"""Sample stores and the fixed 16:00 shelf snapshot used by the walkthrough.

The snapshot is an input. It is not a claim about what a forecast will order.
"""

from __future__ import annotations

from datetime import UTC, datetime

from replenishment.domain import InventoryPosition, SkuSpec, Store

UTC = UTC

# Tuesday 20 Oct 2026 through the decision hour on Monday 2 Nov 2026.
HISTORY_START = datetime(2026, 10, 20, 8, tzinfo=UTC)
AS_OF = datetime(2026, 11, 2, 16, tzinfo=UTC)

STORES: tuple[Store, ...] = (
    Store(store_id="store-100", region="north"),
    Store(store_id="store-200", region="south"),
)

SKUS: tuple[SkuSpec, ...] = (
    SkuSpec(
        sku="milk-1l",
        name="Whole milk 1L",
        category="dairy",
        pack_size=6,
        shelf_id="A1",
        shelf_capacity=24,
        base_hourly=1.4,
    ),
    SkuSpec(
        sku="milk-2l",
        name="Whole milk 2L",
        category="dairy",
        pack_size=4,
        shelf_id="A1",
        shelf_capacity=12,
        base_hourly=0.6,
    ),
    SkuSpec(
        sku="bread-loaf",
        name="Sandwich loaf",
        category="bakery",
        pack_size=8,
        shelf_id="A2",
        shelf_capacity=20,
        base_hourly=1.0,
    ),
    SkuSpec(
        sku="bread-rolls",
        name="Dinner rolls",
        category="bakery",
        pack_size=6,
        shelf_id="A2",
        shelf_capacity=30,
        base_hourly=0.8,
    ),
    SkuSpec(
        sku="water-500",
        name="Water 500ml",
        category="beverage",
        pack_size=12,
        shelf_id="B1",
        shelf_capacity=40,
        base_hourly=1.6,
    ),
    SkuSpec(
        sku="water-1500",
        name="Water 1.5L",
        category="beverage",
        pack_size=6,
        shelf_id="B1",
        shelf_capacity=16,
        base_hourly=0.7,
    ),
    SkuSpec(
        sku="chips-small",
        name="Chips small",
        category="snack",
        pack_size=10,
        shelf_id="C1",
        shelf_capacity=36,
        base_hourly=0.9,
    ),
    SkuSpec(
        sku="chips-large",
        name="Chips large",
        category="snack",
        pack_size=6,
        shelf_id="C1",
        shelf_capacity=18,
        base_hourly=0.5,
    ),
)

SKU_BY_ID: dict[str, SkuSpec] = {spec.sku: spec for spec in SKUS}

# on_shelf, backroom, reserved_units at AS_OF. Reserved units are held for the other store.
DEMO_COUNTS: dict[str, tuple[int, int, int]] = {
    "milk-1l": (1, 18, 0),
    "milk-2l": (10, 8, 0),
    "bread-loaf": (16, 10, 0),
    "bread-rolls": (20, 10, 0),
    "water-500": (20, 24, 0),
    "water-1500": (1, 6, 6),
    "chips-small": (36, 0, 0),
    "chips-large": (0, 4, 4),
}

CATEGORY_CODE: dict[str, int] = {"bakery": 0, "beverage": 1, "dairy": 2, "snack": 3}


def other_store(store_id: str) -> str:
    if store_id == "store-100":
        return "store-200"
    if store_id == "store-200":
        return "store-100"
    raise KeyError(store_id)


def demo_inventory(store_id: str, as_of: datetime = AS_OF) -> list[InventoryPosition]:
    """Fixed on-hand counts so the sample store has a stockout and a reserved SKU."""

    reserved_for = other_store(store_id)
    rows: list[InventoryPosition] = []
    for spec in SKUS:
        on_shelf, backroom, reserved = DEMO_COUNTS[spec.sku]
        rows.append(
            InventoryPosition(
                store_id=store_id,
                sku=spec.sku,
                shelf_id=spec.shelf_id,
                on_shelf=on_shelf,
                backroom=backroom,
                shelf_capacity=spec.shelf_capacity,
                reserved_units=reserved,
                reserved_for_store=reserved_for if reserved else None,
                updated_at=as_of,
            )
        )
    return rows
