"""Analysis Agent (Agent 1) — deterministic JIRA routing gate.

For each job in a time window, looks up its corresponding row in the results
table and sets is_open based on the ticket_link and ticket_resolve_datetime_gmt:

  - No ticket_link                          -> is_open = True  (no ticket yet)
  - ticket_link, no resolve date            -> is_open = True  (ticket still open)
  - ticket resolved < 4h before job_finished -> is_open = False (residual failure)
  - ticket resolved >= 4h before job_finished -> is_open = True  (new incident)

Agent 2 reads is_open to decide whether to create a new JIRA ticket.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.sql
from utils import connect_db, load_config

JIRA_RESIDUAL_WINDOW_HOURS = 4


def compute_is_open(
    ticket_link: str | None,
    ticket_resolve_datetime_gmt: datetime | None,
    job_finished: datetime,
) -> bool:
    """Apply the 4-hour business rule and return the is_open value.

    Ansible jobs run for up to 4 hours. If a ticket was resolved but the job
    finished within 4 hours of that resolution, the failure is residual — the
    job started before the fix was applied, so is_open = False (closed).
    If the job finished >= 4 hours after resolution, it ran after the fix and
    is a new incident, so is_open = True (re-opened).
    """
    if not ticket_link:
        return True

    if ticket_resolve_datetime_gmt is None:
        return True

    resolved_at = ticket_resolve_datetime_gmt
    if not resolved_at.tzinfo:
        resolved_at = resolved_at.replace(tzinfo=timezone.utc)

    delta = job_finished - resolved_at
    if delta < timedelta(hours=JIRA_RESIDUAL_WINDOW_HOURS):
        return False  # residual — ticket recently closed
    return True  # new incident — fix was in place before this job ran


def process_jobs(
    conn: Any,
    config: dict[str, Any],
    since: datetime,
) -> list[dict[str, Any]]:
    """For each job in the window, look up its result row and update is_open."""
    source_table = config["source_table"]
    results_table = config["results_table"]
    annotations = []

    with conn.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                """SELECT e.job_id, e.job_finished,
                          r.id AS result_id, r.ticket_link,
                          r.ticket_resolve_datetime_gmt
                   FROM {source} e
                   JOIN {results} r ON r.job_id::text = e.job_id::text
                   WHERE e.job_finished >= %s
                   ORDER BY e.job_finished DESC"""
            ).format(
                source=psycopg2.sql.Identifier(source_table),
                results=psycopg2.sql.Identifier(results_table),
            ),
            (since,),
        )
        rows = cur.fetchall()

    if not rows:
        print("[INFO] No jobs with result rows found in the specified window", file=sys.stderr)
        return []

    print(f"[INFO] Processing {len(rows)} job(s)", file=sys.stderr)

    with conn.cursor() as cur:
        for row in rows:
            job_id = row["job_id"]
            job_finished = row["job_finished"]

            if not job_finished.tzinfo:
                job_finished = job_finished.replace(tzinfo=timezone.utc)

            is_open = compute_is_open(
                row["ticket_link"],
                row["ticket_resolve_datetime_gmt"],
                job_finished,
            )

            cur.execute(
                psycopg2.sql.SQL(
                    "UPDATE {} SET is_open = %s WHERE id = %s"
                ).format(psycopg2.sql.Identifier(results_table)),
                (is_open, row["result_id"]),
            )

            ticket_link = row["ticket_link"]
            if not ticket_link:
                status = "UNMATCHED_NEW (no ticket)"
            elif is_open and row["ticket_resolve_datetime_gmt"]:
                status = "UNMATCHED_NEW (expired)"
            elif not is_open:
                status = "MATCHED_ACTIVE (residual)"
            else:
                status = "MATCHED_ACTIVE (ticket open)"

            print(f"[{'OPEN' if is_open else 'CLOSED'}] job {job_id} -> {status}")
            annotations.append({
                "job_id": job_id,
                "result_id": row["result_id"],
                "is_open": is_open,
                "ticket_link": ticket_link,
                "status": status,
            })

    conn.commit()
    return annotations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analysis Agent: set is_open on result rows based on JIRA ticket state"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--since",
        type=str,
        default=None,
        help="Process jobs finished after this UTC timestamp (e.g. '2026-08-14 00:00:00')",
    )
    group.add_argument(
        "--lookback-hours",
        type=int,
        default=None,
        help="Process jobs finished in the last N hours (e.g. 24)",
    )
    args = parser.parse_args(argv)

    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    elif args.lookback_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=args.lookback_hours)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        print("[INFO] No window specified, defaulting to last 24 hours", file=sys.stderr)

    try:
        config = load_config(required=("name", "user", "password", "source_table", "results_table"))
    except SystemExit:
        return 1

    try:
        conn = connect_db(config, use_dict_cursor=True)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    try:
        annotations = process_jobs(conn, config, since)
    except psycopg2.Error as e:
        print(f"Failed: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    open_count = sum(1 for a in annotations if a["is_open"])
    closed_count = len(annotations) - open_count
    print(
        f"[DONE] {len(annotations)} job(s): {open_count} open, {closed_count} closed",
        file=sys.stderr,
    )

    print(json.dumps(annotations, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
