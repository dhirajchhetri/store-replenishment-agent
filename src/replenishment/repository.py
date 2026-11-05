"""Document store for inventory, events, and pick lists.

SQLite is the offline store. DynamoDB uses the same keys: a partition per
store and a sort key prefixed by record type. Bodies are JSON so the two
backends share one repository.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError

from replenishment.domain import (
    AuditRecord,
    Escalation,
    InventoryPosition,
    ShelfEvent,
    Signal,
    Store,
    StoredPickList,
    WarehouseOrder,
)


class DocumentStore(Protocol):
    def put_doc(self, pk: str, sk: str, doc: dict, *, only_if_absent: bool = False) -> bool:
        """Insert or replace. Return False when ``only_if_absent`` finds a row."""

    def get_doc(self, pk: str, sk: str) -> dict | None:
        """Return one document, or None."""

    def query_prefix(self, pk: str, prefix: str) -> list[tuple[str, dict]]:
        """Return ``(sort_key, document)`` pairs whose sort key starts with ``prefix``."""

    def scan_sk(self, sk: str) -> list[tuple[str, dict]]:
        """Return ``(partition_key, document)`` pairs with this exact sort key."""


class SqlDocumentStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docs ("
            "pk TEXT NOT NULL, sk TEXT NOT NULL, body TEXT NOT NULL, "
            "PRIMARY KEY (pk, sk))"
        )
        self._conn.commit()

    def put_doc(self, pk: str, sk: str, doc: dict, *, only_if_absent: bool = False) -> bool:
        body = json.dumps(doc)
        with self._lock:
            if only_if_absent:
                inserted = self._conn.execute(
                    "INSERT OR IGNORE INTO docs (pk, sk, body) VALUES (?, ?, ?)",
                    (pk, sk, body),
                )
                self._conn.commit()
                return inserted.rowcount == 1
            self._conn.execute(
                "INSERT INTO docs (pk, sk, body) VALUES (?, ?, ?) "
                "ON CONFLICT(pk, sk) DO UPDATE SET body = excluded.body",
                (pk, sk, body),
            )
            self._conn.commit()
            return True

    def get_doc(self, pk: str, sk: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM docs WHERE pk = ? AND sk = ?", (pk, sk)).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def query_prefix(self, pk: str, prefix: str) -> list[tuple[str, dict]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT sk, body FROM docs WHERE pk = ? AND sk LIKE ? ORDER BY sk",
                (pk, prefix + "%"),
            ).fetchall()
        return [(sk, json.loads(body)) for sk, body in rows]

    def scan_sk(self, sk: str) -> list[tuple[str, dict]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT pk, body FROM docs WHERE sk = ? ORDER BY pk",
                (sk,),
            ).fetchall()
        return [(pk, json.loads(body)) for pk, body in rows]


class DynamoDocumentStore:
    """One DynamoDB table. Items are ``pk``, ``sk``, and a JSON ``body``."""

    def __init__(self, table) -> None:
        self.table = table

    def put_doc(self, pk: str, sk: str, doc: dict, *, only_if_absent: bool = False) -> bool:
        item = {"pk": pk, "sk": sk, "body": json.dumps(doc)}
        kwargs = {"Item": item}
        if only_if_absent:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            self.table.put_item(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def get_doc(self, pk: str, sk: str) -> dict | None:
        response = self.table.get_item(Key={"pk": pk, "sk": sk})
        item = response.get("Item")
        if not item:
            return None
        return json.loads(item["body"])

    def query_prefix(self, pk: str, prefix: str) -> list[tuple[str, dict]]:
        response = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={":pk": pk, ":prefix": prefix},
        )
        rows = [(item["sk"], json.loads(item["body"])) for item in response.get("Items", [])]
        rows.sort(key=lambda item: item[0])
        return rows

    def scan_sk(self, sk: str) -> list[tuple[str, dict]]:
        response = self.table.scan(
            FilterExpression="sk = :sk",
            ExpressionAttributeValues={":sk": sk},
        )
        rows = [(item["pk"], json.loads(item["body"])) for item in response.get("Items", [])]
        rows.sort(key=lambda item: item[0])
        return rows


def store_pk(store_id: str) -> str:
    return f"store#{store_id}"


class ReplenishmentRepo:
    def __init__(self, docs: DocumentStore) -> None:
        self.docs = docs

    def upsert_store(self, store: Store) -> None:
        self.docs.put_doc(store_pk(store.store_id), "store", store.model_dump(mode="json"))

    def list_stores(self) -> list[Store]:
        return [Store.model_validate(doc) for _, doc in self.docs.scan_sk("store")]

    def get_store(self, store_id: str) -> Store | None:
        doc = self.docs.get_doc(store_pk(store_id), "store")
        return Store.model_validate(doc) if doc else None

    def set_meta(self, key: str, value: str) -> None:
        self.docs.put_doc("meta", key, {"value": value})

    def get_meta(self, key: str) -> str | None:
        doc = self.docs.get_doc("meta", key)
        if doc is None:
            return None
        return str(doc["value"])

    def insert_event(self, event: ShelfEvent) -> bool:
        sk = f"evt#{event.ts.isoformat()}#{event.event_id}"
        return self.docs.put_doc(
            store_pk(event.store_id),
            sk,
            event.model_dump(mode="json"),
            only_if_absent=True,
        )

    def list_events(self, store_id: str) -> list[ShelfEvent]:
        rows = self.docs.query_prefix(store_pk(store_id), "evt#")
        return [ShelfEvent.model_validate(doc) for _, doc in rows]

    def upsert_signal(self, signal: Signal) -> None:
        self.docs.put_doc(
            store_pk(signal.store_id),
            f"sig#{signal.ts.isoformat()}",
            signal.model_dump(mode="json"),
        )

    def list_signals(self, store_id: str) -> list[Signal]:
        rows = self.docs.query_prefix(store_pk(store_id), "sig#")
        return [Signal.model_validate(doc) for _, doc in rows]

    def upsert_inventory(self, position: InventoryPosition) -> None:
        self.docs.put_doc(
            store_pk(position.store_id),
            f"inv#{position.sku}",
            position.model_dump(mode="json"),
        )

    def get_inventory(self, store_id: str, sku: str) -> InventoryPosition | None:
        doc = self.docs.get_doc(store_pk(store_id), f"inv#{sku}")
        return InventoryPosition.model_validate(doc) if doc else None

    def list_inventory(self, store_id: str) -> list[InventoryPosition]:
        rows = self.docs.query_prefix(store_pk(store_id), "inv#")
        positions = [InventoryPosition.model_validate(doc) for _, doc in rows]
        positions.sort(key=lambda position: (position.shelf_id, position.sku))
        return positions

    def add_velocity(self, store_id: str, shelf_id: str, sku: str, bucket: str, units: float) -> None:
        sk = f"vel#{bucket}#{shelf_id}#{sku}"
        pk = store_pk(store_id)
        current = self.docs.get_doc(pk, sk) or {"units": 0.0}
        current["units"] = float(current["units"]) + float(units)
        self.docs.put_doc(pk, sk, current)

    def list_velocity(self, store_id: str) -> list[dict]:
        rows = []
        for sk, doc in self.docs.query_prefix(store_pk(store_id), "vel#"):
            _, bucket, shelf_id, sku = sk.split("#", 3)
            rows.append(
                {
                    "bucket": bucket,
                    "shelf_id": shelf_id,
                    "sku": sku,
                    "units": float(doc["units"]),
                }
            )
        return rows

    def insert_pick_list(self, pick: StoredPickList) -> tuple[StoredPickList, bool]:
        created = self.docs.put_doc(
            store_pk(pick.store_id),
            f"pick#{pick.run_id}",
            pick.model_dump(mode="json"),
            only_if_absent=True,
        )
        if created:
            return pick, True
        existing = self.get_pick_list(pick.store_id, pick.run_id)
        if existing is None:
            raise RuntimeError("pick list insert lost a race and the row is missing")
        return existing, False

    def update_pick_list(self, pick: StoredPickList) -> None:
        self.docs.put_doc(
            store_pk(pick.store_id),
            f"pick#{pick.run_id}",
            pick.model_dump(mode="json"),
        )

    def get_pick_list(self, store_id: str, run_id: str) -> StoredPickList | None:
        doc = self.docs.get_doc(store_pk(store_id), f"pick#{run_id}")
        return StoredPickList.model_validate(doc) if doc else None

    def list_pick_lists(self, store_id: str) -> list[StoredPickList]:
        rows = self.docs.query_prefix(store_pk(store_id), "pick#")
        return [StoredPickList.model_validate(doc) for _, doc in rows]

    def add_audit(self, record: AuditRecord) -> None:
        self.docs.put_doc(
            store_pk(record.store_id),
            f"aud#{record.audit_id}",
            record.model_dump(mode="json"),
            only_if_absent=True,
        )

    def list_audit(self, store_id: str) -> list[AuditRecord]:
        rows = self.docs.query_prefix(store_pk(store_id), "aud#")
        return [AuditRecord.model_validate(doc) for _, doc in rows]

    def insert_warehouse(self, order: WarehouseOrder) -> tuple[WarehouseOrder, bool]:
        created = self.docs.put_doc(
            store_pk(order.store_id),
            f"wh#{order.run_id}",
            order.model_dump(mode="json"),
            only_if_absent=True,
        )
        if created:
            return order, True
        existing = self.get_warehouse(order.store_id, order.run_id)
        if existing is None:
            raise RuntimeError("warehouse insert lost a race and the row is missing")
        return existing, False

    def get_warehouse(self, store_id: str, run_id: str) -> WarehouseOrder | None:
        doc = self.docs.get_doc(store_pk(store_id), f"wh#{run_id}")
        return WarehouseOrder.model_validate(doc) if doc else None

    def add_escalation(self, record: Escalation) -> None:
        self.docs.put_doc(
            store_pk(record.store_id),
            f"esc#{record.escalation_id}",
            record.model_dump(mode="json"),
            only_if_absent=True,
        )

    def list_escalations(self, store_id: str) -> list[Escalation]:
        rows = self.docs.query_prefix(store_pk(store_id), "esc#")
        return [Escalation.model_validate(doc) for _, doc in rows]
