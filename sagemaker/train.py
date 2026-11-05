"""SageMaker training entrypoint.

The training channel is a CSV produced by the feature pipeline, with the
feature columns, a ``target`` column, and a ``split`` column. The script
writes ``model.txt`` and ``metrics.json`` to the model directory.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from replenishment.forecast import train_frame


def run(train_csv: Path, model_dir: Path, *, seed: int = 7) -> dict:
    frame = pd.read_csv(train_csv)
    model, metrics = train_frame(frame, seed=seed)
    model_dir.mkdir(parents=True, exist_ok=True)
    model.save(model_dir / "model.txt", model_dir / "metrics.json", metrics)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default=os.environ.get("SM_CHANNEL_TRAIN", ""))
    parser.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "model"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    source = Path(args.train)
    csv_path = source / "training.csv" if source.is_dir() else source
    metrics = run(csv_path, Path(args.model_dir), seed=args.seed)
    print(metrics)


if __name__ == "__main__":
    main()
