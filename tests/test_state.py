"""
tests/test_state.py — Unit tests for the rolling-window state engine.

Covers:
  • PriceWindow pruning and percentage-change calculation
  • StateManager threshold detection
  • Edge cases (zero price, single sample, exact boundary)
"""

import time

import pytest

from state import PriceWindow, StateManager


# =====================================================================
# PriceWindow tests
# =====================================================================

class TestPriceWindow:
    """Unit tests for a single token's rolling price window."""

    def test_single_sample_returns_none(self):
        """pct_change should be None when there is only one data point."""
        pw = PriceWindow("tok1", window_seconds=180)
        pw.add(0.50, timestamp=1000.0)
        assert pw.pct_change() is None

    def test_no_change(self):
        """Price unchanged ⇒ 0 % change."""
        pw = PriceWindow("tok1", window_seconds=180)
        pw.add(0.50, timestamp=1000.0)
        pw.add(0.50, timestamp=1010.0)
        assert pw.pct_change() == pytest.approx(0.0)

    def test_positive_change(self):
        """0.50 → 0.60 = +20 %."""
        pw = PriceWindow("tok1", window_seconds=180)
        pw.add(0.50, timestamp=1000.0)
        pw.add(0.60, timestamp=1010.0)
        assert pw.pct_change() == pytest.approx(20.0)

    def test_negative_change(self):
        """0.50 → 0.40 = −20 %."""
        pw = PriceWindow("tok1", window_seconds=180)
        pw.add(0.50, timestamp=1000.0)
        pw.add(0.40, timestamp=1010.0)
        assert pw.pct_change() == pytest.approx(-20.0)

    def test_zero_oldest_price_returns_none(self):
        """Division by zero must not crash — return None."""
        pw = PriceWindow("tok1", window_seconds=180)
        pw.add(0.0, timestamp=1000.0)
        pw.add(0.50, timestamp=1010.0)
        assert pw.pct_change() is None

    # ── Pruning ──────────────────────────────────────────────────────

    def test_prune_removes_old_entries(self):
        """Entries older than the window should be pruned on add()."""
        pw = PriceWindow("tok1", window_seconds=10)  # tiny window

        pw.add(0.10, timestamp=100.0)
        pw.add(0.20, timestamp=105.0)
        pw.add(0.30, timestamp=111.0)  # 100.0 is now > 10 s old

        # The oldest entry (100.0) should have been pruned
        assert pw.size == 2
        assert pw._data[0] == (105.0, 0.20)

    def test_prune_keeps_entries_inside_window(self):
        """Entries within the window must be kept."""
        pw = PriceWindow("tok1", window_seconds=180)

        pw.add(0.10, timestamp=1000.0)
        pw.add(0.20, timestamp=1050.0)
        pw.add(0.30, timestamp=1100.0)  # all within 180 s

        assert pw.size == 3

    def test_prune_with_exact_boundary(self):
        """An entry exactly at the cutoff boundary should be pruned
        (strictly less-than)."""
        pw = PriceWindow("tok1", window_seconds=10)

        pw.add(0.10, timestamp=100.0)
        pw.add(0.20, timestamp=110.0)  # cutoff = 110 - 10 = 100.0

        # 100.0 < 100.0 is False, so entry stays
        assert pw.size == 2

    def test_latest_price(self):
        pw = PriceWindow("tok1")
        assert pw.latest_price is None
        pw.add(0.42, timestamp=1.0)
        assert pw.latest_price == pytest.approx(0.42)


# =====================================================================
# StateManager tests
# =====================================================================

