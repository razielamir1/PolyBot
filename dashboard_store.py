"""
dashboard_store.py — Thread-safe shared state between the main bot loop and the Flask dashboard.

The module-level `store` singleton is written by the main loop thread and read by Flask worker
threads. All public methods acquire an RLock internally, so callers never need to manage locking.
"""

import os
import threading
import time as _time
from collections import deque
from datetime import datetime, timedelta, timezone

_TZ_OFFSET = timedelta(hours=int(os.getenv("DISPLAY_TIMEZONE_OFFSET", "2")))


def _now_iso() -> str:
    tz = timezone(_TZ_OFFSET)
    return datetime.now(tz).strftime("%H:%M:%S")


class DashboardStore:
    """Single source of truth for the live dashboard."""

    MAX_FEED = 200  # maximum alert entries kept in memory

    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._bot_status: dict = {
            "running": True,
            "cycle_count": 0,
            "last_update": "—",
            "tokens_tracked": 0,
            "start_time": _now_iso(),
        }

        # token_id → {label, current_price, pct_change, alert_count}
        self._market_stats: dict[str, dict] = {}

        # newest-first ring buffer of alert dicts
        self._alert_feed: deque[dict] = deque(maxlen=self.MAX_FEED)

        # Reference to TelegramAlerter for manual trigger from dashboard
        self._alerter = None

        # Event flag: when set, main loop should re-scan markets
        self._refresh_markets_flag = threading.Event()

        # Event flag: when set, main loop should run a price cycle immediately
        self._force_cycle_flag = threading.Event()

        # Last manual action result message
        self._last_action_msg: str = ""

        # Muted event labels — alerts for these are suppressed
        self._muted_labels: set[str] = set()

        # Analytics: tokens that have ever triggered an alert ("hot markets")
        self._watched_tokens: set[str] = set()
        # token_id → deque of {t, p} — up to 2 hours of history at 30s intervals
        self._price_history: dict[str, deque] = {}
        # token_id → list of time strings when alerts fired
        self._alert_times: dict[str, list] = {}
        # token_id → url (latest known)
        self._token_url: dict[str, str] = {}

        # Runtime-adjustable threshold (set by main.py on startup)
        self._threshold: float = 10.0
        self._pending_threshold: float | None = None

    # ------------------------------------------------------------------
    # Writers (called from the main loop thread)
    # ------------------------------------------------------------------

    def register_alerter(self, alerter) -> None:
        """Store a reference to TelegramAlerter so the dashboard can trigger it."""
        with self._lock:
            self._alerter = alerter

    def set_tokens_tracked(self, count: int) -> None:
        with self._lock:
            self._bot_status["tokens_tracked"] = count

    def record_cycle(
        self,
        cycle: int,
        prices: dict[str, float],
        token_to_label: dict[str, str],
        token_to_event_label: dict[str, str] | None = None,
        token_to_category: dict[str, str] | None = None,
        token_to_pct_10m: dict[str, float] | None = None,
    ) -> None:
        with self._lock:
            self._bot_status["cycle_count"] = cycle
            self._bot_status["last_update"] = _now_iso()
            self._bot_status["tokens_tracked"] = len(prices)

            for token_id, price in prices.items():
                lbl = token_to_label.get(token_id, token_id[:12] + "…")
                ev_lbl = (token_to_event_label or {}).get(token_id, lbl)
                cat = (token_to_category or {}).get(token_id, "Other")
                entry = self._market_stats.setdefault(
                    token_id,
                    {
                        "label": lbl,
                        "event_label": ev_lbl,
                        "current_price": price,
                        "pct_change": None,
                        "alert_count": 0,
                        "category": cat,
                    },
                )
                entry["current_price"] = price
                entry["label"] = lbl
                entry["event_label"] = ev_lbl
                entry["category"] = cat
                if token_to_pct_10m and token_id in token_to_pct_10m:
                    entry["pct_10m"] = token_to_pct_10m[token_id]

                # Record price history for hot markets only
                if token_id in self._watched_tokens:
                    if token_id not in self._price_history:
                        self._price_history[token_id] = deque(maxlen=240)
                    self._price_history[token_id].append({"t": _now_iso(), "p": price})

    def record_alerts(self, alerts: list[dict]) -> None:
        with self._lock:
            for a in alerts:
                token_id = a["token_id"]
                entry = self._market_stats.get(token_id)
                if entry:
                    entry["pct_change"] = a["pct_change"]
                    entry["alert_count"] = entry.get("alert_count", 0) + 1

                # Track as hot market
                self._watched_tokens.add(token_id)
                url = a.get("url", "")
                if url:
                    self._token_url[token_id] = url
                t = _now_iso()
                if token_id not in self._alert_times:
                    self._alert_times[token_id] = []
                self._alert_times[token_id].append(t)

                self._alert_feed.appendleft(
                    {
                        "time": t,
                        "ts": _time.time(),
                        "label": a.get("label", token_id),
                        "event_label": a.get("event_label", a.get("label", token_id)),
                        "token_id": token_id,
                        "pct_change": a["pct_change"],
                        "old_price": a["oldest_price"],
                        "new_price": a["latest_price"],
                        "url": url,
                    }
                )

    def set_action_msg(self, msg: str) -> None:
        with self._lock:
            self._last_action_msg = msg

    def init_threshold(self, pct: float) -> None:
        """Called once by main.py on startup to set initial threshold."""
        with self._lock:
            self._threshold = pct

    def set_threshold(self, pct: float) -> None:
        """Called by dashboard to request a runtime threshold change."""
        with self._lock:
            self._threshold = pct
            self._pending_threshold = pct
            self._force_cycle_flag.set()  # wake sleep immediately

    def get_threshold(self) -> float:
        with self._lock:
            return self._threshold

    def consume_threshold_change(self) -> float | None:
        """Called by main loop — returns new threshold if changed, else None."""
        with self._lock:
            val = self._pending_threshold
            self._pending_threshold = None
            return val

    # ------------------------------------------------------------------
    # Trigger methods (called from Flask worker threads)
    # ------------------------------------------------------------------

    def trigger_telegram_test(self) -> dict:
        """Send a test message via Telegram. Returns {ok, message}."""
        with self._lock:
            alerter = self._alerter
        if alerter is None:
            return {"ok": False, "message": "Alerter not registered yet"}
        try:
            ok = alerter._send_message(
                "🔔 <b>PolyBot Dashboard Test</b>\n\nהבוט פעיל ומחובר תקין ✅"
            )
            msg = "הודעת בדיקה נשלחה לטלגרם ✅" if ok else "שליחה נכשלה ❌ — rate limit של טלגרם, נסה שוב בעוד כמה שניות"
            self.set_action_msg(msg)
            return {"ok": ok, "message": msg}
        except Exception as e:
            msg = f"שגיאה: {e}"
            self.set_action_msg(msg)
            return {"ok": False, "message": msg}

    def trigger_market_refresh(self) -> dict:
        """Signal the main loop to re-scan markets on next cycle."""
        self._refresh_markets_flag.set()
        self._force_cycle_flag.set()  # also wake the sleep immediately
        msg = "בקשת רענון שווקים נשלחה ✅ — תופעל עכשיו"
        self.set_action_msg(msg)
        return {"ok": True, "message": msg}

    def trigger_force_cycle(self) -> dict:
        """Signal the main loop to run a price fetch cycle immediately."""
        self._force_cycle_flag.set()
        msg = "מחזור בדיקה הופעל ✅ — מביא מחירים עכשיו..."
        self.set_action_msg(msg)
        return {"ok": True, "message": msg}

    def consume_refresh_request(self) -> bool:
        """Called by main loop — returns True if a refresh was requested, clears the flag."""
        if self._refresh_markets_flag.is_set():
            self._refresh_markets_flag.clear()
            return True
        return False

    def consume_force_cycle(self) -> bool:
        """Called by main loop sleep loop — returns True if immediate cycle was requested."""
        if self._force_cycle_flag.is_set():
            self._force_cycle_flag.clear()
            return True
        return False

    # ------------------------------------------------------------------
    # Reader (called from Flask worker threads)
    # ------------------------------------------------------------------

    def init_muted(self, labels: list[str]) -> None:
        """Load persisted muted labels on startup."""
        with self._lock:
            self._muted_labels = set(labels)

    def mute(self, event_label: str) -> None:
        with self._lock:
            self._muted_labels.add(event_label)

    def unmute(self, event_label: str) -> None:
        with self._lock:
            self._muted_labels.discard(event_label)

    def is_muted(self, event_label: str) -> bool:
        with self._lock:
            return event_label in self._muted_labels

    def get_muted(self) -> list[str]:
        with self._lock:
            return sorted(self._muted_labels)

    def get_hot_markets(self) -> list[dict]:
        """Return hot-market summaries sorted by alert_count desc."""
        with self._lock:
            result = []
            for token_id in self._watched_tokens:
                stats = self._market_stats.get(token_id, {})
                result.append({
                    "token_id": token_id,
                    "label": stats.get("label", token_id[:12] + "…"),
                    "event_label": stats.get("event_label", stats.get("label", "")),
                    "current_price": stats.get("current_price"),
                    "pct_change": stats.get("pct_change"),
                    "alert_count": stats.get("alert_count", 0),
                    "url": self._token_url.get(token_id, ""),
                    "history_len": len(self._price_history.get(token_id, [])),
                    "category": stats.get("category", "Other"),
                })
            result.sort(key=lambda x: x["alert_count"], reverse=True)
            return result

    def get_all_markets(self) -> list[dict]:
        """Return all tracked market summaries (not just hot), sorted by alert_count desc. Capped at 100."""
        with self._lock:
            result = []
            for token_id, stats in self._market_stats.items():
                result.append({
                    "token_id": token_id,
                    "label": stats.get("label", token_id[:12] + "…"),
                    "event_label": stats.get("event_label", stats.get("label", "")),
                    "current_price": stats.get("current_price"),
                    "pct_change": stats.get("pct_change"),
                    "alert_count": stats.get("alert_count", 0),
                    "url": self._token_url.get(token_id, ""),
                    "category": stats.get("category", "Other"),
                })
            result.sort(key=lambda x: x["alert_count"], reverse=True)
            return result[:100]

    def get_chart_data(self, token_id: str) -> dict | None:
        """Return price history and alert markers for one hot market."""
        with self._lock:
            if token_id not in self._watched_tokens:
                return None
            stats = self._market_stats.get(token_id, {})
            history = list(self._price_history.get(token_id, []))
            alert_times = list(self._alert_times.get(token_id, []))
            return {
                "token_id": token_id,
                "label": stats.get("label", token_id[:12] + "…"),
                "event_label": stats.get("event_label", stats.get("label", "")),
                "url": self._token_url.get(token_id, ""),
                "history": history,
                "alert_times": alert_times,
            }

    def add_watch_token(self, token_id: str) -> None:
        """Start tracking price history for a watchlisted token that hasn't alerted yet."""
        with self._lock:
            self._watched_tokens.add(token_id)

    def snapshot(self) -> dict:
        """Return a serialisable copy of all dashboard data."""
        with self._lock:
            return {
                "bot_status": dict(self._bot_status),
                "market_stats": [{"token_id": k, **dict(v), "url": self._token_url.get(k, "")} for k, v in self._market_stats.items()],
                "alert_feed": list(self._alert_feed),
                "last_action_msg": self._last_action_msg,
                "threshold": self._threshold,
            }


# Module-level singleton — import this in main.py and dashboard.py
store = DashboardStore()
