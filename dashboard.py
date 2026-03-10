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
  POST /api/force-cycle       → Force an immediate price fetch cycle
"""

import logging
import threading

from flask import Flask, jsonify

from dashboard_store import store

logger = logging.getLogger(__name__)

app = Flask(__name__)
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PolyBot Dashboard</title>
<style>
  :root {
    --bg:#0d1117; --card:#161b22; --border:#30363d;
    --text:#e6edf3; --muted:#8b949e;
    --green:#3fb950; --red:#f85149; --yellow:#d29922;
    --accent:#58a6ff; --orange:#f0883e;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}

  /* ── header ── */
  header{padding:12px 20px;border-bottom:1px solid var(--border);
         display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--green);flex-shrink:0;animation:pulse 2s infinite;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  #last-refresh{font-size:11px;color:var(--muted);}

  /* ── action bar ── */
  .action-bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
  .btn{
    display:inline-flex;align-items:center;gap:5px;
    padding:5px 12px;border-radius:6px;border:1px solid var(--border);
    font-size:12px;font-weight:600;cursor:pointer;
    background:var(--card);color:var(--text);
    transition:background .15s,border-color .15s;white-space:nowrap;
  }
  .btn:hover{background:#21262d;}
  .btn:disabled{opacity:.45;cursor:not-allowed;}
  .btn-green{border-color:var(--green);color:var(--green);}
  .btn-green:hover{background:rgba(63,185,80,.1);}
  .btn-blue{border-color:var(--accent);color:var(--accent);}
  .btn-blue:hover{background:rgba(88,166,255,.1);}
  .btn-orange{border-color:var(--orange);color:var(--orange);}
  .btn-orange:hover{background:rgba(240,136,62,.1);}
  .btn-yellow{border-color:var(--yellow);color:var(--yellow);}
  .btn-yellow:hover{background:rgba(210,153,34,.1);}
  #actionMsg{
    font-size:12px;padding:4px 10px;border-radius:5px;
    background:rgba(88,166,255,.1);border:1px solid var(--accent);
    color:var(--accent);display:none;
  }

  /* ── grid layout ── */
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px 20px;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;}
  .card-full{grid-column:1/-1;}
  .card h2{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
           letter-spacing:.5px;margin-bottom:10px;display:flex;align-items:center;gap:7px;}
  .badge{display:inline-block;padding:1px 6px;border-radius:999px;font-size:11px;
         font-weight:700;background:rgba(63,185,80,.15);color:var(--green);}
  .badge-orange{background:rgba(240,136,62,.15);color:var(--orange);}

  /* ── stat grid ── */
  .stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;}
  .stat{background:var(--bg);border-radius:6px;padding:9px 12px;}
  .stat-label{font-size:11px;color:var(--muted);margin-bottom:3px;}
  .stat-value{font-size:19px;font-weight:700;color:var(--accent);}

  /* ── tables ── */
  .tbl-wrap{overflow-x:auto;}
  table{width:100%;border-collapse:collapse;}
  th{
    position:sticky;top:0;background:var(--card);
    text-align:right;color:var(--muted);font-size:11px;font-weight:600;
    text-transform:uppercase;padding:6px 10px;border-bottom:1px solid var(--border);
    white-space:nowrap;
  }
  td{padding:7px 10px;border-bottom:1px solid #1c2128;font-size:13px;vertical-align:middle;}
  tr:last-child td{border-bottom:none;}
  tr:hover td{background:#1c2128;}
  .lbl{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .mono{font-family:Consolas,monospace;}
  .up{color:var(--green);font-weight:600;}
  .dn{color:var(--red);font-weight:600;}
  .mu{color:var(--muted);font-size:12px;}
  .wm{color:var(--yellow);font-size:11px;}

  /* ── pagination ── */
  .pager{display:flex;justify-content:center;align-items:center;gap:8px;
         margin-top:10px;font-size:12px;color:var(--muted);}
  .pager button{
    padding:3px 10px;border-radius:4px;border:1px solid var(--border);
    background:var(--card);color:var(--text);cursor:pointer;font-size:12px;
  }
  .pager button:hover{background:#21262d;}
  .pager button:disabled{opacity:.35;cursor:not-allowed;}
  #pageInfo{min-width:80px;text-align:center;}

  /* ── live feed ── */
  #feed{max-height:330px;overflow-y:auto;}
  .fi{
    display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:8px;
    padding:8px 10px;border-radius:6px;margin-bottom:5px;
    background:var(--bg);border-right:3px solid var(--green);
    animation:fi .35s ease;
  }
  @keyframes fi{from{opacity:0;transform:translateY(-5px)}to{opacity:1;transform:translateY(0)}}
  .fi-lbl{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .fi-pct{font-weight:700;color:var(--green);white-space:nowrap;font-size:14px;}
  .fi-meta{font-size:11px;color:var(--muted);text-align:left;white-space:nowrap;}
  .empty{color:var(--muted);padding:14px 0;text-align:center;}

  @media(max-width:680px){.grid{grid-template-columns:1fr;}.card-full{grid-column:1;}}
</style>
</head>
<body>

<header>
  <div class="dot" id="statusDot"></div>
  <h1>PolyBot Dashboard</h1>
  <span id="last-refresh"></span>
  <div class="action-bar">
    <button class="btn btn-orange" id="btnCycle"   onclick="forceCycle()">⚡ הפעל מחזור עכשיו</button>
    <button class="btn btn-green"  id="btnTelegram" onclick="triggerTelegram()">📨 בדיקת טלגרם</button>
    <button class="btn btn-blue"   id="btnRefresh"  onclick="triggerRefresh()">🔄 רענן שווקים</button>
    <span id="actionMsg"></span>
  </div>
</header>

<div class="grid">

  <!-- Bot Status -->
  <div class="card">
    <h2>סטטוס הבוט</h2>
    <div class="stat-grid">
      <div class="stat"><div class="stat-label">מחזורים</div><div class="stat-value" id="cycleCount">—</div></div>
      <div class="stat"><div class="stat-label">שווקים במעקב</div><div class="stat-value" id="mktCount">—</div></div>
      <div class="stat"><div class="stat-label">עדכון אחרון</div><div class="stat-value" style="font-size:15px" id="lastUpdate">—</div></div>
      <div class="stat"><div class="stat-label">פעיל מאז</div><div class="stat-value" style="font-size:15px" id="startTime">—</div></div>
    </div>
  </div>

  <!-- Live Alert Feed -->
  <div class="card">
    <h2>התראות חיות <span id="alertBadge" class="badge" style="display:none">0</span></h2>
    <div id="feed"><div class="empty">ממתין להתראות...</div></div>
  </div>

  <!-- Market Stats (grouped, paginated) -->
  <div class="card card-full">
    <h2>
      סטטיסטיקת שוק
      <span style="font-weight:400;text-transform:none;font-size:11px;color:var(--muted)">
        — מחירים = הסתברות (0%–100%) · מקובצים לפי אירוע
      </span>
      <span id="mktBadge" class="badge" style="margin-right:auto"></span>
    </h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>אירוע / שוק פוליטי</th>
          <th title="הסתברות הגבוהה ביותר מבין כל הטוקנים של האירוע הזה (0%=בלתי סביר, 100%=וודאי)">מחיר גבוה ביותר באירוע (%)</th>
          <th title="הקפיצה הגדולה ביותר שנצפתה באחד הטוקנים של האירוע בתוך חלון הזמן (5 דק׳)">שינוי מקסימלי בחלון 5 דק׳</th>
          <th title="כמה פעמים נשלחה התראת טלגרם על אירוע זה מאז הפעלת הבוט">התראות שנשלחו לטלגרם</th>
        </tr></thead>
        <tbody id="marketBody"><tr><td colspan="4" class="empty">ממתין...</td></tr></tbody>
      </table>
    </div>
    <div class="pager">
      <button id="prevBtn" onclick="changePage(-1)" disabled>◀ הקודם</button>
      <span id="pageInfo">—</span>
      <button id="nextBtn" onclick="changePage(1)"  disabled>הבא ▶</button>
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
          <th>מחיר לפני</th>
          <th>מחיר אחרי</th>
        </tr></thead>
        <tbody id="historyBody"><tr><td colspan="5" class="empty">אין היסטוריה עדיין.</td></tr></tbody>
      </table>
    </div>
  </div>

</div>

<script>
// ── utils ──────────────────────────────────────────────────────────────
const fmtPct  = v => v == null ? null : (v * 100).toFixed(1) + '%';
const fmtChg  = v => v == null
  ? '<span class="wm">מחמם...</span>'
  : `<span class="${v>=0?'up':'dn'}">${v>=0?'+':''}${v.toFixed(2)}%</span>`;

// ── pagination state ───────────────────────────────────────────────────
const PAGE_SIZE = 10;
let allGrouped  = [];
let currentPage = 1;

function groupByLabel(stats) {
  const map = {};
  stats.forEach(m => {
    if (!map[m.label]) map[m.label] = { label: m.label, prices: [], pct_changes: [], alert_count: 0 };
    map[m.label].prices.push(m.current_price);
    if (m.pct_change != null) map[m.label].pct_changes.push(m.pct_change);
    map[m.label].alert_count += m.alert_count;
  });
  return Object.values(map).map(g => ({
    label:       g.label,
    max_price:   g.prices.length ? Math.max(...g.prices) : null,
    max_pct:     g.pct_changes.length ? Math.max(...g.pct_changes) : null,
    alert_count: g.alert_count,
  })).sort((a,b) => (b.alert_count - a.alert_count) || (a.label.localeCompare(b.label)));
}

function renderMarketPage() {
  const tbody = document.getElementById('marketBody');
  const total  = allGrouped.length;
  const pages  = Math.max(1, Math.ceil(total / PAGE_SIZE));
  currentPage  = Math.min(currentPage, pages);
  const start  = (currentPage - 1) * PAGE_SIZE;
  const slice  = allGrouped.slice(start, start + PAGE_SIZE);

  document.getElementById('pageInfo').textContent =
    total ? `עמוד ${currentPage} מתוך ${pages}` : '—';
  document.getElementById('prevBtn').disabled = currentPage <= 1;
  document.getElementById('nextBtn').disabled = currentPage >= pages;
  document.getElementById('mktBadge').textContent = total + ' שווקים';

  if (!slice.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">ממתין לנתונים...</td></tr>';
    return;
  }
  tbody.innerHTML = slice.map(m => `
    <tr>
      <td class="lbl" title="${m.label}">${m.label}</td>
      <td class="mono">${m.max_price != null ? fmtPct(m.max_price) : '—'}</td>
      <td>${fmtChg(m.max_pct)}</td>
      <td>${m.alert_count > 0
          ? `<span class="badge badge-orange">${m.alert_count}</span>`
          : '<span class="mu">0</span>'}</td>
    </tr>`).join('');
}

function changePage(dir) {
  currentPage += dir;
  renderMarketPage();
}

// ── /api/status (every 10s) ────────────────────────────────────────────
async function fetchStatus() {
  try {
    const r    = await fetch('/api/status');
    const data = await r.json();
    const s    = data.bot_status;

    document.getElementById('cycleCount').textContent = s.cycle_count;
    document.getElementById('lastUpdate').textContent = s.last_update;
    document.getElementById('startTime').textContent  = s.start_time;
    document.getElementById('last-refresh').textContent =
      'רענון: ' + new Date().toLocaleTimeString('he-IL');

    // group & count unique events
    allGrouped = groupByLabel(data.market_stats);
    document.getElementById('mktCount').textContent = allGrouped.length;
    renderMarketPage();

    // alert history
    const hbody = document.getElementById('historyBody');
    if (!data.alert_feed.length) {
      hbody.innerHTML = '<tr><td colspan="5" class="empty">אין היסטוריה עדיין.</td></tr>';
    } else {
      hbody.innerHTML = data.alert_feed.map(a => `
        <tr>
          <td class="mu">${a.time}</td>
          <td class="lbl" title="${a.label}">${a.label}</td>
          <td class="up">+${a.pct_change.toFixed(2)}%</td>
          <td class="mono">${fmtPct(a.old_price)}</td>
          <td class="mono up">${fmtPct(a.new_price)}</td>
        </tr>`).join('');
    }

    if (data.last_action_msg) showMsg(data.last_action_msg);

  } catch(e) {
    document.getElementById('statusDot').style.background = 'var(--red)';
  }
}

// ── /api/feed (every 3s) ──────────────────────────────────────────────
let knownFeedIds = new Set();
let totalAlerts  = 0;

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
      el.className = 'fi';
      el.innerHTML = `
        <span class="fi-lbl" title="${a.label}">${a.label}</span>
        <span class="fi-pct">+${a.pct_change.toFixed(2)}%</span>
        <span class="fi-meta">${fmtPct(a.old_price)} → ${fmtPct(a.new_price)}<br>${a.time}</span>`;
      feedDiv.insertBefore(el, feedDiv.firstChild);
    });

    const b = document.getElementById('alertBadge');
    b.textContent = totalAlerts;
    b.style.display = 'inline-block';
  } catch(e) {}
}

// ── action helpers ────────────────────────────────────────────────────
function showMsg(msg) {
  const el = document.getElementById('actionMsg');
  el.textContent = msg;
  el.style.display = 'inline-block';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.style.display = 'none', 7000);
}

async function apiPost(url, btnId, loadingText, originalText) {
  const btn = document.getElementById(btnId);
  btn.disabled = true; btn.textContent = loadingText;
  try {
    const r = await fetch(url, {method:'POST'});
    const d = await r.json();
    showMsg(d.message);
  } catch(e) { showMsg('שגיאת רשת ❌'); }
  btn.textContent = originalText;
  btn.disabled = false;
}

const forceCycle     = () => apiPost('/api/force-cycle',       'btnCycle',    '⚡ מפעיל...', '⚡ הפעל מחזור עכשיו');
const triggerTelegram= () => apiPost('/api/trigger-test',      'btnTelegram', '📨 שולח...',  '📨 בדיקת טלגרם');
const triggerRefresh = () => apiPost('/api/refresh-markets',   'btnRefresh',  '🔄 מרענן...', '🔄 רענן שווקים');

// ── init ──────────────────────────────────────────────────────────────
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

@app.route("/api/force-cycle", methods=["POST"])
def api_force_cycle():
    return jsonify(store.trigger_force_cycle())


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

def start_dashboard(port: int = 5000) -> None:
    def _run():
        app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False, debug=False)
    threading.Thread(target=_run, daemon=True, name="dashboard").start()
    logger.info("Dashboard thread started on port %d", port)
