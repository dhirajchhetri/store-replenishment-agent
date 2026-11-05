"""Load data, train the local model, and open a runtime."""

from __future__ import annotations

import os

import pandas as pd

from replenishment.catalog import STORES, demo_inventory
from replenishment.config import Settings
from replenishment.features import iter_training_rows
from replenishment.forecast import (
    SageMakerEndpointForecaster,
    StockoutModel,
    train_frame,
    write_feature_csv,
)
from replenishment.repository import DynamoDocumentStore, ReplenishmentRepo, SqlDocumentStore
from replenishment.synthetic import SyntheticDataset, generate


def open_repo(settings: Settings) -> ReplenishmentRepo:
    if settings.backend == "dynamodb":
        if not settings.table_name:
            raise ValueError("REPLENISHMENT_TABLE is required for the dynamodb backend")
        import boto3

        table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.table_name)
        return ReplenishmentRepo(DynamoDocumentStore(table))
    return ReplenishmentRepo(SqlDocumentStore(settings.database_path))


def seed_demo(repo: ReplenishmentRepo, as_of) -> None:
    """Write the two sample stores and the fixed on-hand snapshot. No history."""

    for store in STORES:
        repo.upsert_store(store)
        for position in demo_inventory(store.store_id, as_of):
            repo.upsert_inventory(position)
    repo.set_meta("as_of", as_of.isoformat())


def persist_dataset(repo: ReplenishmentRepo, dataset: SyntheticDataset) -> None:
    """Store simulated history, then put the demo snapshot back on the shelves."""

    seed_demo(repo, dataset.as_of)
    for event in dataset.events:
        repo.insert_event(event)
        if event.event_type == "pos_sale":
            bucket = event.ts.replace(minute=0, second=0, microsecond=0).isoformat()
            repo.add_velocity(event.store_id, event.shelf_id, event.sku, bucket, event.quantity)
    for signal in dataset.signals:
        repo.upsert_signal(signal)
    repo.set_meta("seed", str(dataset.seed))
    repo.set_meta("days", str(dataset.days))


def training_frame(repo: ReplenishmentRepo) -> pd.DataFrame:
    from datetime import datetime

    raw = repo.get_meta("as_of")
    if not raw:
        raise ValueError("as_of is not set")
    as_of = datetime.fromisoformat(raw)
    rows: list[dict] = []
    for store in repo.list_stores():
        events = repo.list_events(store.store_id)
        if not events:
            continue
        signals = repo.list_signals(store.store_id)
        for row, label, moment, spec, split in iter_training_rows(
            events,
            signals,
            store_id=store.store_id,
            open_hour=store.open_hour,
            close_hour=store.close_hour,
            holdout_date=as_of.date(),
        ):
            rows.append(
                {
                    **row,
                    "target": label,
                    "split": split,
                    "store_id": store.store_id,
                    "sku": spec.sku,
                    "shelf_id": spec.shelf_id,
                    "ts": moment.isoformat(),
                }
            )
    if not rows:
        raise ValueError("no training rows")
    return pd.DataFrame(rows)


def train_repo(repo: ReplenishmentRepo, settings: Settings, *, seed: int = 7) -> dict:
    frame = training_frame(repo)
    model, metrics = train_frame(frame, seed=seed)
    model.save(settings.model_file, settings.metrics_file, metrics)
    write_feature_csv(frame, settings.features_dir / "training.csv")
    return metrics


def bootstrap(settings: Settings, *, seed: int = 7, days: int = 14) -> dict:
    repo = open_repo(settings)
    persist_dataset(repo, generate(seed=seed, days=days))
    return train_repo(repo, settings, seed=seed)


def load_forecaster(settings: Settings):
    if settings.endpoint_name:
        if settings.residual_std is None:
            from replenishment.errors import NotReadyError

            raise NotReadyError("REPLENISHMENT_RESIDUAL_STD is required with an endpoint")
        import boto3

        client = boto3.client("sagemaker-runtime", region_name=settings.aws_region)
        return SageMakerEndpointForecaster(
            endpoint_name=settings.endpoint_name,
            residual_std=settings.residual_std,
            client=client,
        )
    if settings.model_file.exists():
        return StockoutModel.load(settings.model_file, settings.metrics_file)
    return None


def load_runtime(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    # Endpoint name can also arrive only through the environment.
    if not settings.endpoint_name and os.environ.get("REPLENISHMENT_ENDPOINT_NAME"):
        settings = Settings.from_env()
    return settings, open_repo(settings), load_forecaster(settings)
