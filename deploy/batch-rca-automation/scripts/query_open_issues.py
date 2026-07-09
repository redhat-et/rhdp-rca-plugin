"""Query historical results from the results table grouped by root cause category."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.sql
from dotenv import load_dotenv


def load_config() -> dict[str, Any]:
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file)

    config = {
        "host": os.environ.get("SOURCE_DB_HOST", "localhost"),
        "port": int(os.environ.get("SOURCE_DB_PORT", "5432")),
        "name": os.environ.get("SOURCE_DB_NAME", ""),
        "user": os.environ.get("SOURCE_DB_USER", ""),
        "password": os.environ.get("SOURCE_DB_PASSWORD", ""),
        "results_table": os.environ.get("SOURCE_DB_RESULT_TABLE", ""),
    }

    errors = []
    required_keys = {
        "name": "SOURCE_DB_NAME",
        "user": "SOURCE_DB_USER",
        "password": "SOURCE_DB_PASSWORD",
        "results_table": "SOURCE_DB_RESULT_TABLE",
    }
    for key, env_var in required_keys.items():
        if not config[key]:
            errors.append(f"{env_var} is required")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)

    return config


def connect_db(config: dict[str, Any]) -> Any:
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["name"],
        user=config["user"],
        password=config["password"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def query_historical_results(conn: Any, table: str, lookback_hours: int = 4, limit: int = 50) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_batch_id = f"batch_{cutoff.strftime('%Y%m%d_%H%M%S')}"
    with conn.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                """SELECT id, job_id, root_cause_category, root_cause_summary,
                          confidence, catalog_item, batch_id
                   FROM {}
                   WHERE batch_id >= %s
                   ORDER BY batch_id DESC"""
            ).format(psycopg2.sql.Identifier(table)),
            (cutoff_batch_id,),
        )
        rows = cur.fetchall()

    if not rows:
        return []

    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row["root_cause_category"], row["catalog_item"])
        if key not in groups:
            groups[key] = {
                "root_cause_category": row["root_cause_category"],
                "catalog_item": row["catalog_item"],
                "occurrence_count": 0,
                "job_ids": [],
                "results": [],
                "last_seen_batch": row["batch_id"],
            }
        groups[key]["occurrence_count"] += 1
        groups[key]["job_ids"].append(row["job_id"])
        groups[key]["results"].append({
            "id": row["id"],
            "job_id": row["job_id"],
            "root_cause_summary": row["root_cause_summary"],
            "confidence": row["confidence"],
        })

    result = sorted(groups.values(), key=lambda g: g["occurrence_count"], reverse=True)
    return result[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query historical results from results table")
    parser.add_argument(
        "--lookback", type=int, default=4,
        help="Hours to look back for historical results (default: 4)"
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Maximum number of grouped issues to return (default: 50)"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except SystemExit:
        return 1

    try:
        conn = connect_db(config)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    result = query_historical_results(conn, config["results_table"], lookback_hours=args.lookback, limit=args.limit)
    conn.close()
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
