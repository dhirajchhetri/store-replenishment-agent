"""Runtime settings.

Local runs read a SQLite file under ``data_dir``. AWS Lambda sets
``REPLENISHMENT_BACKEND=dynamodb`` and ``REPLENISHMENT_TABLE``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Knobs for a process. Policy numbers are defaults, not measured service levels."""

    data_dir: Path
    llm_mode: str = "stub"
    bedrock_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    aws_region: str = "us-east-1"
    backend: str = "local"
    table_name: str = ""
    endpoint_name: str = ""
    residual_std: float | None = None
    stale_after_minutes: int = 360
    safety_z: float = 1.65
    max_substitute_attempts: int = 3
    open_hour: int = 8
    close_hour: int = 21
    max_tool_calls: int = 40

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        if self.llm_mode not in {"stub", "bedrock"}:
            raise ValueError("llm_mode must be 'stub' or 'bedrock'")
        if self.backend not in {"local", "dynamodb"}:
            raise ValueError("backend must be 'local' or 'dynamodb'")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "replenishment.db"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "model"

    @property
    def model_file(self) -> Path:
        return self.model_dir / "model.txt"

    @property
    def metrics_file(self) -> Path:
        return self.model_dir / "metrics.json"

    @property
    def features_dir(self) -> Path:
        return self.data_dir / "features"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("REPLENISHMENT_DATA_DIR", "data"))
        residual = os.environ.get("REPLENISHMENT_RESIDUAL_STD")
        return cls(
            data_dir=data_dir,
            llm_mode=os.environ.get("REPLENISHMENT_LLM", "stub"),
            bedrock_model_id=os.environ.get(
                "REPLENISHMENT_BEDROCK_MODEL_ID",
                "anthropic.claude-3-5-haiku-20241022-v1:0",
            ),
            aws_region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
            backend=os.environ.get("REPLENISHMENT_BACKEND", "local"),
            table_name=os.environ.get("REPLENISHMENT_TABLE", ""),
            endpoint_name=os.environ.get("REPLENISHMENT_ENDPOINT_NAME", ""),
            residual_std=float(residual) if residual else None,
            stale_after_minutes=int(os.environ.get("REPLENISHMENT_STALE_MINUTES", "360")),
            safety_z=float(os.environ.get("REPLENISHMENT_SAFETY_Z", "1.65")),
            max_substitute_attempts=int(os.environ.get("REPLENISHMENT_MAX_SUBSTITUTES", "3")),
            max_tool_calls=int(os.environ.get("REPLENISHMENT_MAX_TOOL_CALLS", "40")),
        )
