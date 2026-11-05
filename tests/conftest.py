from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from replenishment.catalog import AS_OF
from replenishment.config import Settings
from replenishment.service import open_repo, seed_demo


class ConstantForecaster:
    """Point forecast that ignores the feature row. Used to lock the rules."""

    def __init__(self, value: float = 8.0, residual_std: float = 1.0) -> None:
        self.value = value
        self.residual_std = residual_std

    def predict_frame(self, frame) -> np.ndarray:
        return np.full(len(frame), self.value, dtype=float)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture
def demo_repo(settings: Settings):
    repo = open_repo(settings)
    seed_demo(repo, AS_OF)
    return repo
