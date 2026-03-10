"""
dashboard.py — Local web dashboard for the PolyBot.

Runs a Flask server in a daemon background thread so it never blocks
the main loop. The HTML/JS is served inline (no static files needed).

Routes:
  GET  /                      → Single-page dashboard (HTML)
  GET  /api/status            → Full snapshot as JSON (polled every 10s)
  GET  /api/feed              → Alert feed only as JSON (polled every 3s)
  POST /api/trigger-test      → Send a test Telegram message
  POST /api/refresh-markets   → Signal the main loop to re-scan markets
"""

import logging
import threading

from flask import Flask, jsonify, request

from dashboard_store import store

logger = logging.getLogger(__name__)

app = Flask(__name__)
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Dashboard HTML — vanilla JS, no CDN, RTL Hebrew layout
# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PolyBot Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --accent: #58a6ff; --orange: #f0883e;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }

  header {
    padding: 14px 24px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  header h1 { font-size: 17px; font-weight: 600; flex: 1; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--green); flex-shrink: 0; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  #last-refresh { font-size: 11px; color: var(--muted); }

  /* Action buttons */
  .action-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border);
    font-size: 12px; font-weight: 600; cursor: pointer;
    background: var(--card); color: var(--text);
    transition: background .15s, border-color .15s;
  }
  .btn:hover { background: #21262d; border-color: #58a6ff; }
  .btn-green { border-color: var(--green); color: var(--green); }
  .btn-green:hover { background: rgba(63,185,80,.12); }
  .btn-blue  { border-color: var(--accent); color: var(--accent); }
  .btn-blue:hover  { background: rgba(88,166,255,.12); }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  #actionMsg {
    font-size: 12px; padding: 5px 10px; border-radius: 5px;
    background: rgba(88,166,255,.1); border: 1px solid var(--accent);
    color: var(--accent); display: none;
  }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 24px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card-full { grid-column: 1 / -1; }
  .card h2 { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 700; background: rgba(63,185,80,.15); color: var(--green); }
  .badge-orange { background: rgba(240,136,62,.15); color: var(--orange); }

  .stat-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat { background: var(--bg); border-radius: 6px; padding: 10px 14px; }
  .stat-label { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .stat-value { font-size: 20px; font-weight: 700; color: var(--accent); }

  /* Tables */
  .tbl-wrap { overflow-x: auto; max-height: 360px; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; }
  th {
    position: sticky; top: 0; background: var(--card);
    text-align: right; color: var(--muted); font-size: 11px; font-weight: 600;
    text-transform: uppercase; padding: 6px 10px; border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td { padding: 8px 10px; border-bottom: 1px solid #21262d; font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1c2128; }

  .label-cell { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .price-cell { font-family: 'Consolas', monospace; font-size: 13px; }
  .up   { color: var(--green);  font-weight: 600; }
  .down { color: var(--red);    font-weight: 600; }
  .muted-cell { color: var(--muted); font-size: 12px; }
  .warming { color: var(--yellow); font-size: 11px; }

  /* Live feed */
  #feed { max-height: 340px; overflow-y: auto; }
  .feed-item {
    display: grid; grid-template-columns: 1fr auto auto;
    align-items: center; gap: 8px;
    padding: 9px 12px; border-radius: 6px; margin-bottom: 6px;
    background: var(--bg); border-right: 3px solid var(--green);
    animation: fadein .35s ease;
  }
  @keyframes fadein { from{opacity:0;transform:translateY(-5px)} to{opacity:1;transform:translateY(0)} }
  .feed-label { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .feed-pct   { font-weight: 700; color: var(--green); white-space: nowrap; font-size: 14px; }
  .feed-meta  { font-size: 11px; color: var(--muted); text-align: left; white-space: nowrap; }
  .empty { color: var(--muted); font-size: 13px; padding: 14px 0; text-align: center; }

  @media (max-width: 700px) {
    .grid { grid-template-columns: 1fr; }
    .card-full { grid-column: 1; }
  }
</style>
</head>
<body>

<header>
  <div class="dot" id="statusDot"></div>
  <h1>PolyBot Dashboard</h1>
  <span id="last-refresh"></span>
  <div class="action-bar">
    <button class="btn btn-green" id="btnTelegram" onclick="triggerTelegram()">
      📨 שלח בדיקת טלגרם
    </button>
    <button class="btn btn-blue" id="btnRefresh" onclick="triggerRefresh()">
      🔄 רענן שווקים
    </button>
    <span id="actionMsg"></span>
  </div>
</header>

<div class="grid">

  <!-- Bot Status -->
  <div class="card">
    <h2>סטטוס הבוט</h2>
    <div class="stat-grid">
      <div class="stat">
        <div class="stat-label">מחזורים שהושלמו</div>
        <div class="stat-value" id="cycleCount">—</div>
      </div>
      <div class="stat">
        <div class="stat-label">טוקנים במעקב</div>
        <div class="stat-value" id="tokenCount">—</div>
      </div>
      <div class="stat">
        <div class="stat-label">עדכון אחרון</div>
        <div class="stat-value" style="font-size:15px" id="lastUpdate">—</div>
      </div>
      <div class="stat">
        <div class="stat-label">הבוט פעיל מאז</div>
        <div class="stat-value" style="font-size:15px" id="startTime">—</div>
      </div>
    </div>
  </div>

  <!-- Live Alert Feed -->
  <div class="card">
    <h2>
      התראות חיות
      <span id="alertBadge" class="badge" style="display:none">0</span>
    </h2>
    <div id="feed"><div class="empty">ממתין להתראות...</div></div>
  </div>

  <!-- Market Stats -->
  <div class="card card-full">
    <h2>
      סטטיסטיקת שוק
      <span style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none">
        — מחירים הם הסתברויות (0%–100%)
      </span>
    </h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>שוק / אירוע</th>
          <th>מחיר נוכחי (%)</th>
          <th>שינוי בחלון (5 דק׳)</th>
          <th>התראות שנשלחו</th>
        </tr></thead>
        <tbody id="marketBody">
          <tr><td colspan="4" class="empty">ממתין לנתונים...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Alert History -->
  <div class="card card-full">
    <h2>היסטוריית התראות</h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>שעה (UTC)</th>
          <th>שוק / אירוע</th>
          <th>קפיצה</th>
          <th>מחיר ישן</th>
          <th>מחיר חדש</th>
        </tr></thead>
        <tbody id="historyBody">
          <tr><td colspan="5" class="empty">אין היסטוריה עדיין.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
  // ── helpers ──────────────────────────────────────────────────────
  const pct  = v => v == null ? null : (v * 100);
  const fmtP = v => v == null ? '—' : pct(v).toFixed(1) + '%';
  const fmtChg = v => v == null ? '<span class="warming">מחמם...</span>'
                                : `<span class="${v>=0?'up':'down'}">${v>=0?'+':''}${v.toFixed(2)}%</span>`;

  // ── known feed keys ──────────────────────────────────────────────
  let knownFeedIds = new Set();
  let totalAlerts  = 0;

  // ── /api/status (every 10s) ───────────────────────────────────────
  async function fetchStatus() {
    try {
      const r    = await fetch('/api/status');
      const data = await r.json();
      const s    = data.bot_status;

      document.getElementById('cycleCount').textContent = s.cycle_count;
      document.getElementById('tokenCount').textContent = s.tokens_tracked;
      document.getElementById('lastUpdate').textContent = s.last_update;
      document.getElementById('startTime').textContent  = s.start_time;
      document.getElementById('last-refresh').textContent =
        'רענון: ' + new Date().toLocaleTimeString('he-IL');

      // market stats
      const tbody = document.getElementById('marketBody');
      if (!data.market_stats.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">ממתין לנתונים...</td></tr>';
      } else {
        // sort: alerts desc → label asc
        data.market_stats.sort((a,b)=>(b.alert_count - a.alert_count)||a.label.localeCompare(b.label));
        tbody.innerHTML = data.market_stats.map(m => `
          <tr>
            <td class="label-cell" title="${m.label}">${m.label}</td>
            <td class="price-cell">${fmtP(m.current_price)}</td>
            <td>${fmtChg(m.pct_change)}</td>
            <td>${m.alert_count > 0
              ? `<span class="badge badge-orange">${m.alert_count}</span>`
              : '<span class="muted-cell">0</span>'}</td>
          </tr>`).join('');
      }

      // alert history
      const hbody = document.getElementById('historyBody');
      if (!data.alert_feed.length) {
        hbody.innerHTML = '<tr><td colspan="5" class="empty">אין היסטוריה עדיין.</td></tr>';
      } else {
        hbody.innerHTML = data.alert_feed.map(a => `
          <tr>
            <td class="muted-cell">${a.time}</td>
            <td class="label-cell" title="${a.label}">${a.label}</td>
            <td class="up">+${a.pct_change.toFixed(2)}%</td>
            <td class="price-cell">${fmtP(a.old_price)}</td>
            <td class="price-cell up">${fmtP(a.new_price)}</td>
          </tr>`).join('');
      }

      // last action message
      if (data.last_action_msg) {
        showActionMsg(data.last_action_msg);
      }

    } catch(e) {
      document.getElementById('statusDot').style.background = 'var(--red)';
    }
  }

  // ── /api/feed (every 3s) ──────────────────────────────────────────
  async function fetchFeed() {
    try {
      const r     = await fetch('/api/feed');
      const items = await r.json();
      const feedDiv = document.getElementById('feed');

      const newItems = items.filter(a => !knownFeedIds.has(a.time + a.token_id));
      if (!newItems.length) return;

      if (feedDiv.querySelector('.empty')) feedDiv.innerHTML = '';

      newItems.forEach(a => {
        knownFeedIds.add(a.time + a.token_id);
        totalAlerts++;
        const el = document.createElement('div');
        el.className = 'feed-item';
        el.innerHTML = `
          <span class="feed-label" title="${a.label}">${a.label}</span>
          <span class="feed-pct">+${a.pct_change.toFixed(2)}%</span>
          <span class="feed-meta">
            ${fmtP(a.old_price)} → ${fmtP(a.new_price)}<br>${a.time}
          </span>`;
        feedDiv.insertBefore(el, feedDiv.firstChild);
      });

      const badge = document.getElementById('alertBadge');
      badge.textContent = totalAlerts;
      badge.style.display = 'inline-block';
    } catch(e) {}
  }

  // ── action buttons ────────────────────────────────────────────────
  function showActionMsg(msg) {
    const el = document.getElementById('actionMsg');
    el.textContent = msg;
    el.style.display = 'inline-block';
    setTimeout(() => { el.style.display = 'none'; }, 6000);
  }

  async function triggerTelegram() {
    const btn = document.getElementById('btnTelegram');
    btn.disabled = true;
    btn.textContent = '📨 שולח...';
    try {
      const r    = await fetch('/api/trigger-test', {method:'POST'});
      const data = await r.json();
      showActionMsg(data.message);
    } catch(e) {
      showActionMsg('שגיאת רשת ❌');
    }
    btn.textContent = '📨 שלח בדיקת טלגרם';
    btn.disabled = false;
  }

  async function triggerRefresh() {
    const btn = document.getElementById('btnRefresh');
    btn.disabled = true;
    btn.textContent = '🔄 מרענן...';
    try {
      const r    = await fetch('/api/refresh-markets', {method:'POST'});
      const data = await r.json();
      showActionMsg(data.message);
    } catch(e) {
      showActionMsg('שגיאת רשת ❌');
    }
    btn.textContent = '🔄 רענן שווקים';
    btn.disabled = false;
  }

  // ── init ──────────────────────────────────────────────────────────
  fetchStatus();
  fetchFeed();
  setInterval(fetchStatus, 10000);
  setInterval(fetchFeed,   3000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return _HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/status")
def api_status():
    return jsonify(store.snapshot())


@app.route("/api/feed")
def api_feed():
    return jsonify(store.snapshot()["alert_feed"])


@app.route("/api/trigger-test", methods=["POST"])
def api_trigger_test():
    return jsonify(store.trigger_telegram_test())


@app.route("/api/refresh-markets", methods=["POST"])
def api_refresh_markets():
    return jsonify(store.trigger_market_refresh())


# ---------------------------------------------------------------------------
# Public launcher
# ---------------------------------------------------------------------------

def start_dashboard(port: int = 5000) -> None:
    """Start the Flask server in a daemon background thread."""
    def _run():
        app.run(
            host="0.0.0.0",
            port=port,
            threaded=True,
            use_reloader=False,
            debug=False,
        )

    t = threading.Thread(target=_run, daemon=True, name="dashboard")
    t.start()
    logger.info("Dashboard thread started on port %d", port)
