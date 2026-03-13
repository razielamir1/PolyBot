"""
dashboard_store.py — Thread-safe shared state between the main bot loop and the Flask dashboard.

The module-level `store` singleton is written by the main loop thread and read by Flask worker
threads. All public methods acquire an RLock internally, so callers never need to manage locking.
"""

import threading
from collections import deque
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


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
    ) -> None:
        with self._lock:
            self._bot_status["cycle_count"] = cycle
            self._bot_status["last_update"] = _now_iso()
            self._bot_status["tokens_tracked"] = len(prices)

            for token_id, price in prices.items():
                lbl = token_to_label.get(token_id, token_id[:12] + "…")
                ev_lbl = (token_to_event_label or {}).get(token_id, lbl)
                entry = self._market_stats.setdefault(
                    token_id,
                    {
                        "label": lbl,
                        "event_label": ev_lbl,
                        "current_price": price,
                        "pct_change": None,
                        "alert_count": 0,
                    },
                )
                entry["current_price"] = price
                entry["label"] = lbl
                entry["event_label"] = ev_lbl

    def record_alerts(self, alerts: list[dict]) -> None:
        with self._lock:
            for a in alerts:
                token_id = a["token_id"]
                entry = self._market_stats.get(token_id)
                if entry:
                    entry["pct_change"] = a["pct_change"]
                    entry["alert_count"] = entry.get("alert_count", 0) + 1

                self._alert_feed.appendleft(
                    {
                        "time": _now_iso(),
                        "label": a.get("label", token_id),
                        "event_label": a.get("event_label", a.get("label", token_id)),
                        "token_id": token_id,
                        "pct_change": a["pct_change"],
                        "old_price": a["oldest_price"],
                        "new_price": a["latest_price"],
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
            msg = "הודעת בדיקה נשלחה לטלגרם ✅" if ok else "שליחה נכשלה ❌ — בדוק token ו-chat_id"
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

    def snapshot(self) -> dict:
        """Return a serialisable copy of all dashboard data."""
        with self._lock:
            return {
                "bot_status": dict(self._bot_status),
                "market_stats": [dict(v) for v in self._market_stats.values()],
                "alert_feed": list(self._alert_feed),
                "last_action_msg": self._last_action_msg,
                "threshold": self._threshold,
            }


# Module-level singleton — import this in main.py and dashboard.py
store = DashboardStore()
