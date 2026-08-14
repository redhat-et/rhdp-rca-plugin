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


def known_issue_active_sql(alias: str = "") -> psycopg2.sql.Composable:
    """Boolean SQL fragment (no leading AND): TRUE unless the row's linked
    ticket is closed and has been for TICKET_CLOSED_GRACE_HOURS+.

    A row is excluded only when ticket_link is set, is_open is false, and
    ticket_resolve_datetime_gmt is old enough. Every other combination (no
    ticket, still open, just closed, or an unknown resolve time) is active.
    """

    def col(name: str) -> psycopg2.sql.Identifier:
        return psycopg2.sql.Identifier(alias, name) if alias else psycopg2.sql.Identifier(name)

    return psycopg2.sql.SQL(
        "({tl} IS NULL OR {io} IS TRUE OR {rd} IS NULL"
        " OR {rd} > NOW() - make_interval(hours => {h}))"
    ).format(
        tl=col("ticket_link"),
        io=col("is_open"),
        rd=col("ticket_resolve_datetime_gmt"),
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
