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


def _scan_markets(fetcher, fetch_limit, min_volume, top_n, min_market_volume=0.0):
    """Fetch events and extract token IDs."""
    events = fetcher.fetch_politics_events(
        limit=fetch_limit,
        min_volume=min_volume,
        top_n=top_n,
    )
    token_ids, token_to_label, token_to_url, token_to_event_label, token_to_mkt_volume, token_to_end_date, token_to_category = \
        fetcher.extract_token_ids(events, min_market_volume=min_market_volume)
    return events, token_ids, token_to_label, token_to_url, token_to_event_label, token_to_mkt_volume, token_to_end_date, token_to_category


def main() -> None:
    load_dotenv()

    # ── Config ──────────────────────────────────────────────────────
    bot_token        = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id          = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    poll_interval    = float(os.getenv("POLL_INTERVAL_SECONDS", "30"))
    threshold_pct    = float(os.getenv("PRICE_CHANGE_THRESHOLD_PCT", "10.0"))
    min_volume       = float(os.getenv("MIN_VOLUME_USD", "100000"))
    top_n            = int(os.getenv("TOP_N_MARKETS", "50"))
    alert_cooldown      = float(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))
    max_alerts_cycle    = int(os.getenv("MAX_ALERTS_PER_CYCLE", "3"))
    fetch_limit         = int(os.getenv("FETCH_LIMIT", "100"))
    min_market_volume   = float(os.getenv("MIN_MARKET_VOLUME_USD", "5000"))
    fetch_workers    = int(os.getenv("FETCH_WORKERS", "50"))
    dashboard_enabled = os.getenv("DASHBOARD_ENABLED", "false").lower() == "true"
    # Railway assigns PORT; fall back to DASHBOARD_PORT for local use
    dashboard_port   = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "5588")))

    volume_spike_usd    = float(os.getenv("VOLUME_SPIKE_USD", "25000"))
    volume_check_every  = int(os.getenv("VOLUME_CHECK_EVERY", "10"))   # cycles
    volume_alert_cooldown = float(os.getenv("VOLUME_ALERT_COOLDOWN_SECONDS", "3600"))

    if not bot_token or not chat_id:
        logger.error("Error: Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        sys.exit(1)

    # ── Initialise modules ──────────────────────────────────────────
    fetcher = Fetcher(workers=fetch_workers)
    def _make_states(thr):
        return [
            StateManager(window_seconds=60,  threshold_pct=thr),
            StateManager(window_seconds=300, threshold_pct=thr),
            StateManager(window_seconds=600, threshold_pct=thr),
            StateManager(window_seconds=900, threshold_pct=thr),
        ]
    states  = _make_states(threshold_pct)
    alerter = TelegramAlerter(bot_token=bot_token, chat_id=chat_id, cooldown_seconds=alert_cooldown)

    # ── Dashboard (optional) ────────────────────────────────────────
    if dashboard_enabled:
        from dashboard import start_dashboard
        from dashboard_store import store
        store.register_alerter(alerter)
        store.init_threshold(threshold_pct)
        store.init_volume_settings(volume_spike_usd, volume_check_every, volume_alert_cooldown)
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
    events, token_ids, token_to_label, token_to_url, token_to_event_label, token_to_mkt_volume, token_to_end_date, token_to_category = \
        _scan_markets(fetcher, fetch_limit, min_volume, top_n, min_market_volume)

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

    # ── Volume spike tracking ────────────────────────────────────────
    prev_event_volumes: dict[str, float] = {
        str(e["id"]): float(e.get("volume", 0) or 0) for e in events
    }
    event_id_to_label: dict[str, str] = {
        str(e["id"]): e.get("title", "Unknown") for e in events
    }
    event_id_to_slug: dict[str, str] = {
        str(e["id"]): e.get("slug", "") for e in events
    }
    last_volume_alert: dict[str, float] = {}  # event_id → timestamp

    # ── Main loop ───────────────────────────────────────────────────
    logger.info(f"Monitoring Begin: {len(token_ids)} tokens. Threshold: {threshold_pct}%")

    cycle = 0
    try:
        while True:
            cycle += 1

            # Check if dashboard requested a market refresh or threshold change
            if dashboard_enabled:
                from dashboard_store import store
                new_thresh = store.consume_threshold_change()
                if new_thresh is not None:
                    threshold_pct = new_thresh
                    states = _make_states(threshold_pct)
                    logger.info(f"Threshold updated to {threshold_pct}%")
                if store.consume_refresh_request():
                    logger.info("Dashboard triggered market refresh — re-scanning...")
                    events, token_ids, token_to_label, token_to_url, token_to_event_label, token_to_mkt_volume, token_to_end_date, token_to_category = \
                        _scan_markets(fetcher, fetch_limit, min_volume, top_n, min_market_volume)
                    states = _make_states(threshold_pct)
                    logger.info(f"Refresh complete: {len(token_ids)} tokens tracked.")
                    store.set_tokens_tracked(len(token_ids))
                    store.set_action_msg(f"רענון הושלם ✅ — {len(token_ids)} טוקנים פעילים")

                new_vol_cfg = store.consume_volume_settings_change()
                if new_vol_cfg:
                    volume_spike_usd      = new_vol_cfg["spike_usd"]
                    volume_check_every    = new_vol_cfg["check_every"]
                    volume_alert_cooldown = new_vol_cfg["cooldown"]
                    logger.info(f"Volume settings updated: spike=${volume_spike_usd:,.0f}, every={volume_check_every} cycles, cooldown={volume_alert_cooldown}s")

            # ── Volume spike check (every N cycles) ─────────────────
            if cycle % volume_check_every == 0:
                fresh_events = fetcher.fetch_politics_events(
                    limit=fetch_limit, min_volume=min_volume, top_n=top_n
                )
                now = time.time()
                for ev in fresh_events:
                    eid = str(ev["id"])
                    new_vol = float(ev.get("volume", 0) or 0)
                    old_vol = prev_event_volumes.get(eid, new_vol)
                    delta = new_vol - old_vol
                    if delta >= volume_spike_usd:
                        last_sent = last_volume_alert.get(eid, 0)
                        if now - last_sent >= volume_alert_cooldown:
                            label = ev.get("title", event_id_to_label.get(eid, "Unknown"))
                            slug  = ev.get("slug",  event_id_to_slug.get(eid, ""))
                            url   = f"https://polymarket.com/event/{slug}" if slug else ""
                            window_min = int(volume_check_every * poll_interval / 60) or 1
                            msg = (
                                f"💰 <b>Volume Spike!</b>\n"
                                f"<b>{label}</b>\n"
                                f"+{_fmt_volume(delta)} in the last ~{window_min} min\n"
                                f"📊 Total: {_fmt_volume(new_vol)}"
                                + (f"\n<a href='{url}'>View on Polymarket</a>" if url else "")
                            )
                            alerter._send_message(msg)
                            last_volume_alert[eid] = now
                            logger.info(f"💰 VOLUME SPIKE: {label} +{_fmt_volume(delta)}")
                    prev_event_volumes[eid] = new_vol

            prices = fetcher.fetch_midpoints(token_ids)

            if prices:
                # Merge alerts from all windows — keep best score per token
                best: dict[str, dict] = {}
                pct_10m: dict[str, float] = {}
                for sm in states:
                    window_alerts = sm.update(prices)
                    for a in window_alerts:
                        tid = a["token_id"]
                        score = abs(a["pct_change"]) * (300.0 / a["window_seconds"])
                        a["score"] = round(score, 2)
                        if tid not in best or a["score"] > best[tid]["score"]:
                            best[tid] = a
                        if a["window_seconds"] == 600:
                            pct_10m[tid] = a["pct_change"]
                alerts = list(best.values())

                if alerts:
                    # Enrich alerts with metadata
                    for a in alerts:
                        tid = a["token_id"]
                        a["label"]        = token_to_label.get(tid, "Unknown")
                        a["url"]          = token_to_url.get(tid, "")
                        a["event_label"]  = token_to_event_label.get(tid, a["label"])
                        a["threshold_pct"] = threshold_pct
                        a["mkt_volume"]   = token_to_mkt_volume.get(tid, 0)
                        a["end_date"]     = token_to_end_date.get(tid, "")
                        logger.info(f"🚀 SPIKE: {a['label']} (+{a['pct_change']}% / {a['window_seconds']}s / score={a['score']})")

                    # Related markets: other alerts from same event in this cycle
                    event_alerts: dict[str, list] = {}
                    for a in alerts:
                        event_alerts.setdefault(a["event_label"], []).append(a)
                    for a in alerts:
                        a["related"] = [x for x in event_alerts[a["event_label"]] if x["token_id"] != a["token_id"]][:3]

                    top_alerts = sorted(alerts, key=lambda x: x["score"], reverse=True)[:max_alerts_cycle]
                    alerter.send_alerts(top_alerts)
                else:
                    logger.info(f"Cycle {cycle}: {len(prices)} prices - No spikes.")

                if dashboard_enabled:
                    from dashboard_store import store
                    store.record_cycle(cycle, prices, token_to_label, token_to_event_label, token_to_category, pct_10m)
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
