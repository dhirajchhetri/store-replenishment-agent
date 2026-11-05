from replenishment.routing import HashRing


def test_same_store_keeps_its_region():
    first = HashRing(vnodes=32)
    second = HashRing(vnodes=32)
    stores = [f"store-{index}" for index in range(200)]
    assert [first.owner(store) for store in stores] == [second.owner(store) for store in stores]
    counts = {region: 0 for region in first.regions}
    for store in stores:
        counts[first.owner(store)] += 1
    assert all(30 <= count <= 80 for count in counts.values())


def test_unhealthy_region_moves_only_its_own_stores():
    ring = HashRing()
    stores = [f"store-{index}" for index in range(80)]
    before = {store: ring.owner(store) for store in stores}
    ring.set_health("east", healthy=False)
    after = {store: ring.owner(store) for store in stores}
    assert all(owner != "east" for owner in after.values())
    for store, owner in before.items():
        if owner != "east":
            assert after[store] == owner
        else:
            assert after[store] != "east"
    ring.set_health("east", healthy=True)
    assert {store: ring.owner(store) for store in stores} == before