class TestStateManager:
    """Integration-level tests for multi-token state + alerting logic."""

    def test_no_alert_below_threshold(self):
        """A 14.9 % increase should NOT trigger an alert at 15 % threshold."""
        sm = StateManager(window_seconds=180, threshold_pct=15.0)

        # 0.50 → 0.5745 = +14.9 %
        alerts = sm.update({"tok1": 0.50}, timestamp=1000.0)
        assert alerts == []

        alerts = sm.update({"tok1": 0.5745}, timestamp=1010.0)
        assert alerts == []

    def test_alert_at_exact_threshold(self):
        """Exactly 15 % increase ⇒ alert triggered."""
        sm = StateManager(window_seconds=180, threshold_pct=15.0)

        sm.update({"tok1": 0.50}, timestamp=1000.0)
        alerts = sm.update({"tok1": 0.575}, timestamp=1010.0)

        assert len(alerts) == 1
        assert alerts[0]["token_id"] == "tok1"
        assert alerts[0]["pct_change"] == pytest.approx(15.0)

    def test_alert_above_threshold(self):
        """20 % increase ⇒ alert with correct metadata."""
        sm = StateManager(window_seconds=180, threshold_pct=15.0)

        sm.update({"tok1": 0.50}, timestamp=1000.0)
        alerts = sm.update({"tok1": 0.60}, timestamp=1010.0)

        assert len(alerts) == 1
        assert alerts[0]["pct_change"] == pytest.approx(20.0)
        assert alerts[0]["oldest_price"] == pytest.approx(0.50)
        assert alerts[0]["latest_price"] == pytest.approx(0.60)

    def test_alert_clears_after_window_expires(self):
        """After the price spike leaves the window, alerts should stop."""
        sm = StateManager(window_seconds=10, threshold_pct=15.0)

        sm.update({"tok1": 0.50}, timestamp=100.0)
        alerts = sm.update({"tok1": 0.60}, timestamp=105.0)  # +20 %
        assert len(alerts) == 1

        # Jump forward so the 0.50 entry is pruned
        alerts = sm.update({"tok1": 0.60}, timestamp=115.0)
        # Now both points are at 0.60 ⇒ 0 % change
        assert alerts == []

    def test_multiple_tokens_independent(self):
        """Alerts should be independent per token."""
        sm = StateManager(window_seconds=180, threshold_pct=15.0)

        sm.update({"tok1": 0.50, "tok2": 1.00}, timestamp=1000.0)
        alerts = sm.update({"tok1": 0.60, "tok2": 1.01}, timestamp=1010.0)

        # tok1 = +20 %, tok2 = +1 %
        assert len(alerts) == 1
        assert alerts[0]["token_id"] == "tok1"

    def test_tracked_count(self):
        sm = StateManager()
        sm.update({"a": 0.1, "b": 0.2, "c": 0.3}, timestamp=1.0)
        assert sm.tracked_count() == 3


# =====================================================================
# Fetcher error-handling tests (mocked network)
# =====================================================================

class TestFetcherErrorHandling:
    """Test that the Fetcher module degrades gracefully."""

    def test_rate_limit_returns_empty(self, monkeypatch):
        """A 429 response should return an empty dict, not crash."""
        from unittest.mock import MagicMock
        import fetcher as fetcher_mod

        # Patch time.sleep so the test doesn't actually wait
        monkeypatch.setattr(time, "sleep", lambda _: None)

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        f = fetcher_mod.Fetcher(session=mock_session)
        result = f.fetch_midpoints(["tok1"])
        assert result == {}

    def test_network_error_returns_empty(self, monkeypatch):
        """A network exception should be caught and return empty."""
        import requests as req_mod
        from unittest.mock import MagicMock
        import fetcher as fetcher_mod

        monkeypatch.setattr(time, "sleep", lambda _: None)

        mock_session = MagicMock()
        mock_session.get.side_effect = req_mod.exceptions.ConnectionError("offline")

        f = fetcher_mod.Fetcher(session=mock_session)
        result = f.fetch_midpoints(["tok1"])
        assert result == {}

    def test_successful_midpoint_parse(self, monkeypatch):
        """A well-formed response should be parsed into a float dict."""
        from unittest.mock import MagicMock
        import fetcher as fetcher_mod

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"tok1": "0.52", "tok2": "0.73"}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        f = fetcher_mod.Fetcher(session=mock_session)
        result = f.fetch_midpoints(["tok1", "tok2"])

        assert result == pytest.approx({"tok1": 0.52, "tok2": 0.73})
