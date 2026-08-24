"""Postgres fixtures for batch-rca-automation integration tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import psycopg2
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

SOURCE_TABLE = "aap2_events"
RESULTS_TABLE = "aap2_job_results"

SCHEMA_SQL = f"""
DROP TABLE IF EXISTS {RESULTS_TABLE};
DROP TABLE IF EXISTS {SOURCE_TABLE};

CREATE TABLE {SOURCE_TABLE} (
    job_id BIGINT PRIMARY KEY,
    job_finished TIMESTAMPTZ,
    ticket_link TEXT,
    ticket_resolve_datetime_gmt TIMESTAMPTZ
);

CREATE TABLE {RESULTS_TABLE} (
    id SERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL,
    batch_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    catalog_item TEXT,
    root_cause_category TEXT
);
"""


def _db_kwargs() -> dict[str, object]:
    return {
        "host": os.environ.get("TEST_DB_HOST", "localhost"),
        "port": int(os.environ.get("TEST_DB_PORT", "5433")),
        "dbname": os.environ.get("TEST_DB_NAME", "rca_test"),
        "user": os.environ.get("TEST_DB_USER", "rca_test"),
        "password": os.environ.get("TEST_DB_PASSWORD", "rca_test"),
    }


@pytest.fixture(scope="session")
def db_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    try:
        conn = psycopg2.connect(**_db_kwargs())
    except psycopg2.OperationalError as exc:
        pytest.skip(
            "Postgres test database not available. "
            "Start it with: docker compose -f docker-compose.test.yml up -d --wait"
            f" ({exc})"
        )

    os.environ["SOURCE_DB_TABLE"] = SOURCE_TABLE

    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()

    yield conn
    conn.close()


@pytest.fixture
def db(
    db_conn: psycopg2.extensions.connection,
) -> Generator[psycopg2.extensions.connection, None, None]:
    with db_conn.cursor() as cur:
        cur.execute(f"TRUNCATE {RESULTS_TABLE} RESTART IDENTITY CASCADE")
        cur.execute(f"TRUNCATE {SOURCE_TABLE} RESTART IDENTITY CASCADE")
    db_conn.commit()
    yield db_conn
