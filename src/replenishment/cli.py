"""Command line for the offline walkthrough."""

from __future__ import annotations

import argparse
import json

from replenishment.config import Settings
from replenishment.service import bootstrap, load_runtime, open_repo, train_repo
from replenishment.synthetic import generate
from replenishment.workflow import approve, run_ml, score_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replenish")
    parser.add_argument("--data-dir", default=None, help="Defaults to REPLENISHMENT_DATA_DIR or ./data")
    sub = parser.add_subparsers(dest="cmd", required=True)

    boot = sub.add_parser("bootstrap", help="Generate synthetic history and train the model")
    boot.add_argument("--seed", type=int, default=7)
    boot.add_argument("--days", type=int, default=14)

    generate_cmd = sub.add_parser("generate", help="Write synthetic history without training")
    generate_cmd.add_argument("--seed", type=int, default=7)
    generate_cmd.add_argument("--days", type=int, default=14)

    sub.add_parser("train", help="Train from history already in the local store")

    score = sub.add_parser("score", help="Print tonight's forecast for one store")
    score.add_argument("--store", default="store-100")

    run = sub.add_parser("run", help="Draft a pending pick list")
    run.add_argument("--store", default="store-100")
    run.add_argument("--mode", choices=("ml", "agent"), default="ml")
    run.add_argument("--run-id", default=None)
    run.add_argument("--approve", action="store_true", help="Publish the draft after it is saved")

    serve = sub.add_parser("serve", help="Run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--bootstrap", action="store_true", help="Generate and train if no model file exists")

    decision = sub.add_parser("approve", help="Approve, reduce, or reject a pending pick list")
    decision.add_argument("--store", required=True)
    decision.add_argument("--run-id", required=True)
    decision.add_argument("--action", choices=("approve", "reject", "reduce"), required=True)

    args = parser.parse_args(argv)
    settings = _settings(args.data_dir)

    if args.cmd == "bootstrap":
        metrics = bootstrap(settings, seed=args.seed, days=args.days)
        _print_metrics(metrics)
        return 0
    if args.cmd == "generate":
        from replenishment.service import persist_dataset

        persist_dataset(open_repo(settings), generate(seed=args.seed, days=args.days))
        print(f"wrote history to {settings.database_path}")
        return 0
    if args.cmd == "train":
        metrics = train_repo(open_repo(settings), settings)
        _print_metrics(metrics)
        return 0
    if args.cmd == "score":
        settings, repo, forecaster = load_runtime(settings)
        if forecaster is None:
            raise SystemExit("no model file; run bootstrap first")
        rows = score_store(repo, forecaster, settings, args.store)
        print(json.dumps(rows, indent=2))
        return 0
    if args.cmd == "run":
        settings, repo, forecaster = load_runtime(settings)
        if forecaster is None:
            raise SystemExit("no model file; run bootstrap first")
        if args.mode == "ml":
            pick, trace = run_ml(repo, forecaster, settings, args.store, args.run_id)
        else:
            from replenishment.agent.planner import run_agent

            pick, trace = run_agent(repo, forecaster, settings, args.store, args.run_id)
        if args.approve and pick.status == "pending_approval":
            pick = approve(repo, settings, pick.store_id, pick.run_id, "approve")
        print(json.dumps({"trace": trace, "pick_list": pick.model_dump(mode="json")}, indent=2))
        return 0
    if args.cmd == "approve":
        settings, repo, _forecaster = load_runtime(settings)
        pick = approve(repo, settings, args.store, args.run_id, args.action)
        print(pick.model_dump_json(indent=2))
        return 0
    if args.cmd == "serve":
        if args.bootstrap and not settings.model_file.exists():
            bootstrap(settings)
        import uvicorn

        from replenishment.api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0
    raise SystemExit(f"unknown command {args.cmd}")


def _settings(data_dir: str | None) -> Settings:
    settings = Settings.from_env()
    if data_dir:
        settings = Settings(
            data_dir=data_dir,
            llm_mode=settings.llm_mode,
            bedrock_model_id=settings.bedrock_model_id,
            aws_region=settings.aws_region,
            backend=settings.backend,
            table_name=settings.table_name,
            endpoint_name=settings.endpoint_name,
            residual_std=settings.residual_std,
            stale_after_minutes=settings.stale_after_minutes,
            safety_z=settings.safety_z,
            max_substitute_attempts=settings.max_substitute_attempts,
            open_hour=settings.open_hour,
            close_hour=settings.close_hour,
            max_tool_calls=settings.max_tool_calls,
        )
    return settings


def _print_metrics(metrics: dict) -> None:
    print(json.dumps(metrics, indent=2))
    print("These figures are for the synthetic holdout only.")


if __name__ == "__main__":
    raise SystemExit(main())
