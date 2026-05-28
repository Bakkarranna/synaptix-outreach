from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from linkedin.daemon import seconds_until_active


def _mock_now(year, month, day, hour, minute=0, tz="UTC"):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))


class TestSecondsUntilActive:
    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_inside_active_window(self):
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 12)):
            assert seconds_until_active() == 0.0

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_before_start(self):
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 7)):
            result = seconds_until_active()
            assert result == pytest.approx(2 * 3600, abs=1)

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_after_end(self):
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 18)):
            result = seconds_until_active()
            assert result == pytest.approx(15 * 3600, abs=1)  # 15h to Thu 9am

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_saturday_is_active(self):
        # Sat Mar 21 2026 noon — weekends are no longer skipped.
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 21, 12)):
            assert seconds_until_active() == 0.0

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_friday_evening_to_saturday_morning(self):
        # Friday 18:00 → Saturday 9:00 = 15h (no weekend skip).
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 20, 18)):
            assert seconds_until_active() == pytest.approx(15 * 3600, abs=1)

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "Europe/Berlin")
    def test_timezone_respected(self):
        # Wed 8am Berlin = still before 9am start
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 8, tz="Europe/Berlin")):
            result = seconds_until_active()
            assert result == pytest.approx(3600, abs=1)

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_at_exact_start(self):
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 9)):
            assert seconds_until_active() == 0.0

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", True)
    @patch("linkedin.daemon.ACTIVE_START_HOUR", 9)
    @patch("linkedin.daemon.ACTIVE_END_HOUR", 17)
    @patch("linkedin.daemon.ACTIVE_TIMEZONE", "UTC")
    def test_at_exact_end(self):
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 17)):
            result = seconds_until_active()
            # Should be outside (end is exclusive), next day 9am = 16h
            assert result == pytest.approx(16 * 3600, abs=1)

    @patch("linkedin.daemon.ENABLE_ACTIVE_HOURS", False)
    def test_disabled_always_active(self):
        with patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 21, 23)):
            assert seconds_until_active() == 0.0
