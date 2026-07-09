"""Store batch RCA JSON reports into a results table on the source database."""

from __future__ import annotations

import argparse
import difflib
import glob
import json
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
        "results_table": os.environ.get("SOURCE_DB_RESULT_TABLE", ""),
        "source_table": os.environ.get("SOURCE_DB_TABLE", ""),
    }

    errors = []
    required_keys = {
        "name": "SOURCE_DB_NAME",
        "user": "SOURCE_DB_USER",
        "password": "SOURCE_DB_PASSWORD",
        "results_table": "SOURCE_DB_RESULT_TABLE",
        "source_table": "SOURCE_DB_TABLE",
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
    )



MATCH_THRESHOLD = 0.85


def find_match(cur: Any, results_table: str, job: dict[str, Any]) -> int | None:
    cur.execute(
        psycopg2.sql.SQL(
            """SELECT id, root_cause_summary FROM {}
               WHERE root_cause_category = %s AND catalog_item = %s AND confidence = 'high'"""
        ).format(psycopg2.sql.Identifier(results_table)),
        (job.get("root_cause_category"), job.get("catalog_item")),
    )
    summary = job.get("root_cause_summary", "")
    for row_id, existing_summary in cur.fetchall():
        ratio = difflib.SequenceMatcher(None, summary, existing_summary).ratio()
        if ratio >= MATCH_THRESHOLD:
            print(f"[MATCH] job {job.get('job_id')} ({ratio:.0%}) -> result {row_id}")
            print(f"  Current:    {summary[:120]}")
            print(f"  Historical: {existing_summary[:120]}")
            return row_id
    return None


def store_report(conn: Any, config: dict[str, Any], report: dict[str, Any], filename: str | None = None) -> bool:
    batch_id = report.get("batch_id")
    if not batch_id and filename:
        base = os.path.splitext(os.path.basename(filename))[0]
        batch_id = base
    if not batch_id:
        print("[ERROR] Cannot determine batch_id", file=sys.stderr)
        return False

    results_table = config["results_table"]
    source_table = config["source_table"]

    jobs = report.get("job_results") or report.get("job_summaries") or report.get("jobs", [])
    with conn.cursor() as cur:
        for job in jobs:
            jid = str(job.get("job_id", ""))

            matched_id = None
            if job.get("confidence") == "high" and job.get("status") == "analyzed":
                matched_id = find_match(cur, results_table, job)

            if matched_id is not None:
                cur.execute(
                    psycopg2.sql.SQL(
                        """UPDATE {} SET aap2_job_results_fk_id = %s, ai_processed = TRUE
                           WHERE job_id = %s"""
                    ).format(psycopg2.sql.Identifier(source_table)),
                    (matched_id, jid),
                )
            else:
                cur.execute(
                    psycopg2.sql.SQL(
                        """INSERT INTO {}
                           (batch_id, job_id, status, root_cause_category, root_cause_summary,
                            confidence, catalog_item, job_duration_seconds, ticket_link, is_open,
                            cross_job_pattern)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (batch_id, job_id) DO NOTHING"""
                    ).format(psycopg2.sql.Identifier(results_table)),
                    (
                        batch_id,
                        jid,
                        job.get("status"),
                        job.get("root_cause_category"),
                        job.get("root_cause_summary"),
                        job.get("confidence"),
                        job.get("catalog_item"),
                        job.get("job_duration_seconds"),
                        job.get("ticket_link"),
                        job.get("is_open", False),
                        str(matched_id) if matched_id is not None else None,
                    ),
                )

                if job.get("status") == "analyzed":
                    cur.execute(
                        psycopg2.sql.SQL(
                            """UPDATE {} SET ai_processed = TRUE
                               WHERE job_id = %s"""
                        ).format(psycopg2.sql.Identifier(source_table)),
                        (jid,),
                    )

    conn.commit()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Store batch RCA reports in PostgreSQL")
    parser.add_argument(
        "report", nargs="?", help="Path to a batch report JSON file"
    )
    parser.add_argument(
        "--backfill", metavar="DIR",
        help="Load all batch_*.json files from DIR"
    )
    args = parser.parse_args(argv)

    if not args.report and not args.backfill:
        parser.error("Provide a report path or --backfill DIR")

    try:
        config = load_config()
    except SystemExit:
        return 1

    try:
        conn = connect_db(config)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    print(f"[INFO] Connected as user: {config['user']} on {config['results_table']}")

    files: list[str] = []
    if args.backfill:
        files = sorted(glob.glob(os.path.join(args.backfill, "batch_*.json")))
        if not files:
            print(f"[WARN] No batch_*.json files found in {args.backfill}", file=sys.stderr)
            conn.close()
            return 0
    elif args.report:
        files = [args.report]

    inserted = 0
    for path in files:
        try:
            with open(path) as f:
                report = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[ERROR] Failed to read {path}: {e}", file=sys.stderr)
            continue

        if store_report(conn, config, report, filename=path):
            bid = report.get("batch_id") or os.path.splitext(os.path.basename(path))[0]
            jobs = report.get("job_results") or report.get("job_summaries") or report.get("jobs", [])
            inserted += 1
            print(f"[OK] Stored {bid} ({len(jobs)} jobs)")

    conn.close()
    print(f"[DONE] {inserted}/{len(files)} report(s) stored in {config['results_table']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
