"""Integration tests for known_issue_active_sql() against Postgres."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.sql
import pytest

from conftest import RESULTS_TABLE, SOURCE_TABLE
from utils import _ticket_columns_present, known_issue_active_sql

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


def _insert_event(
    conn: psycopg2.extensions.connection,
    job_id: int,
    job_finished: datetime,
    ticket_link: str | None = None,
    ticket_resolve: datetime | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SOURCE_TABLE}
                (job_id, job_finished, ticket_link, ticket_resolve_datetime_gmt)
            VALUES (%s, %s, %s, %s)
            """,
            (job_id, job_finished, ticket_link, ticket_resolve),
        )
    conn.commit()


def _insert_result(conn: psycopg2.extensions.connection, job_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {RESULTS_TABLE}
                (job_id, batch_id, confidence, status, catalog_item, root_cause_category)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (job_id, "batch_test", "high", "analyzed", "demo.item", "infra"),
        )
        row = cur.fetchone()
        assert row is not None
        result_id = row[0]
    conn.commit()
    return result_id


def _is_active(conn: psycopg2.extensions.connection, result_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL("SELECT {active} FROM {results} r WHERE r.id = %s").format(
                active=known_issue_active_sql(conn, alias="r", source_table=SOURCE_TABLE),
                results=psycopg2.sql.Identifier(RESULTS_TABLE),
            ),
            (result_id,),
        )
        row = cur.fetchone()
        assert row is not None
        return bool(row[0])


@pytest.mark.parametrize(
    ("label", "ticket_link", "resolve_offset", "expected"),
    [
        ("no ticket", None, None, True),
        ("ticket open", "https://jira.example/ABC-1", None, True),
        ("closed 1h ago", "https://jira.example/ABC-2", timedelta(hours=1), True),
        ("closed 5h ago", "https://jira.example/ABC-3", timedelta(hours=5), False),
        (
            "boundary: closed just under 4h ago",
            "https://jira.example/ABC-4",
            timedelta(hours=3, minutes=55),
            True,
        ),
        (
            "boundary: closed just over 4h ago",
            "https://jira.example/ABC-5",
            timedelta(hours=4, minutes=5),
            False,
        ),
    ],
)
def test_known_issue_active_sql_cases(
    db: psycopg2.extensions.connection,
    label: str,
    ticket_link: str | None,
    resolve_offset: timedelta | None,
    expected: bool,
) -> None:
    job_id = hash(label) % 1_000_000
    ticket_resolve = _now() - resolve_offset if resolve_offset is not None else None
    _insert_event(db, job_id, _now(), ticket_link, ticket_resolve)
    result_id = _insert_result(db, job_id)
    assert _is_active(db, result_id) is expected


def test_known_issue_active_when_event_row_missing(db: psycopg2.extensions.connection) -> None:
    """No aap2_events row -> subqueries return NULL -> treated as active."""
    result_id = _insert_result(db, job_id=999001)
    assert _is_active(db, result_id) is True


def test_known_issue_active_when_ticket_columns_missing(
    db: psycopg2.extensions.connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Source table missing ticket_link/ticket_resolve_datetime_gmt -> filtering
    disabled, a warning is printed, and rows are treated as active regardless."""
    _ticket_columns_present.pop(RESULTS_TABLE, None)

    result_id = _insert_result(db, job_id=999002)

    with db.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL("SELECT {active} FROM {results} r WHERE r.id = %s").format(
                active=known_issue_active_sql(db, alias="r", source_table=RESULTS_TABLE),
                results=psycopg2.sql.Identifier(RESULTS_TABLE),
            ),
            (result_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert bool(row[0]) is True

    warning = capsys.readouterr().err
    assert "missing" in warning
    assert RESULTS_TABLE in warning


def test_known_issue_active_in_prefilter_style_query(db: psycopg2.extensions.connection) -> None:
    """Matches how pre_filter_jobs.py embeds the fragment in a WHERE clause."""
    _insert_event(
        db,
        job_id=1001,
        job_finished=_now(),
        ticket_link="https://jira.example/ABC-6",
        ticket_resolve=_now() - timedelta(hours=1),
    )
    active_id = _insert_result(db, 1001)

    _insert_event(
        db,
        job_id=1002,
        job_finished=_now(),
        ticket_link="https://jira.example/ABC-7",
        ticket_resolve=_now() - timedelta(hours=5),
    )
    inactive_id = _insert_result(db, 1002)

    with db.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                """
                SELECT r.id
                FROM {results} r
                WHERE r.confidence = 'high'
                  AND r.status = 'analyzed'
                  AND {active}
                ORDER BY r.id
                """
            ).format(
                results=psycopg2.sql.Identifier(RESULTS_TABLE),
                active=known_issue_active_sql(db, alias="r", source_table=SOURCE_TABLE),
            ),
        )
        ids = [row[0] for row in cur.fetchall()]

    assert active_id in ids
    assert inactive_id not in ids
