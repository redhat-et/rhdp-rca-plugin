"""Query historical results narrowly filtered by (root_cause_category, catalog_item) pairs."""

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


def query_matches(
    conn: Any,
    table: str,
    pairs: list[dict[str, str]],
    exclude_batch: str | None = None,
    lookback_hours: int = 4,
    limit_per_group: int = 10,
) -> list[dict]:
    if not pairs:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_batch_id = f"batch_{cutoff.strftime('%Y%m%d_%H%M%S')}"

    tuple_values = []
    for p in pairs:
        tuple_values.extend([p["root_cause_category"], p["catalog_item"]])

    in_clause = ", ".join(["(%s, %s)"] * len(pairs))

    query = psycopg2.sql.SQL(
        "SELECT id, job_id, batch_id, root_cause_category, root_cause_summary,"
        " confidence, catalog_item"
        " FROM {}"
        " WHERE batch_id >= %s"
        " AND (root_cause_category, catalog_item) IN (" + in_clause + ")"
        " AND confidence IN ('high', 'medium')"
        " ORDER BY batch_id DESC"
    ).format(psycopg2.sql.Identifier(table))

    params: list[Any] = [cutoff_batch_id]
    if exclude_batch:
        query = psycopg2.sql.SQL(
            "SELECT id, job_id, batch_id, root_cause_category, root_cause_summary,"
            " confidence, catalog_item"
            " FROM {}"
            " WHERE batch_id >= %s AND batch_id != %s"
            " AND (root_cause_category, catalog_item) IN (" + in_clause + ")"
            " AND confidence IN ('high', 'medium')"
            " ORDER BY batch_id DESC"
        ).format(psycopg2.sql.Identifier(table))
        params = [cutoff_batch_id, exclude_batch]

    params.extend(tuple_values)

    with conn.cursor() as cur:
        cur.execute(query, params)
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
                "results": [],
            }
        if groups[key]["occurrence_count"] < limit_per_group:
            groups[key]["results"].append({
                "id": row["id"],
                "job_id": row["job_id"],
                "batch_id": row["batch_id"],
                "root_cause_summary": row["root_cause_summary"],
                "confidence": row["confidence"],
            })
        groups[key]["occurrence_count"] += 1

    return sorted(groups.values(), key=lambda g: g["occurrence_count"], reverse=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query historical results filtered by (root_cause_category, catalog_item) pairs"
    )
    parser.add_argument(
        "--input", metavar="FILE",
        help="JSON file with pairs to query (reads stdin if omitted)",
    )
    parser.add_argument(
        "--exclude-batch", metavar="BATCH_ID",
        help="Exclude this batch_id from results (avoid self-matching)",
    )
    parser.add_argument(
        "--lookback-hours", type=int, default=4,
        help="Hours to look back (default: 4)",
    )
    parser.add_argument(
        "--limit-per-group", type=int, default=10,
        help="Max results per (category, catalog_item) group (default: 10)",
    )
    args = parser.parse_args(argv)

    try:
        if args.input:
            with open(args.input) as f:
                pairs = json.load(f)
        else:
            pairs = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to read pairs input: {e}", file=sys.stderr)
        return 1

    if not isinstance(pairs, list) or not pairs:
        json.dump([], sys.stdout)
        print()
        return 0

    try:
        config = load_config()
    except SystemExit:
        return 1

    try:
        conn = connect_db(config)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    result = query_matches(
        conn,
        config["results_table"],
        pairs,
        exclude_batch=args.exclude_batch,
        lookback_hours=args.lookback_hours,
        limit_per_group=args.limit_per_group,
    )
    conn.close()
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
