"""Lambda entry points used by the Step Functions definition.

Each handler reads ``REPLENISHMENT_*`` from the environment. Offline tests
call the same functions with that environment pointed at a local data dir.
"""

from __future__ import annotations

import base64

from replenishment.errors import NotReadyError
from replenishment.ingest import decode_record, ingest_record
from replenishment.service import load_runtime
from replenishment.workflow import (
    default_run_id,
    forecasts_from_rows,
    guardrail_lines,
    persist_pending,
    run_ml,
)


def score(event, _context=None) -> dict:
    settings, repo, forecaster = load_runtime()
    if forecaster is None:
        raise NotReadyError("no forecaster is loaded")
    store_id = event["store_id"]
    from replenishment.workflow import resolve_as_of, score_store

    as_of = resolve_as_of(repo)
    rows = score_store(repo, forecaster, settings, store_id, as_of)
    return {"store_id": store_id, "as_of": as_of.isoformat(), "forecasts": rows}


def decide(event, _context=None) -> dict:
    settings, repo, forecaster = load_runtime()
    if forecaster is None:
        raise NotReadyError("no forecaster is loaded")
    store_id = event["store_id"]
    scored = event.get("score", event)
    forecasts = forecasts_from_rows(scored["forecasts"])
    from datetime import datetime

    as_of = datetime.fromisoformat(scored["as_of"])
    from replenishment.rules import plan_positions

    lines, blocked = plan_positions(
        repo.list_inventory(store_id),
        forecasts,
        settings=settings,
        as_of=as_of,
        residual_std=forecaster.residual_std,
    )
    return {
        "store_id": store_id,
        "run_id": event.get("run_id"),
        "as_of": as_of.isoformat(),
        "lines": [line.model_dump(mode="json") for line in lines],
        "blocked": [block.model_dump(mode="json") for block in blocked],
    }


def guardrail(event, _context=None) -> dict:
    from replenishment.domain import BlockedLine, PickLine

    settings, repo, _forecaster = load_runtime()
    drafted = event.get("decide", event)
    from datetime import datetime

    as_of = datetime.fromisoformat(drafted["as_of"])
    lines = [PickLine.model_validate(line) for line in drafted["lines"]]
    blocked = [BlockedLine.model_validate(block) for block in drafted["blocked"]]
    kept, blocked = guardrail_lines(repo, settings, event["store_id"], lines, blocked, as_of)
    return {
        "store_id": event["store_id"],
        "run_id": event.get("run_id") or drafted.get("run_id"),
        "as_of": drafted["as_of"],
        "lines": [line.model_dump(mode="json") for line in kept],
        "blocked": [block.model_dump(mode="json") for block in blocked],
    }


def save(event, _context=None) -> dict:
    from replenishment.domain import BlockedLine, PickLine
    from replenishment.rules import explain_plan

    settings, repo, _forecaster = load_runtime()
    checked = event.get("guardrail", event)
    from datetime import datetime

    as_of = datetime.fromisoformat(checked["as_of"])
    lines = [PickLine.model_validate(line) for line in checked["lines"]]
    blocked = [BlockedLine.model_validate(block) for block in checked["blocked"]]
    run_id = event.get("run_id") or checked.get("run_id") or default_run_id(event["store_id"], as_of)
    explanation = explain_plan(event["store_id"], lines, blocked)
    saved, created = persist_pending(
        repo,
        settings,
        store_id=event["store_id"],
        run_id=run_id,
        source="ml",
        as_of=as_of,
        lines=lines,
        blocked=blocked,
        explanation=explanation,
    )
    body = saved.model_dump(mode="json")
    body["created"] = created
    return body


def run(event, _context=None) -> dict:
    """One-shot store run. The state machine calls the step handlers instead."""

    settings, repo, forecaster = load_runtime()
    pick, trace = run_ml(repo, forecaster, settings, event["store_id"], event.get("run_id"))
    return {"trace": trace, "pick_list": pick.model_dump(mode="json")}


def ingest(event, _context=None) -> dict:
    _settings, repo, _forecaster = load_runtime()
    written = 0
    for record in event.get("Records", []):
        raw = base64.b64decode(record["kinesis"]["data"])
        ingest_record(repo, decode_record(raw))
        written += 1
    return {"written": written}


def api(event, context=None):
    from mangum import Mangum

    from replenishment.api import create_app

    settings, _repo, _forecaster = load_runtime()
    return Mangum(create_app(settings))(event, context)
