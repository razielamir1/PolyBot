"""
alert.py — Telegram alerting module.

Sends formatted messages via the Telegram Bot API and enforces a
per-token cooldown to prevent flooding.
"""

import logging
import time
from datetime import datetime, timezone

import os

import requests

from dashboard_store import store as _store

# Import AI client lazily so the bot works without GEMINI_API_KEY
try:
    import ai_client as _ai
    _AI_AVAILABLE = True
except ImportError:
    _AI_AVAILABLE = False

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Minimum seconds between repeated alerts for the *same* token
_DEFAULT_ALERT_COOLDOWN = 300  # seconds — overridden by ALERT_COOLDOWN_SECONDS in .env


class TelegramAlerter:
    """Sends price-spike alerts to a Telegram chat."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        cooldown_seconds: float = _DEFAULT_ALERT_COOLDOWN,
        session: requests.Session | None = None,
    ):
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.cooldown_seconds = cooldown_seconds
        self.session = session or requests.Session()

        # {cooldown_key: last_alert_timestamp}
        self._last_alert: dict[str, float] = {}
        # {cooldown_key: price at last alert} — for price-anchor deduplication
        self._last_alert_price: dict[str, float] = {}

    def send_test_message(self) -> bool:
        """Send a simple message to confirm the bot is connected."""
        msg = "🤖 <b>Polymarket Bot Connected!</b>\nFiltering for Politics markets & monitoring prices..."
        return self._send_message(msg)

    def send_alert(self, alert: dict) -> bool:
        """Send a Telegram message for *alert* if the cooldown has elapsed.

        Returns ``True`` if the message was sent, ``False`` if it was
        suppressed or failed.
        """
        token_id = alert["token_id"]
        cooldown_key = token_id
        now = time.time()

        # Mute check
        if _store.is_muted(cooldown_key):
            logger.debug("Muted: %s — skipping alert", cooldown_key)
            return False

        # Minimum time buffer (prevents burst within same cycle)
        last = self._last_alert.get(cooldown_key, 0)
        if now - last < self.cooldown_seconds:
            logger.debug("Cooldown active for %s — skipping alert", cooldown_key)
            return False

        # Price anchor check: only alert if price moved enough from last alert price
        current_price = alert["latest_price"]
        threshold = alert.get("threshold_pct", 3.0)
        last_price = self._last_alert_price.get(cooldown_key)
        if last_price is not None:
            movement = abs(current_price - last_price) * 100
            if movement < threshold:
                logger.debug("Price anchor: only %.2fpp from last alert (%.4f→%.4f) — skipping",
                             movement, last_price, current_price)
                return False

        ai_summary = ""
        if _AI_AVAILABLE and os.getenv("AI_ALERT_SUMMARY", "true").lower() == "true":
            try:
                ai_summary = _ai.generate_alert_summary(alert)
            except Exception:
                pass

        text = self._format_message(alert, ai_summary=ai_summary)
        ok = self._send_message(text)
        if ok:
            self._last_alert[cooldown_key] = now
            self._last_alert_price[cooldown_key] = current_price
        return ok

    def send_alerts(self, alerts: list[dict]) -> int:
        """Send multiple alerts. Returns the count of messages sent."""
        sent = 0
        for alert in alerts:
            if self.send_alert(alert):
                sent += 1
        return sent

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_message(alert: dict, ai_summary: str = "") -> str:
        pct = alert["pct_change"]
        label = alert.get("label", alert["token_id"])
        event_label = alert.get("event_label", label)
        oldest = alert["oldest_price"]
        latest = alert["latest_price"]

        win_sec = alert.get("window_seconds", 300)
        win_label = f"{round(win_sec / 60)} min" if win_sec >= 60 else f"{int(win_sec)} sec"

        # Score badge
        score = alert.get("score", 0)
        badge = "⚡ חד" if score >= 20 else ("📈 חזק" if score >= 10 else "📊 מתון")

        # Market volume context
        mkt_vol = alert.get("mkt_volume", 0)
        if mkt_vol >= 1_000_000:
            vol_str = f"${mkt_vol / 1_000_000:.1f}M"
        elif mkt_vol >= 1_000:
            vol_str = f"${mkt_vol / 1_000:.0f}K"
        else:
            vol_str = ""
        vol_line = f"\n💰 <b>Volume:</b> {vol_str}" if vol_str else ""

        # Days to close
        days_line = ""
        end_date = alert.get("end_date", "")
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                d = (end_dt - datetime.now(timezone.utc)).days
                if d > 1:
                    days_line = f"\n📅 <b>נסגר בעוד:</b> {d} ימים"
                elif d == 1:
                    days_line = "\n📅 <b>נסגר מחר</b>"
                elif d == 0:
                    days_line = "\n📅 <b>נסגר היום</b>"
            except Exception:
                pass

        # Related markets
        related = alert.get("related", [])
        related_line = ""
        if related:
            parts = [f"{r.get('label', r['token_id'][:8])} {'+' if r['pct_change'] >= 0 else ''}{r['pct_change']:.1f}%" for r in related]
            related_line = f"\n📌 <b>גם זזו:</b> {' · '.join(parts)}"

        url = alert.get("url", "")
        link_line = f"\n<a href=\"{url}\">🔗 View on Polymarket</a>" if url else ""
        ai_line = f"\n\n💡 <i>{ai_summary}</i>" if ai_summary else ""

        outcome_line = f"\n<b>Outcome:</b> {label}" if label != event_label else ""
        return (
            f"🚀 <b>Polymarket Price Alert</b> [{badge}]\n\n"
            f"<b>Market:</b> {event_label}"
            f"{outcome_line}\n"
            f"<b>Change:</b> <code>+{pct:.2f}%</code> in {win_label}\n"
            f"<b>Price:</b> <code>{oldest*100:.1f}% → {latest*100:.1f}%</code>"
            f"{vol_line}"
            f"{days_line}"
            f"{related_line}"
            f"{ai_line}"
            f"{link_line}"
        )

    def _send_message(self, text: str) -> bool:
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error("Telegram API error %s: %s", resp.status_code, resp.text)
                return False
            return True
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False
