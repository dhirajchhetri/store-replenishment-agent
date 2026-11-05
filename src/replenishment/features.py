"""Shelf features from POS sales, RFID counts, and store signals.

A row at time T uses only records with timestamp < T. The training target is
units sold from T until close, so the label is not one of the inputs.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta

from replenishment.catalog import CATEGORY_CODE, SKU_BY_ID, SKUS
from replenishment.domain import InventoryPosition, ShelfEvent, Signal, SkuSpec

FEATURE_COLUMNS: tuple[str, ...] = (
    "lag_1h",
    "lag_2h",
    "lag_3h",
    "same_hour_yesterday",
    "rolling_mean_6h",
    "rolling_mean_7d",
    "hour",
    "dow",
    "hours_until_close",
    "is_promo",
    "temp_c",
    "precip_mm",
    "foot_traffic",
    "rfid_on_shelf",
    "shelf_capacity",
    "category_code",
    "pack_size",
)


def hours_until_close(as_of: datetime, close_hour: int) -> float:
    close = as_of.replace(hour=close_hour, minute=0, second=0, microsecond=0)
    return max(0.0, (close - as_of).total_seconds() / 3600)


def _window_sum(points: list[tuple[datetime, float]], start: datetime, end: datetime) -> float:
    times = [point[0] for point in points]
    start_at = bisect.bisect_left(times, start)
    end_at = bisect.bisect_left(times, end)
    return float(sum(point[1] for point in points[start_at:end_at]))


def _latest_before(points: list[tuple[datetime, float]], as_of: datetime) -> float | None:
    times = [point[0] for point in points]
    index = bisect.bisect_left(times, as_of) - 1
    if index < 0:
        return None
    return points[index][1]


def _latest_signal(signals: list[Signal], as_of: datetime) -> Signal | None:
    times = [signal.ts for signal in signals]
    index = bisect.bisect_left(times, as_of) - 1
    if index < 0:
        return None
    return signals[index]


class _Series:
    def __init__(self, events: list[ShelfEvent]) -> None:
        ordered = sorted(events, key=lambda event: event.ts)
        self.sales = [(event.ts, event.quantity) for event in ordered if event.event_type == "pos_sale"]
        self.rfid = [(event.ts, event.quantity) for event in ordered if event.event_type == "rfid_count"]


def index_events(events: list[ShelfEvent]) -> dict[tuple[str, str], _Series]:
    grouped: dict[tuple[str, str], list[ShelfEvent]] = {}
    for event in events:
        grouped.setdefault((event.store_id, event.sku), []).append(event)
    return {key: _Series(value) for key, value in grouped.items()}


def index_signals(signals: list[Signal]) -> dict[str, list[Signal]]:
    grouped: dict[str, list[Signal]] = {}
    for signal in signals:
        grouped.setdefault(signal.store_id, []).append(signal)
    for rows in grouped.values():
        rows.sort(key=lambda signal: signal.ts)
    return grouped


def feature_row(
    *,
    as_of: datetime,
    spec: SkuSpec,
    position: InventoryPosition | None,
    series: _Series | None,
    signals: list[Signal],
    close_hour: int,
    open_hour: int,
) -> dict[str, float]:
    """One numeric row. Missing history is zero, not a guess at the mean."""

    sales = series.sales if series else []
    rfid = series.rfid if series else []
    signal = _latest_signal(signals, as_of)
    open_span = max(close_hour - open_hour, 1)
    lag_1h = _window_sum(sales, as_of - timedelta(hours=1), as_of)
    lag_2h = _window_sum(sales, as_of - timedelta(hours=2), as_of - timedelta(hours=1))
    lag_3h = _window_sum(sales, as_of - timedelta(hours=3), as_of - timedelta(hours=2))
    yesterday = _window_sum(sales, as_of - timedelta(hours=24), as_of - timedelta(hours=23))
    rolling_6h = _window_sum(sales, as_of - timedelta(hours=6), as_of) / 6
    rolling_7d = _window_sum(sales, as_of - timedelta(days=7), as_of) / (7 * open_span)
    latest_rfid = _latest_before(rfid, as_of)
    capacity = float(position.shelf_capacity if position else spec.shelf_capacity)
    return {
        "lag_1h": lag_1h,
        "lag_2h": lag_2h,
        "lag_3h": lag_3h,
        "same_hour_yesterday": yesterday,
        "rolling_mean_6h": rolling_6h,
        "rolling_mean_7d": rolling_7d,
        "hour": float(as_of.hour),
        "dow": float(as_of.weekday()),
        "hours_until_close": hours_until_close(as_of, close_hour),
        "is_promo": 1.0 if signal and spec.sku in signal.promo_skus else 0.0,
        "temp_c": signal.temp_c if signal else 18.0,
        "precip_mm": signal.precip_mm if signal else 0.0,
        "foot_traffic": signal.foot_traffic if signal else 0.0,
        "rfid_on_shelf": float(latest_rfid) if latest_rfid is not None else 0.0,
        "shelf_capacity": capacity,
        "category_code": float(CATEGORY_CODE[spec.category]),
        "pack_size": float(spec.pack_size),
    }


def target_units(series: _Series | None, as_of: datetime, close_hour: int) -> float:
    if series is None:
        return 0.0
    close = as_of.replace(hour=close_hour, minute=0, second=0, microsecond=0)
    return _window_sum(series.sales, as_of, close)


def iter_training_rows(
    events: list[ShelfEvent],
    signals: list[Signal],
    *,
    store_id: str,
    open_hour: int,
    close_hour: int,
    holdout_date,
):
    """Yield ``(row, target, as_of, spec, split)`` for one store.

    ``split`` is ``train`` before ``holdout_date`` and ``holdout`` on that date.
    Hours on the holdout date are never yielded as training rows.
    """

    series_index = index_events([event for event in events if event.store_id == store_id])
    signal_rows = index_signals(signals).get(store_id, [])
    dates = sorted({event.ts.date() for event in events if event.store_id == store_id})
    for day in dates:
        for hour in range(open_hour, close_hour):
            as_of = datetime(day.year, day.month, day.day, hour, tzinfo=events[0].ts.tzinfo)
            split = "holdout" if day >= holdout_date else "train"
            for spec in SKUS:
                series = series_index.get((store_id, spec.sku))
                row = feature_row(
                    as_of=as_of,
                    spec=spec,
                    position=None,
                    series=series,
                    signals=signal_rows,
                    close_hour=close_hour,
                    open_hour=open_hour,
                )
                # Capacity still comes from the catalog when no live position is passed.
                row["shelf_capacity"] = float(spec.shelf_capacity)
                label = target_units(series, as_of, close_hour)
                yield row, label, as_of, spec, split


def scoring_frame(
    positions: list[InventoryPosition],
    events: list[ShelfEvent],
    signals: list[Signal],
    *,
    as_of: datetime,
    open_hour: int,
    close_hour: int,
) -> list[dict[str, float]]:
    series_index = index_events(events)
    signal_index = index_signals(signals)
    rows: list[dict[str, float]] = []
    for position in sorted(positions, key=lambda item: (item.shelf_id, item.sku)):
        spec = SKU_BY_ID[position.sku]
        row = feature_row(
            as_of=as_of,
            spec=spec,
            position=position,
            series=series_index.get((position.store_id, position.sku)),
            signals=signal_index.get(position.store_id, []),
            close_hour=close_hour,
            open_hour=open_hour,
        )
        rows.append(row)
    return rows
