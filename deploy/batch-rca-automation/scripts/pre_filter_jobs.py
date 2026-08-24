"""Pre-filter jobs against recent known results before spawning Claude agents.

Matches on catalog_item + error_message similarity. A job is only pre-matched when:
  1. Its catalog_item matches a recent high-confidence result unambiguously
     (only one distinct root_cause_category for that catalog_item in the window)
  2. Its error_message is similar enough to the original job's error_message
     (SequenceMatcher ratio >= MATCH_THRESHOLD)

If either side is missing an error_message the job proceeds to full RCA.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.sql
from utils import connect_db, known_issue_active_sql, load_config

MATCH_THRESHOLD = 0.75
CROSS_CATALOG_THRESHOLD = 0.90


def extract_catalog_item(job_name: str) -> str | None:
    """Extract catalog_item from job_name.

    Handles the RHPDS format: 'RHPDS {platform}.{catalog_item}.{env}-{guid}-{action} ...'
    """
    name = job_name.removeprefix("RHPDS ").strip()
    if " " in name:
        name = name.split(maxsplit=1)[0]
    parts = name.split(".")
    if len(parts) >= 3:
        return ".".join(parts[1:-1])
    if len(parts) == 2:
        return parts[1].split("-")[0] if "-" in parts[1] else parts[1]
    return None


_MAX_CMP_LEN = 5000


def error_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a[:_MAX_CMP_LEN], b[:_MAX_CMP_LEN]).ratio()


def fetch_filter_context(
    conn: Any,
    results_table: str,
    source_table: str,
    job_ids: list[int],
    lookback_hours: int = 4,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Fetch recent known results and incoming job metadata in one pass.

    Returns (recent_results, job_metadata) where:
      - recent_results: high-confidence analyzed results with error_message from the source table
      - job_metadata: {job_id: {job_name, error_message}} for the incoming jobs
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_batch_id = f"batch_{cutoff.strftime('%Y%m%d_%H%M%S')}"

    with conn.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                """SELECT r.id, r.catalog_item, r.root_cause_category,
                          r.root_cause_summary, e.error_message
                   FROM {results} r
                   LEFT JOIN {source} e ON r.job_id::text = e.job_id::text
                   WHERE r.confidence = 'high'
                     AND r.batch_id >= %s
                     AND r.status = 'analyzed'
                     AND {active}
                   ORDER BY r.batch_id DESC"""
            ).format(
                results=psycopg2.sql.Identifier(results_table),
                source=psycopg2.sql.Identifier(source_table),
                active=known_issue_active_sql(conn, alias="r"),
            ),
            (cutoff_batch_id,),
        )
        recent_results = [dict(row) for row in cur.fetchall()]

        cur.execute(
            psycopg2.sql.SQL(
                """SELECT job_id, job_name, error_message
                   FROM {} WHERE job_id = ANY(%s) AND job_finished >= %s"""
            ).format(psycopg2.sql.Identifier(source_table)),
            (job_ids, cutoff),
        )
        job_metadata = {
            row["job_id"]: {
                "job_name": row["job_name"],
                "error_message": row["error_message"],
            }
            for row in cur.fetchall()
            if row["job_name"]
        }

    return recent_results, job_metadata


def build_catalog_index(recent_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build catalog_item -> result index, excluding ambiguous items.

    A catalog_item is excluded when it has more than one distinct root_cause_category
    in the lookback window — the same item is failing for different reasons so we
    cannot safely pre-match new jobs against it.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in recent_results:
        ci = result["catalog_item"]
        if not ci:
            continue
        grouped.setdefault(ci, []).append(result)

    return {
        ci: results[0]  # most recent (results ordered by batch_id DESC)
        for ci, results in grouped.items()
        if len({r["root_cause_category"] for r in results}) == 1
    }


def build_category_index(recent_results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build root_cause_category -> [results] index for cross-catalog matching."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in recent_results:
        cat = result.get("root_cause_category")
        if not cat:
            continue
        grouped.setdefault(cat, []).append(result)
    return grouped


def filter_jobs(
    job_ids: list[int],
    job_metadata: dict[int, dict[str, Any]],
    find_match: Callable[[int, dict[str, Any]], dict[str, Any] | None],
    match_reason: str,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Split job_ids into (analyze, matched) using the find_match callback."""
    analyze: list[int] = []
    matched: list[dict[str, Any]] = []

    for job_id in job_ids:
        meta = job_metadata.get(job_id, {})
        result = find_match(job_id, meta)
        if result:
            matched.append(
                {
                    "job_id": job_id,
                    "matched_result_id": result["id"],
                    "catalog_item": result["catalog_item"],
                    "root_cause_category": result["root_cause_category"],
                    "match_reason": match_reason,
                    "recent_result_summary": result["root_cause_summary"][:200],
                }
            )
        else:
            analyze.append(job_id)

    return analyze, matched


def _catalog_matcher(
    catalog_index: dict[str, dict[str, Any]],
) -> Callable[[int, dict[str, Any]], dict[str, Any] | None]:
    def find_match(_job_id: int, meta: dict[str, Any]) -> dict[str, Any] | None:
        extracted = extract_catalog_item(meta.get("job_name", ""))
        result = catalog_index.get(extracted) if extracted else None
        if not result:
            return None
        new_err = meta.get("error_message") or ""
        known_err = result.get("error_message") or ""
        if not new_err or not known_err:
            return None
        if error_similarity(new_err, known_err) < MATCH_THRESHOLD:
            return None
        return result

    return find_match


