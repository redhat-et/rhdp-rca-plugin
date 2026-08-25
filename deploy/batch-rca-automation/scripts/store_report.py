"""Store batch RCA JSON reports into a results table on the source database."""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.sql
from utils import connect_db, known_issue_active_sql, load_config

MATCH_THRESHOLD = 0.85
LOOKBACK_HOURS = 4


def find_match(cur: Any, results_table: str, job: dict[str, Any]) -> int | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    cutoff_batch_id = f"batch_{cutoff.strftime('%Y%m%d_%H%M%S')}"

    cur.execute(
        psycopg2.sql.SQL(
            """SELECT id, root_cause_summary FROM {}
               WHERE root_cause_category = %s AND catalog_item = %s
                 AND confidence = 'high' AND batch_id >= %s
                 AND {active}"""
        ).format(
            psycopg2.sql.Identifier(results_table),
            active=known_issue_active_sql(cur.connection, table=results_table),
        ),
        (job.get("root_cause_category"), job.get("catalog_item"), cutoff_batch_id),
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


def _validate_match_id(cur: Any, results_table: str, matched_id: int, job: dict[str, Any]) -> bool:
    cur.execute(
        psycopg2.sql.SQL(
            """SELECT 1 FROM {}
               WHERE id = %s AND root_cause_category = %s AND confidence = 'high'"""
        ).format(psycopg2.sql.Identifier(results_table)),
        (matched_id, job.get("root_cause_category")),
    )
    return cur.fetchone() is not None


def store_report(
    conn: Any, config: dict[str, Any], report: dict[str, Any], filename: str | None = None
) -> bool:
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
            status = job.get("status")

            matched_id = None
            if status in ("analyzed", "matched_known_issue"):
                candidate_id = job.get("matched_result_id")
                if candidate_id is None:
                    hist = job.get("historical_matches")
                    if hist and isinstance(hist, list) and len(hist) > 0:
                        candidate_id = hist[0].get("matched_result_id")

                if candidate_id is not None:
                    if _validate_match_id(cur, results_table, candidate_id, job):
                        matched_id = candidate_id
                        print(f"[MATCH-AGENT] job {jid} -> result {matched_id} (validated)")
                    else:
                        print(f"[WARN] job {jid}: declared match {candidate_id} failed validation")

                if matched_id is None:
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
                            confidence, catalog_item, job_duration_seconds)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (batch_id, job_id) DO UPDATE SET status = EXCLUDED.status
                           RETURNING id"""
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
                    ),
                )
                row = cur.fetchone()
                new_id = row[0] if row else None

                if status in ("analyzed", "matched_known_issue"):
                    cur.execute(
                        psycopg2.sql.SQL(
                            """UPDATE {} SET aap2_job_results_fk_id = %s, ai_processed = TRUE
                               WHERE job_id = %s"""
                        ).format(psycopg2.sql.Identifier(source_table)),
                        (new_id, jid),
                    )

    conn.commit()
    return True


def store_pre_matched(conn: Any, config: dict[str, Any], pre_matched: list[dict[str, Any]]) -> int:
    """Store pre-filtered jobs that were matched before Claude invocation."""
    source_table = config["source_table"]
    count = 0
    with conn.cursor() as cur:
        for job in pre_matched:
            jid = str(job["job_id"])
            matched_id = job["matched_result_id"]
            cur.execute(
                psycopg2.sql.SQL(
                    """UPDATE {} SET aap2_job_results_fk_id = %s, ai_processed = TRUE
                       WHERE job_id = %s"""
                ).format(psycopg2.sql.Identifier(source_table)),
                (matched_id, jid),
            )
            print(
                f"[PRE-MATCH] job {jid} -> result {matched_id}"
                f" (reason: {job.get('match_reason', 'catalog_item')})"
            )
            count += 1
    conn.commit()
    return count


def link_intra_batch_dupes(
    conn: Any, config: dict[str, Any], intra_batch_dupes: list[dict[str, Any]]
) -> int:
    """Copy aap2_job_results_fk_id from representative to each duplicate in the same batch."""
    source_table = config["source_table"]
    count = 0
    with conn.cursor() as cur:
        for entry in intra_batch_dupes:
            dupe_id = str(entry["job_id"])
            rep_id = str(entry["representative_job_id"])

            cur.execute(
                psycopg2.sql.SQL("SELECT aap2_job_results_fk_id FROM {} WHERE job_id = %s").format(
                    psycopg2.sql.Identifier(source_table)
                ),
                (rep_id,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                print(
                    f"[WARN] intra-batch dupe {dupe_id}: representative {rep_id} has no FK yet",
                    file=sys.stderr,
                )
                continue

            fk_id = row[0]
            cur.execute(
                psycopg2.sql.SQL(
                    """UPDATE {} SET aap2_job_results_fk_id = %s, ai_processed = TRUE
                       WHERE job_id = %s"""
                ).format(psycopg2.sql.Identifier(source_table)),
                (fk_id, dupe_id),
            )
            print(f"[DUPE-LINK] job {dupe_id} -> result {fk_id} (via representative {rep_id})")
            count += 1

    conn.commit()
    return count


def store_cross_patterns(conn: Any, config: dict[str, Any], report: dict[str, Any]) -> None:
    patterns = report.get("cross_job_patterns", [])
    if not patterns:
        return
    results_table = config["results_table"]
    batch_id = report.get("batch_id", "")
    with conn.cursor() as cur:
        for p in patterns:
            pattern_name = p.get("pattern")
            description = p.get("description")
            for job_id in p.get("jobs", []):
                cur.execute(
                    psycopg2.sql.SQL(
                        """UPDATE {}
                           SET cross_job_pattern = %s, cross_job_pattern_description = %s
                           WHERE batch_id = %s AND job_id = %s"""
                    ).format(psycopg2.sql.Identifier(results_table)),
                    (pattern_name, description, batch_id, str(job_id)),
                )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Store batch RCA reports in PostgreSQL")
    parser.add_argument("report", nargs="?", help="Path to a batch report JSON file")
    parser.add_argument("--backfill", metavar="DIR", help="Load all batch_*.json files from DIR")
    parser.add_argument(
        "--pre-matched",
        metavar="JSON",
        help="JSON array of pre-matched jobs from pre_filter_jobs.py (stdin if '-')",
    )
    parser.add_argument(
        "--link-dupes",
        metavar="JSON",
        help="JSON array of intra-batch dupes [{job_id, representative_job_id}] to link",
    )
    args = parser.parse_args(argv)

    if not args.report and not args.backfill and not args.pre_matched and not args.link_dupes:
        parser.error(
            "Provide a report path, --backfill DIR, --pre-matched JSON, or --link-dupes JSON"
        )

    try:
        config = load_config(required=("name", "user", "password", "results_table", "source_table"))
    except SystemExit:
        return 1

    try:
        conn = connect_db(config)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    print(f"[INFO] Connected as user: {config['user']} on {config['results_table']}")

    if args.pre_matched:
        try:
            if args.pre_matched == "-":
                pre_matched = json.load(sys.stdin)
            else:
                pre_matched = json.loads(args.pre_matched)
            count = store_pre_matched(conn, config, pre_matched)
            print(f"[DONE] {count} pre-matched job(s) stored")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[ERROR] Invalid pre-matched JSON: {e}", file=sys.stderr)
            return 1
        finally:
            conn.close()
        return 0

    if args.link_dupes:
        try:
            dupes = json.loads(args.link_dupes)
            count = link_intra_batch_dupes(conn, config, dupes)
            print(f"[DONE] {count} intra-batch dupe(s) linked")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[ERROR] Invalid link-dupes JSON: {e}", file=sys.stderr)
            return 1
        finally:
            conn.close()
        return 0

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
            jobs = (
                report.get("job_results") or report.get("job_summaries") or report.get("jobs", [])
            )
            inserted += 1
            print(f"[OK] Stored {bid} ({len(jobs)} jobs)")
            try:
                store_cross_patterns(conn, config, report)
            except Exception as e:
                print(f"[WARN] Failed to store cross_job_patterns: {e}", file=sys.stderr)

    conn.close()
    print(f"[DONE] {inserted}/{len(files)} report(s) stored in {config['results_table']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
