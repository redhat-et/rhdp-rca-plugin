"""Surface MLflow trace cost for a batch RCA Claude invocation.

The Stop hook (mlflow.claude_code.hooks.stop_hook_handler) already logs a
trace for each headless `claude -p` run, and MLflow computes cost onto
trace.info.cost from the recorded token usage. That cost isn't logged
anywhere visible outside the trace detail view, so this script fetches the
trace just created for this invocation and re-logs its cost as a metric on
a tagged MLflow run, making it show up in run/metric views too.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Log the most recent Claude trace's cost to MLflow"
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)

    try:
        import mlflow
    except ImportError:
        print("[WARN] mlflow not installed, skipping cost logging", file=sys.stderr)
        return 0

    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "")
    client = mlflow.MlflowClient()
    exp = mlflow.get_experiment_by_name(experiment_name)
    if exp is None:
        print(
            "[WARN] MLFLOW_EXPERIMENT_NAME not set or experiment not found, skipping",
            file=sys.stderr,
        )
        return 0

    traces = client.search_traces(
        experiment_ids=[exp.experiment_id], order_by=["timestamp_ms DESC"], max_results=1
    )
    if not traces:
        print("[WARN] No traces found for this experiment, skipping cost logging", file=sys.stderr)
        return 0

    trace = traces[0]
    cost = getattr(trace.info, "cost", None)
    token_usage = getattr(trace.info, "token_usage", None) or {}

    if cost is None:
        print(f"[WARN] trace {trace.info.trace_id} has no cost recorded, skipping", file=sys.stderr)
        return 0

    with mlflow.start_run(run_name=args.batch_id):
        mlflow.set_tags(
            {
                "batch_id": args.batch_id,
                "model": args.model,
                "trace_id": trace.info.trace_id,
            }
        )
        mlflow.log_metric("cost_usd", float(cost))
        for key, value in token_usage.items():
            try:
                mlflow.log_metric(key, float(value))
            except (TypeError, ValueError):
                continue

    print(f"[INFO] Logged cost_usd={cost} for trace {trace.info.trace_id} to MLflow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
