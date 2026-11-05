from fastapi.testclient import TestClient

from replenishment.api import create_app
from tests.conftest import ConstantForecaster


def test_health_run_and_approval(demo_repo, settings):
    del demo_repo
    app = create_app(settings, forecaster=ConstantForecaster())
    client = TestClient(app)
    assert client.get("/health").json()["model_loaded"] is True
    missing = client.post("/v1/stores/missing/runs", json={"mode": "ml"})
    assert missing.status_code == 404

    created = client.post("/v1/stores/store-100/runs", json={"mode": "agent", "run_id": "api-1"})
    assert created.status_code == 200
    body = created.json()
    assert body["pick_list"]["status"] == "pending_approval"
    assert body["pick_list"]["lines"]

    listed = client.get("/v1/stores/store-100/pick-lists")
    assert listed.status_code == 200
    assert len(listed.json()["pick_lists"]) == 1

    approved = client.post(
        "/v1/stores/store-100/pick-lists/api-1/approval",
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "published"
    assert app.state.repo.get_warehouse("store-100", "api-1") is not None

    forecasts = client.get("/v1/stores/store-100/forecasts")
    assert forecasts.status_code == 200
    assert len(forecasts.json()["forecasts"]) == 8


def test_event_ingest(demo_repo, settings):
    del demo_repo
    client = TestClient(create_app(settings, forecaster=ConstantForecaster()))
    response = client.post(
        "/v1/events",
        json={
            "kind": "pos_sale",
            "store_id": "store-100",
            "shelf_id": "A1",
            "sku": "milk-1l",
            "quantity": 2,
            "ts": "2026-11-02T15:00:00+00:00",
        },
    )
    assert response.status_code == 200
    assert response.json()["stored"] == "pos_sale"
