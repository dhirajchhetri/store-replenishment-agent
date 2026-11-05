from datetime import UTC, datetime

from replenishment.catalog import SKU_BY_ID
from replenishment.domain import ShelfEvent, Signal
from replenishment.features import feature_row, hours_until_close, index_events, target_units

UTC = UTC


def _event(sku: str, hour: int, qty: float, kind: str = "pos_sale") -> ShelfEvent:
    spec = SKU_BY_ID[sku]
    ts = datetime(2026, 11, 2, hour, tzinfo=UTC)
    return ShelfEvent(
        event_id=f"{kind}-{hour}-{qty}",
        store_id="store-100",
        shelf_id=spec.shelf_id,
        sku=sku,
        event_type=kind,
        ts=ts,
        quantity=qty,
    )


def test_lags_ignore_the_current_hour_and_the_future():
    events = [
        _event("milk-1l", 14, 1),
        _event("milk-1l", 15, 4),
        _event("milk-1l", 16, 9),
        _event("milk-1l", 17, 2),
        _event("milk-1l", 15, 7, "rfid_count"),
    ]
    as_of = datetime(2026, 11, 2, 16, tzinfo=UTC)
    series = index_events(events)[("store-100", "milk-1l")]
    row = feature_row(
        as_of=as_of,
        spec=SKU_BY_ID["milk-1l"],
        position=None,
        series=series,
        signals=[],
        close_hour=21,
        open_hour=8,
    )
    assert row["lag_1h"] == 4
    assert row["lag_2h"] == 1
    assert row["lag_3h"] == 0
    assert row["rfid_on_shelf"] == 7
    assert hours_until_close(as_of, 21) == 5
    assert target_units(series, as_of, 21) == 11


def test_promo_and_weather_come_from_the_latest_signal_before_as_of():
    as_of = datetime(2026, 11, 2, 16, tzinfo=UTC)
    signals = [
        Signal(
            store_id="store-100",
            ts=datetime(2026, 11, 2, 15, tzinfo=UTC),
            foot_traffic=120,
            temp_c=11,
            precip_mm=2.5,
            promo_skus=["bread-loaf"],
        ),
        Signal(
            store_id="store-100",
            ts=datetime(2026, 11, 2, 16, tzinfo=UTC),
            foot_traffic=999,
            temp_c=30,
            precip_mm=0,
            promo_skus=[],
        ),
    ]
    row = feature_row(
        as_of=as_of,
        spec=SKU_BY_ID["bread-loaf"],
        position=None,
        series=None,
        signals=signals,
        close_hour=21,
        open_hour=8,
    )
    assert row["is_promo"] == 1
    assert row["temp_c"] == 11
    assert row["precip_mm"] == 2.5
    assert row["foot_traffic"] == 120
