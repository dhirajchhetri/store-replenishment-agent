from replenishment.domain import InboundRecord
from replenishment.handlers import decide, guardrail, save, score
from replenishment.service import bootstrap


def test_step_handlers_follow_the_state_machine(settings, monkeypatch):
    bootstrap(settings, seed=7, days=14)
    monkeypatch.setenv("REPLENISHMENT_DATA_DIR", str(settings.data_dir))
    scored = score({"store_id": "store-100"}, None)
    assert scored["forecasts"]
    state = {"store_id": "store-100", "score": scored}
    drafted = decide(state, None)
    state["decide"] = drafted
    checked = guardrail(state, None)
    state["guardrail"] = checked
    saved = save(state, None)
    assert saved["status"] == "pending_approval"
    assert saved["created"] is True


def test_score_requires_a_model(demo_repo, settings, monkeypatch):
    del demo_repo
    monkeypatch.setenv("REPLENISHMENT_DATA_DIR", str(settings.data_dir))
    import pytest

    from replenishment.errors import NotReadyError

    with pytest.raises(NotReadyError):
        score({"store_id": "store-100"}, None)


def test_endpoint_forecaster_uses_the_client(monkeypatch):
    monkeypatch.setenv("REPLENISHMENT_ENDPOINT_NAME", "stockout")
    monkeypatch.setenv("REPLENISHMENT_RESIDUAL_STD", "1.25")
    monkeypatch.setenv("REPLENISHMENT_DATA_DIR", "/tmp/replenishment-endpoint-test")

    class Body:
        def read(self):
            return b'{"predictions": [1.5, 2.5]}'

    class Client:
        def invoke_endpoint(self, **kwargs):
            Client.kwargs = kwargs
            return {"Body": Body()}

    import pandas as pd

    from replenishment.features import FEATURE_COLUMNS
    from replenishment.forecast import SageMakerEndpointForecaster

    frame = pd.DataFrame([{column: 0 for column in FEATURE_COLUMNS}, {column: 1 for column in FEATURE_COLUMNS}])
    model = SageMakerEndpointForecaster("stockout", 1.25, Client())
    assert model.predict_frame(frame).tolist() == [1.5, 2.5]
    assert Client.kwargs["EndpointName"] == "stockout"


def test_record_encode_used():
    # Keeps the domain import honest if the handler test above is trimmed.
    assert InboundRecord(kind="signal", store_id="store-100", ts="2026-11-02T15:00:00+00:00")
