import json
from pathlib import Path


def test_state_machine_stops_before_the_warehouse():
    path = Path("infra/step_functions/replenishment.asl.json")
    document = json.loads(path.read_text())
    assert document["StartAt"] == "ScoreStore"
    assert set(document["States"]) == {
        "ScoreStore",
        "DecideStore",
        "GuardrailStore",
        "SavePendingPickList",
        "SendToDlq",
    }
    assert "PublishWarehouse" not in document["States"]
