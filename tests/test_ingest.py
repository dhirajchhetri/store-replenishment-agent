import base64
from datetime import UTC, datetime

from replenishment.domain import InboundRecord
from replenishment.handlers import ingest
from replenishment.ingest import (
    KinesisPublisher,
    decode_record,
    encode_record,
    ingest_record,
    parse_iot_rfid,
)

UTC = UTC


def test_pos_sale_updates_velocity_and_a_repeat_does_not(demo_repo):
    record = InboundRecord(
        kind="pos_sale",
        store_id="store-100",
        shelf_id="A1",
        sku="milk-1l",
        quantity=4,
        ts=datetime(2026, 11, 2, 15, 30, tzinfo=UTC),
    )
    ingest_record(demo_repo, record)
    ingest_record(demo_repo, record)
    rows = demo_repo.list_velocity("store-100")
    assert len(rows) == 1
    assert rows[0]["units"] == 4
    assert rows[0]["sku"] == "milk-1l"


def test_rfid_updates_existing_inventory(demo_repo):
    record = parse_iot_rfid(
        {
            "store_id": "store-100",
            "shelf_id": "A1",
            "sku": "milk-1l",
            "on_shelf": 6,
            "ts": "2026-11-02T15:45:00+00:00",
        }
    )
    ingest_record(demo_repo, record)
    position = demo_repo.get_inventory("store-100", "milk-1l")
    assert position.on_shelf == 6


def test_kinesis_round_trip_and_handler(demo_repo, settings, monkeypatch):
    record = InboundRecord(
        kind="signal",
        store_id="store-100",
        ts=datetime(2026, 11, 2, 15, tzinfo=UTC),
        foot_traffic=40,
        temp_c=12,
        precip_mm=0.2,
    )
    encoded = encode_record(record)
    assert decode_record(encoded).foot_traffic == 40

    class Client:
        def __init__(self):
            self.calls = []

        def put_record(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    KinesisPublisher("events", client).publish(record)
    assert client.calls[0]["PartitionKey"] == "store-100"
    assert client.calls[0]["StreamName"] == "events"

    monkeypatch.setenv("REPLENISHMENT_DATA_DIR", str(settings.data_dir))
    payload = base64.b64encode(encoded).decode()
    result = ingest({"Records": [{"kinesis": {"data": payload}}]}, None)
    assert result == {"written": 1}
    assert demo_repo.list_signals("store-100")
