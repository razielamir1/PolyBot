"""
main.py — Polymarket Trend-Spotting Bot entry point.

Monitors top Politics events on Polymarket using aggregated volumes,
evaluates a rolling price window, and sends Telegram alerts on spikes.
"""

import logging
import os
import sys
import time

from dotenv import load_dotenv

from alert import TelegramAlerter
from fetcher import Fetcher
from state import StateManager

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polybot")


def _fmt_volume(vol: float) -> str:
    """Human-readable dollar volume (e.g. $789.3M, $1.2B)."""
    if vol >= 1_000_000_000:
        return f"${vol / 1_000_000_000:.1f}B"
    if vol >= 1_000_000:
        return f"${vol / 1_000_000:.1f}M"
    if vol >= 1_000:
        return f"${vol / 1_000:.1f}K"
    return f"${vol:,.0f}"


def _scan_markets(fetcher, fetch_limit, min_volume, top_n):
    """Fetch events and extract token IDs. Returns (events, token_ids, token_to_label)."""
    events = fetcher.fetch_politics_events(
        limit=fetch_limit,
        min_volume=min_volume,
        top_n=top_n,
    )
    token_ids, token_to_label = fetcher.extract_token_ids(events)
    return events, token_ids, token_to_label


def main() -> None:
    load_dotenv()

    # ── Config ──────────────────────────────────────────────────────
    bot_token        = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id          = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    poll_interval    = float(os.getenv("POLL_INTERVAL_SECONDS", "30"))
    threshold_pct    = float(os.getenv("PRICE_CHANGE_THRESHOLD_PCT", "10.0"))
    window_seconds   = float(os.getenv("ROLLING_WINDOW_SECONDS", "300"))
    min_volume       = float(os.getenv("MIN_VOLUME_USD", "100000"))
    top_n            = int(os.getenv("TOP_N_MARKETS", "50"))
    alert_cooldown   = float(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))
    fetch_limit      = int(os.getenv("FETCH_LIMIT", "100"))
    fetch_workers    = int(os.getenv("FETCH_WORKERS", "50"))
    dashboard_enabled = os.getenv("DASHBOARD_ENABLED", "false").lower() == "true"
    # Railway assigns PORT; fall back to DASHBOARD_PORT for local use
    dashboard_port   = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "5588")))

    if not bot_token or not chat_id:
        logger.error("Error: Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        sys.exit(1)

    # ── Initialise modules ──────────────────────────────────────────
    fetcher = Fetcher(workers=fetch_workers)
    state   = StateManager(window_seconds=window_seconds, threshold_pct=threshold_pct)
    alerter = TelegramAlerter(bot_token=bot_token, chat_id=chat_id, cooldown_seconds=alert_cooldown)

    # ── Dashboard (optional) ────────────────────────────────────────
    if dashboard_enabled:
        from dashboard import start_dashboard
        from dashboard_store import store
        store.register_alerter(alerter)
        start_dashboard(port=dashboard_port)
        logger.info(f"Dashboard running at http://localhost:{dashboard_port}")

    # ── Startup Notification ────────────────────────────────────────
    logger.info("Starting Polymarket Bot...")
    if alerter._send_message("Bot is Online! 🚀"):
        logger.info("Startup notification sent to Telegram.")
    else:
        logger.warning("Notification failed - check .env credentials.")

    # ── Discover high-volume Politics events ────────────────────────
    logger.info(f"Scanning for Politics events (Vol > {_fmt_volume(min_volume)})...")
    events, token_ids, token_to_label = _scan_markets(fetcher, fetch_limit, min_volume, top_n)

    if not token_ids:
        logger.error("No tracked tokens found. Shutdown.")
        sys.exit(1)

    logger.info("-" * 40)
    for i, event in enumerate(events, 1):
        title = event.get("title", "?")[:40]
        vol   = float(event.get("volume", 0) or 0)
        logger.info(f"{i:2}. {title:<40} {_fmt_volume(vol):>8}")
    logger.info("-" * 40)

    if dashboard_enabled:
        from dashboard_store import store
        store.set_tokens_tracked(len(token_ids))

    # ── Main loop ───────────────────────────────────────────────────
    logger.info(f"Monitoring Begin: {len(token_ids)} tokens. Threshold: {threshold_pct}%")

    cycle = 0
    try:
        while True:
            cycle += 1

            # Check if dashboard requested a market refresh
            if dashboard_enabled:
                from dashboard_store import store
                if store.consume_refresh_request():
                    logger.info("Dashboard triggered market refresh — re-scanning...")
                    events, token_ids, token_to_label = _scan_markets(
                        fetcher, fetch_limit, min_volume, top_n
                    )
                    state = StateManager(window_seconds=window_seconds, threshold_pct=threshold_pct)
                    logger.info(f"Refresh complete: {len(token_ids)} tokens tracked.")
                    store.set_tokens_tracked(len(token_ids))
                    store.set_action_msg(f"רענון הושלם ✅ — {len(token_ids)} טוקנים פעילים")

            prices = fetcher.fetch_midpoints(token_ids)

            if prices:
                alerts = state.update(prices)
                if alerts:
                    for a in alerts:
                        label = token_to_label.get(a["token_id"], "Unknown")
                        a["label"] = label
                        a["window_seconds"] = window_seconds
                        logger.info(f"🚀 SPIKE: {label} (+{a['pct_change']}%)")
                    alerter.send_alerts(alerts)
                else:
                    logger.info(f"Cycle {cycle}: {len(prices)} prices - No spikes.")

                if dashboard_enabled:
                    from dashboard_store import store
                    store.record_cycle(cycle, prices, token_to_label)
                    if alerts:
                        store.record_alerts(alerts)
            else:
                logger.warning(f"Cycle {cycle}: Connection issue or no prices.")

            # Interruptible sleep — wakes early if dashboard requests a force cycle
            if dashboard_enabled:
                from dashboard_store import store
                deadline = time.time() + poll_interval
                while time.time() < deadline:
                    time.sleep(1)
                    if store.consume_force_cycle():
                        break
            else:
                time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("Exiting...")


if __name__ == "__main__":
    main()
