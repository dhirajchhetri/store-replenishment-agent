from datetime import UTC, datetime

from botocore.exceptions import ClientError

from replenishment.catalog import AS_OF
from replenishment.domain import InventoryPosition, StoredPickList
from replenishment.repository import DynamoDocumentStore, ReplenishmentRepo
from replenishment.service import seed_demo
from replenishment.workflow import run_ml

UTC = UTC


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression == "attribute_not_exists(pk)" and key in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.items[key] = Item

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        values = kwargs["ExpressionAttributeValues"]
        pk = values[":pk"]
        prefix = values[":prefix"]
        items = [
            item for (item_pk, item_sk), item in self.items.items() if item_pk == pk and item_sk.startswith(prefix)
        ]
        return {"Items": items}

    def scan(self, **kwargs):
        sk = kwargs["ExpressionAttributeValues"][":sk"]
        items = [item for (_pk, item_sk), item in self.items.items() if item_sk == sk]
        return {"Items": items}


def test_pick_list_insert_is_idempotent(demo_repo, settings):
    from tests.conftest import ConstantForecaster

    forecaster = ConstantForecaster()
    first, trace = run_ml(demo_repo, forecaster, settings, "store-100", "run-1")
    second, again = run_ml(demo_repo, forecaster, settings, "store-100", "run-1")
    assert trace[-1] == "SavePendingPickList"
    assert again == ["ExistingPickList"]
    assert second.run_id == first.run_id
    assert second.explanation == first.explanation
    assert len(demo_repo.list_pick_lists("store-100")) == 1
    assert len(demo_repo.list_escalations("store-100")) == 1


def test_dynamo_document_store_round_trip():
    repo = ReplenishmentRepo(DynamoDocumentStore(FakeTable()))
    seed_demo(repo, AS_OF)
    position = repo.get_inventory("store-100", "milk-1l")
    assert position.on_shelf == 1
    position = InventoryPosition(
        store_id="store-200",
        sku="milk-1l",
        shelf_id="A1",
        on_shelf=3,
        backroom=4,
        shelf_capacity=24,
        reserved_units=0,
        updated_at=datetime(2026, 11, 2, 16, tzinfo=UTC),
    )
    repo.upsert_inventory(position)
    assert repo.get_inventory("store-200", "milk-1l").on_shelf == 3
    pick = StoredPickList(
        store_id="store-100",
        run_id="run-1",
        lines=[],
        blocked=[],
        explanation="Nothing to pick for this run.",
        source="ml",
        status="pending_approval",
        store_region="north",
        agent_region="north",
        as_of=AS_OF,
        created_at=AS_OF,
    )
    saved, created = repo.insert_pick_list(pick)
    assert created
    again, created_again = repo.insert_pick_list(pick)
    assert not created_again
    assert again.run_id == saved.run_id
