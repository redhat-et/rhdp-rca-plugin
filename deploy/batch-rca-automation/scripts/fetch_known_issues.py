"""Fetch recent high-confidence results from aap2_job_results for agent context."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.sql
from utils import connect_db, known_issue_active_sql, load_config


def fetch_known_issues(
    conn: Any, results_table: str, lookback_hours: int = 4, limit: int = 50
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_batch_id = f"batch_{cutoff.strftime('%Y%m%d_%H%M%S')}"

    with conn.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                """SELECT * FROM (
                       SELECT DISTINCT ON (root_cause_category, catalog_item)
                              id, catalog_item, root_cause_category, root_cause_summary,
                              batch_id, confidence,
                              COUNT(*) OVER (
                                  PARTITION BY root_cause_category, catalog_item
                              ) AS recurrence_count
                       FROM {}
                       WHERE confidence = 'high'
                         AND batch_id >= %s
                         AND status = 'analyzed'
                         AND {active}
                       ORDER BY root_cause_category, catalog_item, batch_id DESC
                   ) sub
                   ORDER BY batch_id DESC
                   LIMIT %s"""
            ).format(psycopg2.sql.Identifier(results_table), active=known_issue_active_sql()),
            (cutoff_batch_id, limit),
        )
        rows = cur.fetchall()

    return [
        {
            "result_id": row["id"],
            "catalog_item": row["catalog_item"],
            "root_cause_category": row["root_cause_category"],
            "root_cause_summary": row["root_cause_summary"],
            "batch_id": row["batch_id"],
            "confidence": row["confidence"],
            "recurrence_count": row["recurrence_count"],
        }
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch recent known issues from results table")
    parser.add_argument(
        "--lookback-hours", type=int, default=4, help="How far back to look (default: 4)"
    )
    parser.add_argument("--limit", type=int, default=50, help="Max results to return (default: 50)")
    args = parser.parse_args(argv)

    try:
        config = load_config(required=("name", "user", "password", "results_table"))
    except SystemExit:
        return 1

    try:
        conn = connect_db(config, use_dict_cursor=True)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    try:
        issues = fetch_known_issues(conn, config["results_table"], args.lookback_hours, args.limit)
    except psycopg2.Error as e:
        print(f"Query failed: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(json.dumps(issues, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
