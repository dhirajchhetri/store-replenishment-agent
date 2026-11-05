from replenishment.catalog import AS_OF, SKUS, STORES
from replenishment.service import open_repo, persist_dataset
from replenishment.synthetic import generate


def test_generator_is_stable_and_covers_both_stores(settings):
    first = generate(seed=7, days=14)
    second = generate(seed=7, days=14)
    assert [event.event_id for event in first.events] == [event.event_id for event in second.events]
    stores = {event.store_id for event in first.events}
    assert stores == {store.store_id for store in STORES}
    assert first.as_of == AS_OF
    assert any(event.event_type == "pos_sale" for event in first.events)
    assert any(event.event_type == "rfid_count" for event in first.events)
    assert len(first.signals) == 14 * 13 * len(STORES)


def test_demo_snapshot_replaces_simulated_on_hand(settings):
    repo = open_repo(settings)
    persist_dataset(repo, generate(seed=7, days=3))
    milk = repo.get_inventory("store-100", "milk-1l")
    reserved = repo.get_inventory("store-100", "water-1500")
    assert milk is not None and milk.on_shelf == 1
    assert reserved is not None and reserved.reserved_units == reserved.backroom
    assert reserved.reserved_for_store == "store-200"
    assert len(repo.list_inventory("store-100")) == len(SKUS)
    assert repo.get_meta("as_of") == AS_OF.isoformat()
