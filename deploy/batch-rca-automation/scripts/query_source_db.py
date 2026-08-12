"""Query source PostgreSQL table for unanalyzed job IDs.

Reads events where ai_processed = FALSE and outputs distinct job IDs,
one per line. The batch script passes these to Claude agents for RCA.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import psycopg2
import psycopg2.sql
from utils import connect_db, load_config


def query_job_ids(
    conn: Any, table: str, since: str | None = None, limit: int | None = None
) -> list[int]:
    conditions = ["(ai_processed IS NULL OR ai_processed = FALSE)"]
    params: list[Any] = []

    if since:
        conditions.append("job_finished >= %s")
        params.append(since)

    suffix = ""
    if limit is not None:
        suffix = " LIMIT %s"
        params.append(limit)

    query = psycopg2.sql.SQL(
        "SELECT DISTINCT job_id "
        "FROM {} "
        "WHERE " + " AND ".join(conditions) + " "
        "ORDER BY job_id DESC" + suffix
    ).format(psycopg2.sql.Identifier(table))

    with conn.cursor() as cur:
        cur.execute(query, params)
        return [row["job_id"] for row in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query source DB for unanalyzed job IDs")
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only include events after this timestamp (e.g. '2024-06-01 08:00:00')",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Maximum number of job IDs to return"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Save job IDs to this file (default: print to stdout)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(required=("name", "user", "password", "source_table"))
    except SystemExit:
        return 1

    conn = None
    try:
        conn = connect_db(config, use_dict_cursor=True)
        job_ids = query_job_ids(conn, config["source_table"], args.since, args.limit)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to source database: {e}", file=sys.stderr)
        return 1
    except psycopg2.Error as e:
        print(f"Query failed: {e}", file=sys.stderr)
        return 1
    finally:
        if conn:
            conn.close()

    output = "\n".join(str(jid) for jid in job_ids)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n" if output else "")
        print(f"Saved {len(job_ids)} job ID(s) to {args.output}", file=sys.stderr)
    else:
        if output:
            print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
