"""
alert.py — Telegram alerting module.

Sends formatted messages via the Telegram Bot API and enforces a
per-token cooldown to prevent flooding.
"""

import logging
import time
import requests

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

        # {token_id: last_alert_timestamp}
        self._last_alert: dict[str, float] = {}

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
        cooldown_key = alert.get("event_label") or alert.get("label") or token_id
        now = time.time()

        # Cooldown check (event-level: one alert per event per cooldown period)
        last = self._last_alert.get(cooldown_key, 0)
        if now - last < self.cooldown_seconds:
            logger.debug("Cooldown active for %s — skipping alert", cooldown_key)
            return False

        text = self._format_message(alert)
        ok = self._send_message(text)
        if ok:
            self._last_alert[cooldown_key] = now
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
    def _format_message(alert: dict) -> str:
        pct = alert["pct_change"]
        # Use more descriptive labels if available
        # Note: label (market title) should be passed in alert dict if possible
        label = alert.get("label", alert["token_id"])
        event_label = alert.get("event_label", label)
        oldest = alert["oldest_price"]
        latest = alert["latest_price"]

        win_sec = alert.get("window_seconds", 300)
        win_label = f"{round(win_sec / 60)} min" if win_sec >= 60 else f"{int(win_sec)} sec"
        url = alert.get("url", "")
        link_line = f"\n<a href=\"{url}\">🔗 View on Polymarket</a>" if url else ""

        # Show outcome line only when it differs from the event title
        outcome_line = f"\n<b>Outcome:</b> {label}" if label != event_label else ""
        return (
            f"🚀 <b>Polymarket Price Alert</b>\n\n"
            f"<b>Market:</b> {event_label}"
            f"{outcome_line}\n"
            f"<b>Change:</b> <code>+{pct:.2f}%</code> in {win_label}\n"
            f"<b>Price:</b> <code>{oldest:.4f} → {latest:.4f}</code>"
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
