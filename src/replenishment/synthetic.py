"""Synthetic RFID, POS, weather, and foot-traffic history.

Sales are Poisson draws from a rate that depends on hour, weekday, promo,
weather, and category. The generator does not remove sales when a shelf is
empty, so the series is a demand signal rather than a censored one. After the
history is written, on-hand counts are replaced with the fixed demo snapshot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from replenishment.catalog import AS_OF, HISTORY_START, SKUS, STORES, SkuSpec
from replenishment.domain import ShelfEvent, Signal

HOUR_PROFILE: dict[int, float] = {
    8: 0.4,
    9: 0.6,
    10: 0.8,
    11: 1.0,
    12: 1.4,
    13: 1.2,
    14: 0.9,
    15: 0.8,
    16: 1.0,
    17: 1.5,
    18: 1.6,
    19: 1.2,
    20: 0.7,
}


@dataclass
class SyntheticDataset:
    events: list[ShelfEvent]
    signals: list[Signal]
    as_of: datetime
    seed: int
    days: int


def _dow_factor(day: datetime) -> float:
    if day.weekday() >= 5:
        return 1.2
    if day.weekday() == 0:
        return 0.9
    return 1.0


def _rate(spec: SkuSpec, hour: int, day: datetime, precip: float, traffic: float, promo: bool) -> float:
    rate = spec.base_hourly * HOUR_PROFILE[hour] * _dow_factor(day)
    if promo:
        rate *= 1.35
    if precip > 1.0 and spec.category == "snack":
        rate *= 1.25
    if precip > 1.0 and spec.category == "beverage":
        rate *= 0.75
    if traffic > 100:
        rate *= 1.1
    return max(rate, 0.05)


def generate(seed: int = 7, days: int = 14, as_of: datetime = AS_OF) -> SyntheticDataset:
    """Build ``days`` of store-hour history ending on ``as_of``'s date."""

    if days < 2:
        raise ValueError("days must be at least 2 so training and holdout differ")
    rng = np.random.default_rng(seed)
    start_day = as_of.date() - timedelta(days=days - 1)
    # HISTORY_START documents the default calendar; the loop uses start_day.
    _ = HISTORY_START
    events: list[ShelfEvent] = []
    signals: list[Signal] = []

    for store in STORES:
        _simulate_store(store, start_day, days, as_of, rng, events, signals)

    events.sort(key=lambda event: (event.store_id, event.ts, event.sku, event.event_type))
    signals.sort(key=lambda signal: (signal.store_id, signal.ts))
    return SyntheticDataset(events=events, signals=signals, as_of=as_of, seed=seed, days=days)


def _simulate_store(store, start_day, days, as_of, rng, events, signals) -> None:
    on_shelf = {spec.sku: spec.shelf_capacity // 2 for spec in SKUS}
    backroom = {spec.sku: 24 for spec in SKUS}
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        day_dt = datetime(day.year, day.month, day.day, tzinfo=as_of.tzinfo)
        temp = 18 + 6 * math.sin(offset / 3) + float(rng.normal(0, 0.8))
        precip = float(rng.gamma(1.2, 1.0)) if rng.random() < 0.3 else 0.0
        promo = [spec.sku for spec in SKUS if spec.category == "bakery" and day_dt.weekday() >= 5]
        for hour, profile in HOUR_PROFILE.items():
            ts = datetime(day.year, day.month, day.day, hour, tzinfo=as_of.tzinfo)
            traffic = max(0.0, 80 * profile * (0.7 if precip > 2 else 1.0) + float(rng.normal(0, 4)))
            signals.append(
                Signal(
                    store_id=store.store_id,
                    ts=ts,
                    foot_traffic=round(traffic, 2),
                    temp_c=round(temp, 2),
                    precip_mm=round(precip, 2),
                    promo_skus=list(promo),
                )
            )
            for spec in SKUS:
                rate = _rate(spec, hour, day_dt, precip, traffic, spec.sku in promo)
                sales = int(rng.poisson(rate))
                on_shelf[spec.sku] = max(0, on_shelf[spec.sku] - sales)
                if sales:
                    events.append(
                        ShelfEvent(
                            event_id=f"{store.store_id}|{spec.sku}|pos_sale|{ts.isoformat()}",
                            store_id=store.store_id,
                            shelf_id=spec.shelf_id,
                            sku=spec.sku,
                            event_type="pos_sale",
                            ts=ts,
                            quantity=float(sales),
                        )
                    )
                if on_shelf[spec.sku] < 4 and backroom[spec.sku] > 0:
                    move = min(8, backroom[spec.sku], spec.shelf_capacity - on_shelf[spec.sku])
                    on_shelf[spec.sku] += move
                    backroom[spec.sku] -= move
                events.append(
                    ShelfEvent(
                        event_id=f"{store.store_id}|{spec.sku}|rfid_count|{ts.isoformat()}",
                        store_id=store.store_id,
                        shelf_id=spec.shelf_id,
                        sku=spec.sku,
                        event_type="rfid_count",
                        ts=ts,
                        quantity=float(on_shelf[spec.sku]),
                    )
                )
