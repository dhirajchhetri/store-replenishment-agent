"""LightGBM demand model and a SageMaker endpoint adapter.

Metrics written next to the model are for the synthetic holdout only.
They are not a store-operations result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd

from replenishment.features import FEATURE_COLUMNS


class Forecaster(Protocol):
    residual_std: float

    def predict_frame(self, frame: pd.DataFrame) -> np.ndarray:
        """Return one non-negative unit forecast per row."""


@dataclass
class StockoutModel:
    booster: lgb.Booster
    residual_std: float
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS

    def predict_frame(self, frame: pd.DataFrame) -> np.ndarray:
        matrix = frame.loc[:, list(self.feature_columns)]
        raw = self.booster.predict(matrix)
        return np.clip(np.asarray(raw, dtype=float), 0, None)

    def save(self, model_file: Path, metrics_file: Path, metrics: dict) -> None:
        model_file.parent.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(model_file))
        payload = dict(metrics)
        payload["residual_std"] = self.residual_std
        payload["feature_columns"] = list(self.feature_columns)
        payload["note"] = (
            "Holdout figures are computed on the synthetic series only. "
            "They are not a measure of performance in a store."
        )
        metrics_file.write_text(json.dumps(payload, indent=2) + "\n")

    @classmethod
    def load(cls, model_file: Path, metrics_file: Path | None = None) -> StockoutModel:
        booster = lgb.Booster(model_file=str(model_file))
        residual = 1.0
        if metrics_file and metrics_file.exists():
            payload = json.loads(metrics_file.read_text())
            residual = float(payload.get("residual_std", 1.0))
        return cls(booster=booster, residual_std=residual)


@dataclass
class SageMakerEndpointForecaster:
    """Scores a feature frame by calling a SageMaker endpoint.

    ``residual_std`` is the training residual stored beside the model. The
    endpoint returns point forecasts; safety stock still uses this residual.
    """

    endpoint_name: str
    residual_std: float
    client: object

    def predict_frame(self, frame: pd.DataFrame) -> np.ndarray:
        records = frame.loc[:, list(FEATURE_COLUMNS)].to_dict(orient="records")
        response = self.client.invoke_endpoint(
            EndpointName=self.endpoint_name,
            ContentType="application/json",
            Body=json.dumps({"instances": records}).encode(),
        )
        body = response["Body"].read()
        payload = json.loads(body)
        predictions = payload["predictions"]
        return np.clip(np.asarray(predictions, dtype=float), 0, None)


def wape(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    denom = float(np.abs(actual).sum())
    if denom == 0:
        return None
    return float(np.abs(actual - predicted).sum() / denom)


def train_frame(frame: pd.DataFrame, *, seed: int = 7) -> tuple[StockoutModel, dict]:
    """Fit on rows where ``split == train`` and score ``split == holdout``."""

    missing = [column for column in FEATURE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"training frame is missing {missing}")
    if "target" not in frame.columns or "split" not in frame.columns:
        raise ValueError("training frame needs target and split columns")
    train = frame[frame["split"] == "train"]
    holdout = frame[frame["split"] == "holdout"]
    if train.empty or holdout.empty:
        raise ValueError("training frame needs both train and holdout rows")

    model = lgb.LGBMRegressor(
        n_estimators=80,
        learning_rate=0.08,
        num_leaves=15,
        min_child_samples=10,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=seed,
        verbosity=-1,
        force_col_wise=True,
    )
    model.fit(train.loc[:, list(FEATURE_COLUMNS)], train["target"])
    booster = model.booster_
    train_pred = np.clip(booster.predict(train.loc[:, list(FEATURE_COLUMNS)]), 0, None)
    holdout_pred = np.clip(booster.predict(holdout.loc[:, list(FEATURE_COLUMNS)]), 0, None)
    residual = float(np.std(train["target"].to_numpy() - train_pred))
    residual = max(residual, 0.25)
    naive = float(train["target"].mean())
    naive_pred = np.full(len(holdout), max(naive, 0.0))
    actual = holdout["target"].to_numpy(dtype=float)
    metrics = {
        "library": "lightgbm",
        "n_train": int(len(train)),
        "n_holdout": int(len(holdout)),
        "synthetic_holdout_wape": wape(actual, holdout_pred),
        "synthetic_holdout_naive_wape": wape(actual, naive_pred),
        "train_residual_std": residual,
    }
    return StockoutModel(booster=booster, residual_std=residual), metrics


def write_feature_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["split", "store_id", "sku", "shelf_id", "ts", *FEATURE_COLUMNS, "target"]
    frame.loc[:, columns].to_csv(path, index=False)
