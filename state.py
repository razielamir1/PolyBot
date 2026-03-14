"""
state.py — Rolling-window state management.

Stores ``(timestamp, price)`` pairs in per-token deques and computes
the percentage price change over a configurable time window.
"""

import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

# Default window = 3 minutes
DEFAULT_WINDOW_SECONDS = 180


class PriceWindow:
    """Manages a single token's rolling price window."""

    __slots__ = ("token_id", "_window_seconds", "_data")

    def __init__(self, token_id: str, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self.token_id = token_id
        self._window_seconds = window_seconds
        # Each entry: (unix_timestamp, price)
        self._data: deque[tuple[float, float]] = deque()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def add(self, price: float, timestamp: float | None = None) -> None:
        """Append a price sample and prune stale data."""
        ts = timestamp if timestamp is not None else time.time()
        self._data.append((ts, price))
        self._prune(ts)

    def pct_change(self) -> float | None:
        """Return the % change from the *oldest* price in the window
        to the *newest*, or ``None`` if there is insufficient data.

        Result is signed: +15.0 means a 15 % increase.
        """
        if len(self._data) < 2:
            return None
        oldest_price = self._data[0][1]
        newest_price = self._data[-1][1]
        return (newest_price - oldest_price) * 100.0

    @property
    def latest_price(self) -> float | None:
        """Return the most recent price, or ``None``."""
        return self._data[-1][1] if self._data else None

    @property
    def size(self) -> int:
        return len(self._data)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _prune(self, now: float | None = None) -> None:
        """Remove entries older than the rolling window."""
        now = now if now is not None else time.time()
        cutoff = now - self._window_seconds
        while self._data and self._data[0][0] < cutoff:
            self._data.popleft()


class StateManager:
    """Container for all tracked tokens' price windows."""

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        threshold_pct: float = 15.0,
    ):
        self.window_seconds = window_seconds
        self.threshold_pct = threshold_pct
        self._windows: dict[str, PriceWindow] = {}

    def update(
        self,
        prices: dict[str, float],
        timestamp: float | None = None,
    ) -> list[dict]:
        """Ingest a batch of ``{token_id: price}`` and return a list of
        alerts for any token whose % change meets the threshold.

        Each alert dict contains:
        ``token_id``, ``pct_change``, ``oldest_price``, ``latest_price``.
        """
        alerts: list[dict] = []
        ts = timestamp if timestamp is not None else time.time()

        for token_id, price in prices.items():
            window = self._windows.get(token_id)
            if window is None:
                window = PriceWindow(token_id, self.window_seconds)
                self._windows[token_id] = window

            window.add(price, timestamp=ts)
            pct = window.pct_change()

            if pct is not None and pct >= self.threshold_pct:
                alerts.append(
                    {
                        "token_id": token_id,
                        "pct_change": round(pct, 2),
                        "oldest_price": window._data[0][1],
                        "latest_price": price,
                        "window_seconds": self.window_seconds,
                    }
                )
        return alerts

    def tracked_count(self) -> int:
        """Return the number of tokens currently being tracked."""
        return len(self._windows)
