"""Query historical results narrowly filtered by (root_cause_category, catalog_item) pairs."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.sql
from utils import connect_db, known_issue_active_sql, load_config


def query_matches(
    conn: Any,
    table: str,
    pairs: list[dict[str, str]],
    exclude_batch: str | None = None,
    lookback_days: int = 7,
    limit_per_group: int = 10,
) -> list[dict]:
    if not pairs:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
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
        " AND status = 'analyzed'"
        " AND {active}"
        " ORDER BY batch_id DESC"
    ).format(psycopg2.sql.Identifier(table), active=known_issue_active_sql(conn, table=table))

    params: list[Any] = [cutoff_batch_id]
    if exclude_batch:
        query = psycopg2.sql.SQL(
            "SELECT id, job_id, batch_id, root_cause_category, root_cause_summary,"
            " confidence, catalog_item"
            " FROM {}"
            " WHERE batch_id >= %s AND batch_id != %s"
            " AND (root_cause_category, catalog_item) IN (" + in_clause + ")"
            " AND confidence IN ('high', 'medium')"
            " AND status = 'analyzed'"
            " AND {active}"
            " ORDER BY batch_id DESC"
        ).format(psycopg2.sql.Identifier(table), active=known_issue_active_sql(conn, table=table))
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
            groups[key]["results"].append(
                {
                    "id": row["id"],
                    "job_id": row["job_id"],
                    "batch_id": row["batch_id"],
                    "root_cause_summary": row["root_cause_summary"],
                    "confidence": row["confidence"],
                }
            )
        groups[key]["occurrence_count"] += 1

    return sorted(groups.values(), key=lambda g: g["occurrence_count"], reverse=True)


def _extract_pairs(report: dict) -> list[dict[str, str]]:
    """Extract unique high-confidence (root_cause_category, catalog_item) pairs from a batch report."""
    seen: set[tuple[str, str]] = set()
    pairs = []
    for job in report.get("job_summaries", []):
        cat = job.get("root_cause_category")
        ci = job.get("catalog_item")
        conf = job.get("confidence")
        if conf == "high" and cat and ci:
            key = (cat, ci)
            if key not in seen:
                pairs.append({"root_cause_category": cat, "catalog_item": ci})
                seen.add(key)
    return pairs


_SIMILARITY_THRESHOLD = 0.6


def merge_historical_into_report(
    report: dict,
    groups: list[dict],
    max_matches: int = 3,
) -> dict:
    """Attach historical_matches to each high-confidence job_summary in the report."""
    group_map = {(g["root_cause_category"], g["catalog_item"]): g for g in groups}
    for job in report.get("job_summaries", []):
        if job.get("confidence") != "high":
            continue
        key = (job.get("root_cause_category"), job.get("catalog_item"))
        group = group_map.get(key)
        if group:
            job_summary = job.get("root_cause_summary", "")
            similar = [
                r
                for r in group["results"]
                if difflib.SequenceMatcher(
                    None, job_summary, r.get("root_cause_summary", "")
                ).ratio()
                >= _SIMILARITY_THRESHOLD
            ]
            job["historical_matches"] = [
                {
                    "matched_result_id": r["id"],
                    "recurrence_count": group["occurrence_count"],
                    "similarity_reasoning": (
                        f"Same {key[0]} failure in {key[1]} "
                        f"({group['occurrence_count']} prior occurrence(s))"
                    ),
                }
                for r in similar[:max_matches]
            ]
        else:
            job["historical_matches"] = []
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query historical results filtered by (root_cause_category, catalog_item) pairs"
    )
    parser.add_argument(
        "--input",
        metavar="FILE",
        help="JSON file with pairs to query (reads stdin if omitted)",
    )
    parser.add_argument(
        "--report",
        metavar="FILE",
        help="Batch report JSON to annotate with historical_matches (alternative to --input)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write annotated report to this path (used with --report; defaults to overwrite input)",
    )
    parser.add_argument(
        "--exclude-batch",
        metavar="BATCH_ID",
        help="Exclude this batch_id from results (avoid self-matching)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Days to look back (default: 7)",
    )
    parser.add_argument(
        "--limit-per-group",
        type=int,
        default=10,
        help="Max results per (category, catalog_item) group (default: 10)",
    )
    args = parser.parse_args(argv)

    # --report mode: read report, extract pairs, merge matches back in
    if args.report:
        try:
            with open(args.report) as f:
                report = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Failed to read report: {e}", file=sys.stderr)
            return 1

        pairs = _extract_pairs(report)
        if not pairs:
            print("No high-confidence job summaries found; nothing to correlate.", file=sys.stderr)
            return 0

        try:
            config = load_config(required=("name", "user", "password", "results_table"))
            conn = connect_db(config, use_dict_cursor=True)
        except (SystemExit, psycopg2.OperationalError) as e:
            print(f"DB connection failed: {e}", file=sys.stderr)
            return 1

        groups = query_matches(
            conn,
            config["results_table"],
            pairs,
            exclude_batch=args.exclude_batch,
            lookback_days=args.lookback_days,
            limit_per_group=args.limit_per_group,
        )
        conn.close()

        updated = merge_historical_into_report(report, groups)
        out_path = args.output or args.report
        out_dir = os.path.dirname(os.path.abspath(out_path))
        fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(updated, f, indent=2)
            os.replace(tmp_path, out_path)
        except BaseException:
            os.unlink(tmp_path)
            raise
        print(f"Historical matches written to {out_path}", file=sys.stderr)
        return 0

    # --input / stdin mode: return raw groups as JSON
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
        config = load_config(required=("name", "user", "password", "results_table"))
    except SystemExit:
        return 1

    try:
        conn = connect_db(config, use_dict_cursor=True)
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to database: {e}", file=sys.stderr)
        return 1

    result = query_matches(
        conn,
        config["results_table"],
        pairs,
        exclude_batch=args.exclude_batch,
        lookback_days=args.lookback_days,
        limit_per_group=args.limit_per_group,
    )
    conn.close()
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
