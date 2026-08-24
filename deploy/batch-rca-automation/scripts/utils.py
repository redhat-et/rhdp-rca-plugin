"""Shared database utilities for batch RCA automation scripts."""

from __future__ import annotations

import os
import sys
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.sql
from dotenv import load_dotenv

ALL_CONFIG_KEYS = {
    "host": ("SOURCE_DB_HOST", "localhost"),
    "port": ("SOURCE_DB_PORT", "5432"),
    "name": ("SOURCE_DB_NAME", ""),
    "user": ("SOURCE_DB_USER", ""),
    "password": ("SOURCE_DB_PASSWORD", ""),
    "source_table": ("SOURCE_DB_TABLE", ""),
    "results_table": ("SOURCE_DB_RESULT_TABLE", ""),
}

# Grace period after a linked JIRA ticket is closed before a known-issue row
# is excluded from matching. Distinct from the unrelated lookback_hours=4
# recency windows used elsewhere in this pipeline.
TICKET_CLOSED_GRACE_HOURS = 4

_TICKET_COLUMNS = ("ticket_link", "ticket_resolve_datetime_gmt")

# Per-table cache so the schema is only probed once per process, not once per query.
_ticket_columns_present: dict[str, bool] = {}


def _has_ticket_columns(conn: Any, table: str) -> bool:
    if table not in _ticket_columns_present:
        with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = %s AND column_name = ANY(%s)",
                (table, list(_TICKET_COLUMNS)),
            )
            found = {row[0] for row in cur.fetchall()}
        present = set(_TICKET_COLUMNS) <= found
        if not present:
            print(
                f"[WARN] {table} is missing {', '.join(_TICKET_COLUMNS)}; "
                "known-issue ticket filtering disabled, treating all known issues as active",
                file=sys.stderr,
            )
        _ticket_columns_present[table] = present
    return _ticket_columns_present[table]


def load_config(required: tuple[str, ...] = ("name", "user", "password")) -> dict[str, Any]:
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file)

    config: dict[str, Any] = {}
    for key, (env_var, default) in ALL_CONFIG_KEYS.items():
        val = os.environ.get(env_var, default)
        config[key] = int(val) if key == "port" else val

    errors = [f"{ALL_CONFIG_KEYS[k][0]} is required" for k in required if not config.get(k)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)

    return config


def known_issue_active_sql(
    conn: Any,
    alias: str = "",
    *,
    source_table: str | None = None,
) -> psycopg2.sql.Composable:
    """Boolean SQL fragment (no leading AND): TRUE unless the row's linked
    ticket is closed and has been for TICKET_CLOSED_GRACE_HOURS+.

    ticket_link and ticket_resolve_datetime_gmt live on the source/events
    table (aap2_events), not the results table, so they're read via a
    correlated subquery keyed on the row's own job_id.

    A row is excluded only when ticket_link is set and
    ticket_resolve_datetime_gmt is old enough. Every other combination (no
    ticket, or an unknown/recent resolve time) is active.

    If the source table is missing ticket_link/ticket_resolve_datetime_gmt
    (schema drift), ticket filtering is disabled and every row is treated as
    active -- a warning is printed once per table.
    """

    def col(name: str) -> psycopg2.sql.Identifier:
        return psycopg2.sql.Identifier(alias, name) if alias else psycopg2.sql.Identifier(name)

    table = source_table or os.environ.get("SOURCE_DB_TABLE", "")
    if not table:
        raise ValueError(
            "source_table is required for known_issue_active_sql "
            "(pass explicitly or set SOURCE_DB_TABLE)"
        )

    if not _has_ticket_columns(conn, table):
        return psycopg2.sql.SQL("TRUE")

    def event_col(column: str) -> psycopg2.sql.Composable:
        return psycopg2.sql.SQL(
            "(SELECT e.{column} FROM {source} e"
            " WHERE e.job_id::text = {job_id}::text LIMIT 1)"
        ).format(
            column=psycopg2.sql.Identifier(column),
            source=psycopg2.sql.Identifier(table),
            job_id=col("job_id"),
        )

    return psycopg2.sql.SQL(
        "({tl} IS NULL OR {rd} IS NULL"
        " OR {rd} > NOW() - make_interval(hours => {h}))"
    ).format(
        tl=event_col("ticket_link"),
        rd=event_col("ticket_resolve_datetime_gmt"),
        h=psycopg2.sql.Literal(TICKET_CLOSED_GRACE_HOURS),
    )


def connect_db(config: dict[str, Any], *, use_dict_cursor: bool = False) -> Any:
    kwargs: dict[str, Any] = {
        "host": config["host"],
        "port": config["port"],
        "dbname": config["name"],
        "user": config["user"],
        "password": config["password"],
    }
    if use_dict_cursor:
        kwargs["cursor_factory"] = psycopg2.extras.RealDictCursor
    return psycopg2.connect(**kwargs)
