"""Accept POS, RFID, and signal records into the document store.

The velocity counter is what the planner's shelf reader uses. Training still
reads the raw events, which is the durable copy.
"""

from __future__ import annotations

import json
from datetime import datetime

from replenishment.domain import InboundRecord, ShelfEvent, Signal
from replenishment.repository import ReplenishmentRepo


def encode_record(record: InboundRecord) -> bytes:
    return record.model_dump_json().encode()


def decode_record(payload: bytes | str) -> InboundRecord:
    if isinstance(payload, bytes):
        payload = payload.decode()
    return InboundRecord.model_validate(json.loads(payload))


def parse_iot_rfid(body: dict) -> InboundRecord:
    """Map a shelf-reader payload onto the stream record.

    Expected fields: store_id, shelf_id, sku, on_shelf, ts.
    """

    return InboundRecord(
        kind="rfid_count",
        store_id=body["store_id"],
        shelf_id=body["shelf_id"],
        sku=body["sku"],
        quantity=float(body["on_shelf"]),
        ts=body["ts"] if isinstance(body["ts"], datetime) else datetime.fromisoformat(body["ts"]),
    )


def ingest_record(repo: ReplenishmentRepo, record: InboundRecord) -> str:
    """Store one record. Returns the kind that was written."""

    if record.kind == "signal":
        repo.upsert_signal(
            Signal(
                store_id=record.store_id,
                ts=record.ts,
                foot_traffic=record.foot_traffic,
                temp_c=record.temp_c,
                precip_mm=record.precip_mm,
                promo_skus=list(record.promo_skus),
            )
        )
        return "signal"
    event = ShelfEvent(
        event_id=f"{record.store_id}|{record.sku}|{record.kind}|{record.ts.isoformat()}",
        store_id=record.store_id,
        shelf_id=record.shelf_id,
        sku=record.sku,
        event_type=record.kind,
        ts=record.ts,
        quantity=record.quantity,
    )
    inserted = repo.insert_event(event)
    if record.kind == "pos_sale" and inserted:
        bucket = record.ts.replace(minute=0, second=0, microsecond=0).isoformat()
        repo.add_velocity(record.store_id, record.shelf_id, record.sku, bucket, record.quantity)
    if record.kind == "rfid_count" and inserted:
        current = repo.get_inventory(record.store_id, record.sku)
        if current is not None:
            updated = current.model_copy(update={"on_shelf": int(record.quantity), "updated_at": record.ts})
            repo.upsert_inventory(updated)
    return record.kind


class KinesisPublisher:
    """Put one JSON record on a Kinesis stream. Partition by store."""

    def __init__(self, stream_name: str, client) -> None:
        self.stream_name = stream_name
        self.client = client

    def publish(self, record: InboundRecord) -> None:
        self.client.put_record(
            StreamName=self.stream_name,
            Data=encode_record(record),
            PartitionKey=record.store_id,
        )
