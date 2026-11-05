import importlib.util
import json
from pathlib import Path

import pandas as pd

from replenishment.forecast import StockoutModel, train_frame
from replenishment.service import bootstrap, training_frame


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_training_holdout_is_finite_and_the_model_reloads(settings):
    metrics = bootstrap(settings, seed=7, days=14)
    assert metrics["n_train"] > metrics["n_holdout"] > 0
    assert 0 <= metrics["synthetic_holdout_wape"] < 1.5
    assert metrics["synthetic_holdout_naive_wape"] is not None
    frame = training_frame(__import__("replenishment.service", fromlist=["open_repo"]).open_repo(settings))
    assert set(frame.loc[frame["split"] == "holdout", "ts"].str.slice(0, 10)) == {"2026-11-02"}
    assert frame.loc[frame["split"] == "train", "ts"].str.slice(0, 10).max() < "2026-11-02"

    model = StockoutModel.load(settings.model_file, settings.metrics_file)
    holdout = frame[frame["split"] == "holdout"]
    predictions = model.predict_frame(holdout)
    assert len(predictions) == len(holdout)
    assert (predictions >= 0).all()

    train_script = _load(Path("sagemaker/train.py"), "sm_train")
    copied = settings.data_dir / "channel" / "training.csv"
    copied.parent.mkdir()
    frame.to_csv(copied, index=False)
    out = settings.data_dir / "sm-model"
    train_script.run(copied, out)
    reloaded = StockoutModel.load(out / "model.txt", out / "metrics.json")
    again = reloaded.predict_frame(holdout.head(3))
    assert len(again) == 3

    infer = _load(Path("sagemaker/inference.py"), "sm_infer")
    loaded = infer.model_fn(str(out))
    body = json.dumps({"instances": holdout.head(2).to_dict(orient="records")}).encode()
    payload = json.loads(infer.handle_invocation(loaded, body))
    assert len(payload["predictions"]) == 2
    assert all(value >= 0 for value in payload["predictions"])


def test_train_frame_rejects_a_missing_split():
    import pytest

    with pytest.raises(ValueError):
        train_frame(pd.DataFrame({"target": [1.0]}))
