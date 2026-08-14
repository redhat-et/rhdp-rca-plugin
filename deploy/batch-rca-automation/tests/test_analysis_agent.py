"""Tests for the Analysis Agent — the 4-hour business rule.

The 4-hour window is relative to job_finished vs ticket_resolve_datetime_gmt.

Test cases from jappleii's spec:
  1. No ticket_link                              -> is_open = True
  2. ticket_link, no resolve date                -> is_open = True  (still open)
  3. ticket resolved < 4h before job_finished    -> is_open = False (residual)
  4. ticket resolved >= 4h before job_finished   -> is_open = True  (new incident)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from analysis_agent import compute_is_open  # noqa: I001

TICKET_URL = "https://redhat.atlassian.net/browse/GPTEINFRA-1"
RESOLVED_AT = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class TestComputeIsOpen:
    def test_case1_no_ticket_link(self) -> None:
        """No ticket_link -> is_open = True."""
        assert compute_is_open(None, None, RESOLVED_AT + timedelta(hours=10)) is True

    def test_case2_ticket_no_resolve_date(self) -> None:
        """ticket_link exists but not resolved -> is_open = True."""
        assert compute_is_open(TICKET_URL, None, RESOLVED_AT + timedelta(hours=10)) is True

    def test_case3_resolved_within_4h(self) -> None:
        """job_finished 2h after resolution -> is_open = False (residual)."""
        job_finished = RESOLVED_AT + timedelta(hours=2)
        assert compute_is_open(TICKET_URL, RESOLVED_AT, job_finished) is False

    def test_case4_resolved_over_4h(self) -> None:
        """job_finished 5h after resolution -> is_open = True (new incident)."""
        job_finished = RESOLVED_AT + timedelta(hours=5)
        assert compute_is_open(TICKET_URL, RESOLVED_AT, job_finished) is True

    def test_boundary_exactly_4h(self) -> None:
        """job_finished exactly 4h after resolution -> is_open = True (new incident)."""
        job_finished = RESOLVED_AT + timedelta(hours=4)
        assert compute_is_open(TICKET_URL, RESOLVED_AT, job_finished) is True

    def test_naive_resolve_datetime_treated_as_utc(self) -> None:
        """Naive datetime in ticket_resolve_datetime_gmt is treated as UTC."""
        resolved_naive = datetime(2024, 6, 15, 12, 0, 0)
        job_finished = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)  # 2h later
        assert compute_is_open(TICKET_URL, resolved_naive, job_finished) is False
