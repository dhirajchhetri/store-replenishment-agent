"""HTTP API for forecasts, runs, and associate approval."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from replenishment.agent.planner import run_agent
from replenishment.config import Settings
from replenishment.errors import ConflictError, NotReadyError, PlanValidationError
from replenishment.service import load_forecaster, open_repo
from replenishment.workflow import approve, run_ml, score_store


class RunRequest(BaseModel):
    mode: str = Field(pattern="^(ml|agent)$")
    run_id: str | None = None


class ApprovalRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject|reduce)$")
    lines: list[dict] | None = None


class EventRequest(BaseModel):
    kind: str
    store_id: str
    ts: str
    shelf_id: str = ""
    sku: str = ""
    quantity: float = 0
    foot_traffic: float = 0
    temp_c: float = 0
    precip_mm: float = 0
    promo_skus: list[str] = Field(default_factory=list)


def create_app(settings: Settings | None = None, forecaster=None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Store-floor replenishment", version="0.1.0")
    app.state.settings = settings
    app.state.repo = open_repo(settings)
    app.state.forecaster = forecaster if forecaster is not None else load_forecaster(settings)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "llm": settings.llm_mode,
            "backend": settings.backend,
            "model_loaded": app.state.forecaster is not None,
        }

    @app.get("/v1/model")
    def model_metrics() -> dict:
        if not settings.metrics_file.exists():
            raise HTTPException(status_code=404, detail="no metrics file")
        import json

        return json.loads(settings.metrics_file.read_text())

    @app.post("/v1/events")
    def post_event(body: EventRequest) -> dict:
        from datetime import datetime

        from replenishment.domain import InboundRecord
        from replenishment.ingest import ingest_record

        record = InboundRecord(
            kind=body.kind,
            store_id=body.store_id,
            ts=datetime.fromisoformat(body.ts),
            shelf_id=body.shelf_id,
            sku=body.sku,
            quantity=body.quantity,
            foot_traffic=body.foot_traffic,
            temp_c=body.temp_c,
            precip_mm=body.precip_mm,
            promo_skus=body.promo_skus,
        )
        kind = ingest_record(app.state.repo, record)
        return {"stored": kind}

    @app.get("/v1/stores/{store_id}/forecasts")
    def forecasts(store_id: str) -> dict:
        _require_store(app, store_id)
        _require_model(app)
        rows = score_store(app.state.repo, app.state.forecaster, settings, store_id)
        return {"store_id": store_id, "forecasts": rows}

    @app.post("/v1/stores/{store_id}/runs")
    def create_run(store_id: str, body: RunRequest):
        _require_store(app, store_id)
        _require_model(app)
        try:
            if body.mode == "ml":
                pick, trace = run_ml(app.state.repo, app.state.forecaster, settings, store_id, body.run_id)
            else:
                pick, trace = run_agent(app.state.repo, app.state.forecaster, settings, store_id, body.run_id)
        except PlanValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except NotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"trace": trace, "pick_list": pick}

    @app.get("/v1/stores/{store_id}/pick-lists")
    def list_lists(store_id: str) -> dict:
        _require_store(app, store_id)
        return {"pick_lists": app.state.repo.list_pick_lists(store_id)}

    @app.get("/v1/stores/{store_id}/pick-lists/{run_id}")
    def get_list(store_id: str, run_id: str):
        pick = app.state.repo.get_pick_list(store_id, run_id)
        if pick is None:
            raise HTTPException(status_code=404, detail="pick list not found")
        return pick

    @app.post("/v1/stores/{store_id}/pick-lists/{run_id}/approval")
    def post_approval(store_id: str, run_id: str, body: ApprovalRequest):
        try:
            return approve(
                app.state.repo,
                settings,
                store_id,
                run_id,
                body.action,
                body.lines,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="pick list not found") from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def _require_store(app: FastAPI, store_id: str) -> None:
    if app.state.repo.get_store(store_id) is None:
        raise HTTPException(status_code=404, detail="store not found")


def _require_model(app: FastAPI) -> None:
    if app.state.forecaster is None:
        raise HTTPException(status_code=409, detail="no forecaster is loaded")
