"""
fetcher.py — Data fetching module for Polymarket.

Uses the Gamma API **``/events``** endpoint to discover Politics events
with their *aggregated* volume (matching the website), then extracts
child-market CLOB token IDs for real-time price polling via the CLOB API.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Exponential back-off defaults
_INITIAL_BACKOFF = 1.0   # seconds
_MAX_BACKOFF = 60.0       # seconds
_BACKOFF_FACTOR = 2.0

# Filtering defaults
DEFAULT_MIN_VOLUME = 100_000      # USD
DEFAULT_TOP_N = 50                # max events to track

# Keywords that identify a Politics-related event
_POLITICS_KEYWORDS = [
    "election", "president", "nominee", "fed", "congress",
    "senate", "governor", "trump", "democrat", "republican",
    "primaries", "midterm", "political", "vote", "ballot",
]


class Fetcher:
    """Handles all HTTP interaction with Polymarket APIs."""

    def __init__(self, session: requests.Session | None = None, workers: int = 50):
        self.session = session or requests.Session()
        self._backoff = _INITIAL_BACKOFF
        self._workers = workers
        # Thread-local storage so each worker thread gets its own Session
        self._local = __import__("threading").local()

    # ------------------------------------------------------------------
    # Event discovery (Gamma API /events)
    # ------------------------------------------------------------------
    def fetch_politics_events(
        self,
        limit: int = 100,
        min_volume: float = DEFAULT_MIN_VOLUME,
        top_n: int = DEFAULT_TOP_N,
    ) -> list[dict[str, Any]]:
        """Return the *top_n* active Politics events whose **aggregated**
        volume exceeds *min_volume* (USD), sorted by volume descending.
        """
        url = f"{GAMMA_API_BASE}/events"
        params = {
            "active": "true",
            "closed": "false",
            "order": "volume",
            "ascending": "false",
            "limit": limit,
            "tag": "Politics",
        }
        tag_events = self._get_json(url, params=params)
        if not isinstance(tag_events, list):
            tag_events = []

        params_global = {
            "active": "true",
            "closed": "false",
            "order": "volume",
            "ascending": "false",
            "limit": limit,
        }
        global_events = self._get_json(url, params=params_global)
        if not isinstance(global_events, list):
            global_events = []

        seen_ids: set[str] = set()
        merged: list[dict[str, Any]] = []

        for event in tag_events + global_events:
            eid = str(event.get("id", ""))
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)

            tags = str(event.get("tags", "") or "").lower()
            title = str(event.get("title", "") or "").lower()
            slug = str(event.get("slug", "") or "").lower()
            combined = f"{tags} {title} {slug}"

            is_politics = "politics" in combined or any(
                kw in combined for kw in _POLITICS_KEYWORDS
            )
            if not is_politics:
                continue

            merged.append(event)

        filtered: list[dict[str, Any]] = []
        for event in merged:
            try:
                volume = float(event.get("volume", 0) or 0)
            except (ValueError, TypeError):
                volume = 0.0
            if volume >= min_volume:
                filtered.append(event)

        filtered.sort(
            key=lambda e: float(e.get("volume", 0) or 0),
            reverse=True,
        )
        filtered = filtered[:top_n]

        logger.info(
            "Events: %d politics filtered → %d pass $%s volume → keeping top %d.",
            len(merged),
            len(filtered),
            f"{min_volume:,.0f}",
            len(filtered),
        )
        return filtered

    # ------------------------------------------------------------------
    # Token extraction
    # ------------------------------------------------------------------
    def extract_token_ids(
        self,
        events: list[dict[str, Any]],
    ) -> tuple[list[str], dict[str, str], dict[str, str], dict[str, str]]:
        """Extract CLOB token IDs from the child markets of each event."""
        token_ids: list[str] = []
        token_to_label: dict[str, str] = {}
        token_to_url: dict[str, str] = {}
        token_to_event_label: dict[str, str] = {}

        for event in events:
            title = event.get("title", "Unknown event")
            slug = event.get("slug", "")
            url = f"https://polymarket.com/event/{slug}" if slug else ""
            markets = event.get("markets", [])
            if not isinstance(markets, list):
                continue

            for market in markets:
                clob_ids = market.get("clobTokenIds")
                if clob_ids:
                    if isinstance(clob_ids, str):
                        try:
                            clob_ids = json.loads(clob_ids)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    if isinstance(clob_ids, list):
                        market_label = market.get("groupItemTitle") or market.get("question") or title
                        for tid in clob_ids:
                            tid_str = str(tid)
                            token_ids.append(tid_str)
                            token_to_label[tid_str] = market_label
                            token_to_url[tid_str] = url
                            token_to_event_label[tid_str] = title

        seen: set[str] = set()
        unique: list[str] = []
        for tid in token_ids:
            if tid not in seen:
                seen.add(tid)
                unique.append(tid)
        return unique, token_to_label, token_to_url, token_to_event_label

    # ------------------------------------------------------------------
    # Price fetching (CLOB API)
    # ------------------------------------------------------------------
    def fetch_midpoints(self, token_ids: list[str]) -> dict[str, float]:
        """Return ``{token_id: midpoint_price}`` for the given tokens.

        Fetches all tokens concurrently using a thread pool.
        Each worker thread uses its own requests.Session for thread safety.
        Skips 404 errors immediately without retry.
        """
        if not token_ids:
            return {}

        def _fetch_one(tid: str) -> tuple[str, float] | None:
            # Per-thread session (created once per thread, reused across calls)
            session = getattr(self._local, "session", None)
            if session is None:
                self._local.session = requests.Session()
                session = self._local.session

            url = f"{CLOB_API_BASE}/midpoint"
            try:
                resp = session.get(url, params={"token_id": tid}, timeout=10.0)
                if resp.status_code in (404, 400):
                    return None
                if resp.status_code == 429:
                    logger.warning("Rate-limited fetching %s", tid)
                    return None
                resp.raise_for_status()
                data = resp.json()
                mid = data.get("mid") if isinstance(data, dict) else data
                if mid is not None:
                    return (tid, float(mid))
            except Exception:
                pass
            return None

        prices: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(_fetch_one, tid): tid for tid in token_ids}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    prices[result[0]] = result[1]
        return prices

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_json(
        self,
        url: str,
        params: Any = None,
        timeout: float = 15.0,
        skip_404: bool = False,
    ) -> Any | None:
        """GET *url* and return parsed JSON, with back-off on transient errors."""
        try:
            resp = self.session.get(url, params=params, timeout=timeout)

            # Rule: Skip 404 immediately
            if resp.status_code == 404 and skip_404:
                return None

            if resp.status_code == 429:
                self._handle_rate_limit()
                return None

            resp.raise_for_status()
            self._reset_backoff()
            return resp.json()

        except requests.exceptions.HTTPError as exc:
            # If 404 was raised but not caught by status_code check
            if exc.response is not None and exc.response.status_code == 404:
                return None
            logger.error("HTTP error for %s: %s", url, exc)
            return None
        except requests.exceptions.RequestException as exc:
            logger.error("Request failed for %s: %s", url, exc)
            self._handle_rate_limit()
            return None

    def _handle_rate_limit(self) -> None:
        logger.warning("Backing off for %.1f s …", self._backoff)
        time.sleep(self._backoff)
        self._backoff = min(self._backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    def _reset_backoff(self) -> None:
        self._backoff = _INITIAL_BACKOFF