def _cross_catalog_matcher(
    category_index: dict[str, list[dict[str, Any]]],
) -> Callable[[int, dict[str, Any]], dict[str, Any] | None]:
    def find_match(_job_id: int, meta: dict[str, Any]) -> dict[str, Any] | None:
        new_err = meta.get("error_message") or ""
        if not new_err:
            return None
        best_result = None
        best_score = CROSS_CATALOG_THRESHOLD
        for results in category_index.values():
            for result in results:
                known_err = result.get("error_message") or ""
                if not known_err:
                    continue
                score = error_similarity(new_err, known_err)
                if score >= best_score:
                    best_score = score
                    best_result = result
        return best_result

    return find_match


def fetch_job_metadata(
    conn: Any, source_table: str, job_ids: list[int]
) -> dict[int, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                "SELECT job_id, job_name, error_message FROM {} WHERE job_id = ANY(%s)"
            ).format(psycopg2.sql.Identifier(source_table)),
            (job_ids,),
        )
        return {
            row["job_id"]: {
                "job_name": row["job_name"],
                "error_message": row["error_message"],
            }
            for row in cur.fetchall()
            if row["job_name"]
        }


def dedup_batch(
    job_ids: list[int],
    job_metadata: dict[int, dict[str, Any]],
) -> tuple[list[int], list[dict[str, Any]]]:
    """Group jobs by catalog_item + error_message similarity, pick earliest per group."""
    by_catalog: dict[str, list[int]] = {}
    for job_id in job_ids:
        meta = job_metadata.get(job_id)
        if not meta:
            by_catalog.setdefault("__unknown__", []).append(job_id)
            continue
        ci = extract_catalog_item(meta.get("job_name", "")) or "__unknown__"
        by_catalog.setdefault(ci, []).append(job_id)

    representatives: list[int] = []
    dupes: list[dict[str, Any]] = []

    for group_ids in by_catalog.values():
        if len(group_ids) == 1:
            representatives.append(group_ids[0])
            continue

        sorted_ids = sorted(group_ids)

        clusters: list[list[int]] = []
        for job_id in sorted_ids:
            err = (job_metadata.get(job_id) or {}).get("error_message") or ""
            placed = False
            for cluster in clusters:
                rep_err = (job_metadata.get(cluster[0]) or {}).get("error_message") or ""
                if err and rep_err and error_similarity(err, rep_err) >= MATCH_THRESHOLD:
                    cluster.append(job_id)
                    placed = True
                    break
            if not placed:
                clusters.append([job_id])

        for cluster in clusters:
            representatives.append(cluster[0])
            for dupe_id in cluster[1:]:
                dupes.append({"job_id": dupe_id, "representative_job_id": cluster[0]})

    return representatives, dupes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-filter jobs against known results")
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="File with newline-separated job IDs (default: stdin)",
    )
    parser.add_argument(
        "--lookback-hours", type=int, default=4, help="Lookback window (default: 4)"
    )
    parser.add_argument(
        "--dedup-only",
        action="store_true",
        help="Only run intra-batch dedup (no pre-filter against known issues)",
    )
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input) as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    job_ids = [int(line.strip()) for line in raw.strip().splitlines() if line.strip()]
    if not job_ids:
        if args.dedup_only:
            print(json.dumps({"representatives": job_ids, "dupes": []}))
        else:
            print(json.dumps({"analyze": [], "pre_matched": []}))
        return 0

    required_keys = ("name", "user", "password", "source_table")
    if not args.dedup_only:
        required_keys = (*required_keys, "results_table")

    try:
        config = load_config(required=required_keys)
    except SystemExit:
        return 1

    try:
        conn = connect_db(config, use_dict_cursor=True)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    try:
        if args.dedup_only:
            job_metadata = fetch_job_metadata(conn, config["source_table"], job_ids)
            representatives, dupes = dedup_batch(job_ids, job_metadata)
            result = {"representatives": representatives, "dupes": dupes}
        else:
            recent_results, job_metadata = fetch_filter_context(
                conn,
                config["results_table"],
                config["source_table"],
                job_ids,
                args.lookback_hours,
            )
            catalog_index = build_catalog_index(recent_results) if recent_results else {}
            category_index = build_category_index(recent_results) if recent_results else {}

            unknown_ids = [jid for jid in job_ids if jid not in job_metadata]

            # First pass: match by catalog_item + error similarity
            analyze, pre_matched = filter_jobs(
                list(job_metadata.keys()),
                job_metadata,
                _catalog_matcher(catalog_index),
                "pre_filter_catalog_item+error_message",
            )
            analyze.extend(unknown_ids)

            # Second pass: cross-catalog match for platform-level failures
            analyze, cross_matched = filter_jobs(
                analyze,
                job_metadata,
                _cross_catalog_matcher(category_index),
                "cross_catalog_error_message",
            )
            result = {"analyze": analyze, "pre_matched": pre_matched + cross_matched}

    except psycopg2.Error as e:
        print(f"Query failed: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
