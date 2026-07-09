"""Query source PostgreSQL table for unanalyzed job IDs.

Reads events where ai_processed = FALSE and outputs distinct job IDs,
one per line. The batch script passes these to Claude agents for RCA.
"""

from __future__ import annotations

import argparse
import os
import sys
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
        "table": os.environ.get("SOURCE_DB_TABLE", ""),
    }

    errors = []
    for key in ("name", "user", "password", "table"):
        if not config[key]:
            env_var = f"SOURCE_DB_{key.upper()}"
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


def query_job_ids(
    conn: Any, table: str, since: str | None = None, limit: int | None = None
) -> list[int]:
    conditions = ["(ai_processed IS NULL OR ai_processed = FALSE)"]
    params: list[Any] = []

    if since:
        conditions.append("job_started >= %s")
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
    parser = argparse.ArgumentParser(
        description="Query source DB for unanalyzed job IDs"
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="Only include events after this timestamp (e.g. '2024-06-01 08:00:00')"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum number of job IDs to return"
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Save job IDs to this file (default: print to stdout)"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except SystemExit:
        return 1

    conn = None
    try:
        conn = connect_db(config)
        job_ids = query_job_ids(conn, config["table"], args.since, args.limit)
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
