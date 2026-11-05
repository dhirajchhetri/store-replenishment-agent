"""SageMaker inference functions and a small local HTTP server.

``/ping`` and ``/invocations`` match the SageMaker container contract.
The server is only for a local container; it is not a store-facing API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from replenishment.features import FEATURE_COLUMNS
from replenishment.forecast import StockoutModel


def model_fn(model_dir: str):
    directory = Path(model_dir)
    return StockoutModel.load(directory / "model.txt", directory / "metrics.json")


def input_fn(body: bytes | str, content_type: str) -> pd.DataFrame:
    if content_type not in {"application/json", "application/jsonlines"}:
        raise ValueError(f"unsupported content type {content_type}")
    if isinstance(body, bytes):
        body = body.decode()
    payload = json.loads(body)
    rows = payload["instances"] if isinstance(payload, dict) else payload
    frame = pd.DataFrame(rows)
    missing = [column for column in FEATURE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"missing features: {missing}")
    return frame


def predict_fn(data: pd.DataFrame, model: StockoutModel) -> list[float]:
    return [float(value) for value in model.predict_frame(data)]


def output_fn(prediction: list[float], accept: str) -> tuple[str, str]:
    if accept not in {"application/json", "*/*"}:
        raise ValueError(f"unsupported accept {accept}")
    return json.dumps({"predictions": prediction}), "application/json"


def handle_invocation(model: StockoutModel, body: bytes) -> bytes:
    frame = input_fn(body, "application/json")
    payload, _content_type = output_fn(predict_fn(frame, model), "application/json")
    return payload.encode()
