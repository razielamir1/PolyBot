"""
dashboard.py — PolyBot web dashboard with authentication and user management.

Routes:
  GET/POST /login              → Login page
  GET      /logout             → Logout
  GET      /                   → Dashboard (login required)
  GET      /admin              → User management (admin only)
  POST     /admin/users/add    → Create user (admin only)
  POST     /admin/users/<id>/delete  → Delete user (admin only)
  POST     /admin/users/<id>/role    → Toggle role (admin only)
  POST     /admin/users/<id>/analytics → Toggle analytics access (admin only)
  GET      /api/status         → JSON snapshot (login required)
  GET      /api/feed           → JSON alert feed (login required)
  POST     /api/trigger-test   → Test Telegram (admin only)
  POST     /api/refresh-markets → Re-scan markets (admin only)
  POST     /api/force-cycle    → Immediate cycle (admin only)
  POST     /api/set-threshold  → Change alert threshold (admin only)
  GET      /analytics          → Analytics dashboard (admin or analytics_enabled)
  GET      /api/hot-markets    → Hot markets list (admin or analytics_enabled)
  GET      /api/chart/<token_id> → Price history for one token (admin or analytics_enabled)
"""

import logging
import os
import threading
from functools import wraps

from flask import Flask, jsonify, redirect, render_template_string, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)

import db
from dashboard_store import store

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Flask-Login setup
# ---------------------------------------------------------------------------

login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, data: dict):
        self.id = data["id"]
        self.email = data["email"]
        self.role = data["role"]
        self.analytics_enabled = bool(data.get("analytics_enabled", 0))

    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_analytics(self) -> bool:
        return self.role == "admin" or self.analytics_enabled


@login_manager.user_loader
def load_user(user_id):
    data = db.get_user_by_id(int(user_id))
    return User(data) if data else None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "message": "Admin access required"}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def analytics_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_analytics():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "message": "Analytics access required"}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — כניסה</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--red:#f85149;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;
       min-height:100vh;display:flex;align-items:center;justify-content:center;}
  .box{background:var(--card);border:1px solid var(--border);border-radius:10px;
       padding:32px 36px;width:340px;}
  h1{font-size:18px;margin-bottom:6px;}
  .sub{font-size:12px;color:var(--muted);margin-bottom:24px;}
  label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;}
  input{width:100%;padding:8px 12px;background:var(--bg);border:1px solid var(--border);
        border-radius:6px;color:var(--text);font-size:14px;margin-bottom:14px;}
  input:focus{outline:none;border-color:var(--accent);}
  button{width:100%;padding:9px;background:var(--accent);color:#0d1117;
         border:none;border-radius:6px;font-weight:700;font-size:14px;cursor:pointer;}
  button:hover{opacity:.9;}
  .err{background:rgba(248,81,73,.1);border:1px solid var(--red);color:var(--red);
       border-radius:6px;padding:8px 12px;font-size:13px;margin-bottom:14px;}
</style>
</head>
<body>
<div class="box">
  <h1>PolyBot Dashboard</h1>
  <p class="sub">הזן את פרטי הכניסה שלך</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>אימייל</label>
    <input type="email" name="email" required autofocus>
    <label>סיסמה</label>
    <input type="password" name="password" required>
    <button type="submit">כניסה</button>
  </form>
</div>
</body>
</html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user_data = db.get_user_by_email(email)
        if user_data and db.verify_password(user_data, password):
            login_user(User(user_data), remember=True)
            return redirect(url_for("index"))
        error = "אימייל או סיסמה שגויים"
    return render_template_string(_LOGIN_HTML, error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

_ADMIN_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — ניהול משתמשים</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--red:#f85149;--green:#3fb950;
        --yellow:#d29922;--orange:#f0883e;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}
  header{padding:12px 20px;border-bottom:1px solid var(--border);
         display:flex;align-items:center;gap:10px;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  a.btn-back{color:var(--accent);text-decoration:none;font-size:13px;
             border:1px solid var(--border);padding:4px 12px;border-radius:6px;}
  a.btn-back:hover{background:#21262d;}
  .page{padding:20px;max-width:800px;margin:0 auto;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;
        padding:20px;margin-bottom:16px;}
  h2{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;
     letter-spacing:.5px;margin-bottom:14px;}
  table{width:100%;border-collapse:collapse;}
  th{text-align:right;color:var(--muted);font-size:11px;font-weight:600;
     text-transform:uppercase;padding:6px 10px;border-bottom:1px solid var(--border);}
  td{padding:8px 10px;border-bottom:1px solid #1c2128;}
  tr:last-child td{border-bottom:none;}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;}
  .badge-admin{background:rgba(240,136,62,.15);color:var(--orange);}
  .badge-viewer{background:rgba(88,166,255,.1);color:var(--accent);}
  .actions{display:flex;gap:6px;}
  .btn{padding:4px 10px;border-radius:5px;border:1px solid var(--border);
       font-size:12px;cursor:pointer;background:var(--card);color:var(--text);}
  .btn:hover{background:#21262d;}
  .btn-red{border-color:var(--red);color:var(--red);}
  .btn-red:hover{background:rgba(248,81,73,.1);}
  .btn-green{border-color:var(--green);color:var(--green);}
  .btn-green:hover{background:rgba(63,185,80,.1);}
  .form-row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;}
  .form-group{display:flex;flex-direction:column;gap:4px;flex:1;min-width:150px;}
  label{font-size:12px;color:var(--muted);}
  input,select{padding:7px 10px;background:var(--bg);border:1px solid var(--border);
               border-radius:6px;color:var(--text);font-size:13px;}
  input:focus,select:focus{outline:none;border-color:var(--accent);}
  .btn-submit{padding:7px 18px;background:var(--accent);color:#0d1117;border:none;
              border-radius:6px;font-weight:700;cursor:pointer;white-space:nowrap;}
  .msg{padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:13px;}
  .msg-ok{background:rgba(63,185,80,.1);border:1px solid var(--green);color:var(--green);}
  .msg-err{background:rgba(248,81,73,.1);border:1px solid var(--red);color:var(--red);}
  .you{font-size:11px;color:var(--muted);margin-right:4px;}
</style>
</head>
<body>
<header>
  <h1>🔧 ניהול משתמשים</h1>
  <a class="btn-back" href="/">← חזרה לדאשבורד</a>
</header>
<div class="page">
  {% if msg %}<div class="msg {{ msg_class }}">{{ msg }}</div>{% endif %}

  <!-- Add user -->
  <div class="card">
    <h2>הוספת משתמש חדש</h2>
    <form method="POST" action="/admin/users/add">
      <div class="form-row">
        <div class="form-group">
          <label>אימייל</label>
          <input type="email" name="email" required placeholder="user@example.com">
        </div>
        <div class="form-group">
          <label>סיסמה</label>
          <input type="password" name="password" required placeholder="לפחות 6 תווים">
        </div>
        <div class="form-group" style="max-width:130px">
          <label>תפקיד</label>
          <select name="role">
            <option value="viewer">Viewer</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <button type="submit" class="btn-submit">הוסף</button>
      </div>
    </form>
  </div>

  <!-- Users list -->
  <div class="card">
    <h2>משתמשים קיימים ({{ users|length }})</h2>
    <table>
      <thead><tr>
        <th>אימייל</th><th>תפקיד</th><th>אנליטיקס</th><th>נוצר</th><th>פעולות</th>
      </tr></thead>
      <tbody>
      {% for u in users %}
      <tr>
        <td>
          {{ u.email }}
          {% if u.id == current_id %}<span class="you">(אתה)</span>{% endif %}
        </td>
        <td><span class="badge badge-{{ u.role }}">{{ u.role }}</span></td>
        <td>
          {% if u.role == 'admin' %}
            <span style="color:var(--muted);font-size:12px">תמיד</span>
          {% else %}
            <form method="POST" action="/admin/users/{{ u.id }}/analytics" style="display:inline">
              <button type="submit" class="btn {% if u.analytics_enabled %}btn-green{% endif %}" style="min-width:60px">
                {% if u.analytics_enabled %}✓ מופעל{% else %}כבוי{% endif %}
              </button>
            </form>
          {% endif %}
        </td>
        <td style="color:var(--muted);font-size:12px">{{ u.created_at[:10] }}</td>
        <td>
          <div class="actions">
            {% if u.id != current_id %}
              <form method="POST" action="/admin/users/{{ u.id }}/role" style="display:inline">
                <button type="submit" class="btn btn-green">
                  {% if u.role == 'admin' %}הורד ל-Viewer{% else %}הפוך ל-Admin{% endif %}
                </button>
              </form>
              <form method="POST" action="/admin/users/{{ u.id }}/delete" style="display:inline"
                    onsubmit="return confirm('למחוק את {{ u.email }}?')">
                <button type="submit" class="btn btn-red">מחק</button>
              </form>
            {% else %}
              <span style="color:var(--muted);font-size:12px">—</span>
            {% endif %}
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <!-- Muted events -->
  <div class="card">
    <h2>אירועים מושתקים</h2>
    <div id="mutedList" style="margin-bottom:12px">
      <span style="color:var(--muted);font-size:13px">טוען...</span>
    </div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <input type="text" id="muteInput" placeholder="שם אירוע להשתקה" style="flex:1;min-width:200px">
      <button class="btn-submit" onclick="adminMute()">🔕 השתק</button>
    </div>
  </div>
</div>

<script>
async function loadMuted() {
  const r = await fetch('/api/muted');
  const labels = await r.json();
  const el = document.getElementById('mutedList');
  if (!labels.length) {
    el.innerHTML = '<span style="color:var(--muted);font-size:13px">אין אירועים מושתקים</span>';
    return;
  }
  el.innerHTML = labels.map(l => `
    <div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #1c2128">
      <span style="flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${l}">${l}</span>
      <button class="btn btn-red" onclick="adminUnmute(this,'${l.replace(/'/g,"\\'")}')">הסר</button>
    </div>`).join('');
}
async function adminMute() {
  const inp = document.getElementById('muteInput');
  const label = inp.value.trim();
  if (!label) return;
  await fetch('/api/mute', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_label:label})});
  inp.value = '';
  loadMuted();
}
async function adminUnmute(btn, label) {
  btn.disabled = true;
  await fetch('/api/unmute', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_label:label})});
  loadMuted();
}
loadMuted();
</script>
</body>
</html>"""


@app.route("/admin")
@login_required
@admin_required
def admin():
    users = db.get_all_users()
    msg = request.args.get("msg", "")
    msg_class = request.args.get("cls", "msg-ok")
    return render_template_string(
        _ADMIN_HTML,
        users=users,
        current_id=current_user.id,
        msg=msg,
        msg_class=msg_class,
    )


@app.route("/admin/users/add", methods=["POST"])
@login_required
@admin_required
def admin_add_user():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "viewer")
    if len(password) < 6:
        return redirect(url_for("admin", msg="הסיסמה חייבת להכיל לפחות 6 תווים", cls="msg-err"))
    ok = db.create_user(email, password, role)
    if ok:
        return redirect(url_for("admin", msg=f"משתמש {email} נוצר בהצלחה"))
    return redirect(url_for("admin", msg=f"האימייל {email} כבר קיים", cls="msg-err"))


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(uid):
    if uid == current_user.id:
        return redirect(url_for("admin", msg="אי אפשר למחוק את עצמך", cls="msg-err"))
    db.delete_user(uid)
    return redirect(url_for("admin", msg="משתמש נמחק"))


@app.route("/admin/users/<int:uid>/role", methods=["POST"])
@login_required
@admin_required
def admin_toggle_role(uid):
    if uid == current_user.id:
        return redirect(url_for("admin", msg="אי אפשר לשנות את התפקיד שלך", cls="msg-err"))
    user = db.get_user_by_id(uid)
    if not user:
        return redirect(url_for("admin", msg="משתמש לא נמצא", cls="msg-err"))
    new_role = "viewer" if user["role"] == "admin" else "admin"
    db.update_user_role(uid, new_role)
    return redirect(url_for("admin", msg=f"תפקיד עודכן ל-{new_role}"))


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

  header{padding:10px 20px;border-bottom:1px solid var(--border);
         display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--green);flex-shrink:0;animation:pulse 2s infinite;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  #last-refresh{font-size:11px;color:var(--muted);}

  .user-bar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);}
  .role-badge{padding:1px 7px;border-radius:999px;font-size:11px;font-weight:700;}
  .role-admin{background:rgba(240,136,62,.15);color:var(--orange);}
  .role-viewer{background:rgba(88,166,255,.1);color:var(--accent);}
  .user-bar a{color:var(--muted);text-decoration:none;border:1px solid var(--border);
              padding:3px 9px;border-radius:5px;font-size:11px;}
  .user-bar a:hover{background:#21262d;color:var(--text);}

  .action-bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
  .btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;
       border:1px solid var(--border);font-size:12px;font-weight:600;cursor:pointer;
       background:var(--card);color:var(--text);transition:background .15s;white-space:nowrap;}
  .btn:hover{background:#21262d;}
  .btn:disabled{opacity:.45;cursor:not-allowed;}
  .btn-green{border-color:var(--green);color:var(--green);}
  .btn-green:hover{background:rgba(63,185,80,.1);}
  .btn-blue{border-color:var(--accent);color:var(--accent);}
  .btn-blue:hover{background:rgba(88,166,255,.1);}
  .btn-orange{border-color:var(--orange);color:var(--orange);}
  .btn-orange:hover{background:rgba(240,136,62,.1);}
  #actionMsg{font-size:12px;padding:4px 10px;border-radius:5px;
             background:rgba(88,166,255,.1);border:1px solid var(--accent);
             color:var(--accent);display:none;}

  .threshold-bar{display:flex;align-items:center;gap:7px;font-size:12px;
                 background:rgba(88,166,255,.06);border:1px solid var(--border);
                 border-radius:6px;padding:5px 10px;}
  .threshold-bar label{color:var(--muted);}
  .threshold-bar input{width:60px;padding:3px 7px;background:var(--bg);
                       border:1px solid var(--border);border-radius:4px;
                       color:var(--text);font-size:12px;text-align:center;}
  .threshold-bar input:focus{outline:none;border-color:var(--accent);}
  .threshold-bar button{padding:3px 10px;border-radius:4px;border:1px solid var(--accent);
                        background:transparent;color:var(--accent);font-size:11px;
                        font-weight:600;cursor:pointer;}
  .threshold-bar button:hover{background:rgba(88,166,255,.1);}

  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px 20px;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;}
  .card-full{grid-column:1/-1;}
  .card h2{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
           letter-spacing:.5px;margin-bottom:10px;display:flex;align-items:center;gap:7px;}
  .badge{display:inline-block;padding:1px 6px;border-radius:999px;font-size:11px;
         font-weight:700;background:rgba(63,185,80,.15);color:var(--green);}
  .badge-orange{background:rgba(240,136,62,.15);color:var(--orange);}

  .stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;}
  .stat{background:var(--bg);border-radius:6px;padding:9px 12px;}
  .stat-label{font-size:11px;color:var(--muted);margin-bottom:3px;}
  .stat-value{font-size:19px;font-weight:700;color:var(--accent);}

  .tbl-wrap{overflow-x:auto;}
  table{width:100%;border-collapse:collapse;}
  th{position:sticky;top:0;background:var(--card);text-align:right;color:var(--muted);
     font-size:11px;font-weight:600;text-transform:uppercase;padding:6px 10px;
     border-bottom:1px solid var(--border);white-space:nowrap;}
  td{padding:7px 10px;border-bottom:1px solid #1c2128;font-size:13px;vertical-align:middle;}
  tr:last-child td{border-bottom:none;}
  tr:hover td{background:#1c2128;}
  .lbl{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .mono{font-family:Consolas,monospace;}
  .up{color:var(--green);font-weight:600;}
  .dn{color:var(--red);font-weight:600;}
  .mu{color:var(--muted);font-size:12px;}
  .wm{color:var(--yellow);font-size:11px;}

  .pager{display:flex;justify-content:center;align-items:center;gap:8px;
         margin-top:10px;font-size:12px;color:var(--muted);}
  .pager button{padding:3px 10px;border-radius:4px;border:1px solid var(--border);
                background:var(--card);color:var(--text);cursor:pointer;font-size:12px;}
  .pager button:hover{background:#21262d;}
  .pager button:disabled{opacity:.35;cursor:not-allowed;}
  #pageInfo{min-width:80px;text-align:center;}

  #feed{max-height:330px;overflow-y:auto;}
  .fi{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:8px;
      padding:8px 10px;border-radius:6px;margin-bottom:5px;
      background:var(--bg);border-right:3px solid var(--green);animation:fi .35s ease;}
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

  <div class="action-bar" id="adminBar" style="display:none">
    <button class="btn btn-orange" id="btnCycle"    onclick="forceCycle()">⚡ הפעל מחזור עכשיו</button>
    <button class="btn btn-green"  id="btnTelegram" onclick="triggerTelegram()">📨 בדיקת טלגרם</button>
    <button class="btn btn-blue"   id="btnRefresh"  onclick="triggerRefresh()">🔄 רענן שווקים</button>
    <div class="threshold-bar">
      <label>סף:</label>
      <input type="number" id="thresholdInput" min="0.1" max="100" step="0.1" value="10">
      <span style="color:var(--muted)">%</span>
      <button onclick="setThreshold()">עדכן</button>
    </div>
    <span id="actionMsg"></span>
  </div>

  <div class="user-bar">
    <span id="userEmail" style="color:var(--text)"></span>
    <span id="roleBadge" class="role-badge"></span>
    <a href="/analytics" id="analyticsLink" style="display:none">📊 אנליטיקס</a>
    <a href="/admin" id="adminLink" style="display:none">🔧 ניהול</a>
    <a href="/logout">יציאה</a>
  </div>
</header>

<div class="grid">

  <div class="card">
    <h2>סטטוס הבוט</h2>
    <div class="stat-grid">
      <div class="stat"><div class="stat-label">מחזורים</div><div class="stat-value" id="cycleCount">—</div></div>
      <div class="stat"><div class="stat-label">שווקים במעקב</div><div class="stat-value" id="mktCount">—</div></div>
      <div class="stat"><div class="stat-label">עדכון אחרון</div><div class="stat-value" style="font-size:15px" id="lastUpdate">—</div></div>
      <div class="stat"><div class="stat-label">פעיל מאז</div><div class="stat-value" style="font-size:15px" id="startTime">—</div></div>
    </div>
  </div>

  <div class="card">
    <h2>התראות חיות <span id="alertBadge" class="badge" style="display:none">0</span></h2>
    <div id="feed"><div class="empty">ממתין להתראות...</div></div>
  </div>

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
          <th>נושא</th><th>תשובה</th>
          <th title="הסתברות הגבוהה ביותר מבין כל הטוקנים של האירוע הזה">מחיר גבוה ביותר באירוע (%)</th>
          <th title="הקפיצה הגדולה ביותר שנצפתה בחלון 5 דק׳">שינוי מקסימלי בחלון 5 דק׳</th>
          <th title="כמה התראות נשלחו מאז הפעלת הבוט">התראות שנשלחו לטלגרם</th>
        </tr></thead>
        <tbody id="marketBody"><tr><td colspan="5" class="empty">ממתין...</td></tr></tbody>
      </table>
    </div>
    <div class="pager">
      <button id="prevBtn" onclick="changePage(-1)" disabled>◀ הקודם</button>
      <span id="pageInfo">—</span>
      <button id="nextBtn" onclick="changePage(1)" disabled>הבא ▶</button>
    </div>
  </div>

  <div class="card card-full">
    <h2>היסטוריית התראות</h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>שעה (UTC)</th><th>נושא</th><th>תשובה</th><th>קפיצה</th><th>מחיר לפני</th><th>מחיר אחרי</th><th></th>
        </tr></thead>
        <tbody id="historyBody"><tr><td colspan="7" class="empty">אין היסטוריה עדיין.</td></tr></tbody>
      </table>
    </div>
  </div>

</div>

<script>
const fmtPct = v => v == null ? null : (v * 100).toFixed(1) + '%';
const fmtChg = v => v == null
  ? '<span class="wm">מחמם...</span>'
  : `<span class="${v>=0?'up':'dn'}">${v>=0?'+':''}${v.toFixed(2)}%</span>`;

let isAdmin = false;

function applyAuthUI(admin, email, role, threshold, canAnalytics) {
  isAdmin = admin;
  document.getElementById('userEmail').textContent = email;
  const rb = document.getElementById('roleBadge');
  rb.textContent = role === 'admin' ? 'Admin' : 'Viewer';
  rb.className = 'role-badge role-' + role;
  if (admin) {
    document.getElementById('adminBar').style.display = 'flex';
    document.getElementById('adminLink').style.display = 'inline';
    if (threshold != null) document.getElementById('thresholdInput').value = threshold;
  }
  if (canAnalytics) {
    document.getElementById('analyticsLink').style.display = 'inline';
  }
}

const PAGE_SIZE = 10;
let allGrouped = [], currentPage = 1;

function groupByLabel(stats) {
  const map = {};
  stats.forEach(m => {
    const ev = m.event_label || m.label;
    const out = (m.label && m.label !== ev) ? m.label : '—';
    const key = ev + '||' + out;
    if (!map[key]) map[key] = { event_label: ev, label: out, prices: [], pct_changes: [], alert_count: 0 };
    map[key].prices.push(m.current_price);
    if (m.pct_change != null) map[key].pct_changes.push(m.pct_change);
    map[key].alert_count += m.alert_count;
  });
  return Object.values(map).map(g => ({
    event_label: g.event_label,
    label: g.label,
    max_price: g.prices.length ? Math.max(...g.prices) : null,
    max_pct: g.pct_changes.length ? Math.max(...g.pct_changes) : null,
    alert_count: g.alert_count,
  })).sort((a,b) => (b.alert_count - a.alert_count) || a.event_label.localeCompare(b.event_label));
}

function renderMarketPage() {
  const total = allGrouped.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  currentPage = Math.min(currentPage, pages);
  const slice = allGrouped.slice((currentPage-1)*PAGE_SIZE, currentPage*PAGE_SIZE);
  document.getElementById('pageInfo').textContent = total ? `עמוד ${currentPage} מתוך ${pages}` : '—';
  document.getElementById('prevBtn').disabled = currentPage <= 1;
  document.getElementById('nextBtn').disabled = currentPage >= pages;
  document.getElementById('mktBadge').textContent = total + ' שווקים';
  const tbody = document.getElementById('marketBody');
  if (!slice.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">ממתין לנתונים...</td></tr>'; return; }
  tbody.innerHTML = slice.map(m => `
    <tr>
      <td class="lbl" title="${m.event_label}">${m.event_label}</td>
      <td class="lbl">${m.label}</td>
      <td class="mono">${m.max_price != null ? fmtPct(m.max_price) : '—'}</td>
      <td>${fmtChg(m.max_pct)}</td>
      <td>${m.alert_count > 0 ? `<span class="badge badge-orange">${m.alert_count}</span>` : '<span class="mu">0</span>'}</td>
    </tr>`).join('');
}

function changePage(dir) { currentPage += dir; renderMarketPage(); }

let firstLoad = true;

async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    if (r.status === 401) { window.location.href = '/login'; return; }
    const data = await r.json();
    const s = data.bot_status;
    document.getElementById('cycleCount').textContent = s.cycle_count;
    document.getElementById('lastUpdate').textContent = s.last_update;
    document.getElementById('startTime').textContent  = s.start_time;
    document.getElementById('last-refresh').textContent = 'רענון: ' + new Date().toLocaleTimeString('he-IL');

    if (firstLoad) {
      applyAuthUI(data.is_admin, data.user_email, data.user_role, data.threshold, data.can_analytics);
      firstLoad = false;
    }

    allGrouped = groupByLabel(data.market_stats);
    document.getElementById('mktCount').textContent = allGrouped.length;
    renderMarketPage();

    const hbody = document.getElementById('historyBody');
    if (!data.alert_feed.length) {
      hbody.innerHTML = '<tr><td colspan="7" class="empty">אין היסטוריה עדיין.</td></tr>';
    } else {
      hbody.innerHTML = data.alert_feed.map(a => {
        const ev = a.event_label || a.label;
        const out = (a.label && a.label !== ev) ? a.label : '—';
        const link = a.url ? `<a href="${a.url}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:14px" title="פתח בפולימארקט">🔗</a>` : '';
        const muteBtn = isAdmin ? `<button onclick="muteEvent(this,'${ev.replace(/'/g,"\\'")}',event)" style="background:none;border:none;cursor:pointer;font-size:13px;opacity:.5;padding:0 2px" title="השתק אירוע זה">🔕</button>` : '';
        return `<tr>
          <td class="mu">${a.time}</td>
          <td class="lbl" title="${ev}">${ev}</td>
          <td class="lbl">${out}</td>
          <td class="up">+${a.pct_change.toFixed(2)}%</td>
          <td class="mono">${fmtPct(a.old_price)}</td>
          <td class="mono up">${fmtPct(a.new_price)}</td>
          <td style="white-space:nowrap">${link}${muteBtn}</td>
        </tr>`;
      }).join('');
    }
    if (data.last_action_msg) showMsg(data.last_action_msg);
  } catch(e) {
    document.getElementById('statusDot').style.background = 'var(--red)';
  }
}

let knownFeedIds = new Set(), totalAlerts = 0;

async function fetchFeed() {
  try {
    const r = await fetch('/api/feed');
    if (r.status === 401) return;
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
        <span class="fi-lbl" title="${a.event_label || a.label}">${a.event_label || a.label}${(a.label && a.label !== (a.event_label || a.label)) ? ' — ' + a.label : ''}</span>
        <span class="fi-pct">+${a.pct_change.toFixed(2)}%</span>
        <span class="fi-meta">${fmtPct(a.old_price)} → ${fmtPct(a.new_price)}<br>${a.time}</span>`;
      feedDiv.insertBefore(el, feedDiv.firstChild);
    });
    const b = document.getElementById('alertBadge');
    b.textContent = totalAlerts; b.style.display = 'inline-block';
  } catch(e) {}
}

function showMsg(msg) {
  const el = document.getElementById('actionMsg');
  el.textContent = msg; el.style.display = 'inline-block';
  clearTimeout(el._t); el._t = setTimeout(() => el.style.display = 'none', 7000);
}

async function apiPost(url, btnId, loadingText, origText) {
  if (!isAdmin) return;
  const btn = document.getElementById(btnId);
  btn.disabled = true; btn.textContent = loadingText;
  try {
    const r = await fetch(url, {method:'POST'});
    const d = await r.json();
    showMsg(d.message);
  } catch(e) { showMsg('שגיאת רשת'); }
  btn.textContent = origText; btn.disabled = false;
}

const forceCycle      = () => apiPost('/api/force-cycle',     'btnCycle',    '⚡ מפעיל...', '⚡ הפעל מחזור עכשיו');
const triggerTelegram = () => apiPost('/api/trigger-test',    'btnTelegram', '📨 שולח...',  '📨 בדיקת טלגרם');
const triggerRefresh  = () => apiPost('/api/refresh-markets', 'btnRefresh',  '🔄 מרענן...', '🔄 רענן שווקים');

async function setThreshold() {
  if (!isAdmin) return;
  const val = parseFloat(document.getElementById('thresholdInput').value);
  if (isNaN(val) || val <= 0) { showMsg('ערך סף לא תקין'); return; }
  try {
    const r = await fetch('/api/set-threshold', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({threshold: val})
    });
    const d = await r.json();
    showMsg(d.message);
  } catch(e) { showMsg('שגיאת רשת'); }
}

async function muteEvent(btn, eventLabel, e) {
  e.stopPropagation();
  if (!isAdmin) return;
  if (!confirm(`להשתיק התראות עבור:\n"${eventLabel}"?`)) return;
  try {
    const r = await fetch('/api/mute', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({event_label: eventLabel})
    });
    const d = await r.json();
    showMsg(d.message);
    btn.textContent = '🔇'; btn.title = 'מושתק'; btn.disabled = true;
  } catch(e) { showMsg('שגיאת רשת'); }
}

fetchStatus();
fetchFeed();
setInterval(fetchStatus, 10000);
setInterval(fetchFeed, 3000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return _HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/status")
@login_required
def api_status():
    data = store.snapshot()
    data["is_admin"] = current_user.is_admin()
    data["user_email"] = current_user.email
    data["user_role"] = current_user.role
    data["can_analytics"] = current_user.can_analytics()
    return jsonify(data)


@app.route("/api/feed")
@login_required
def api_feed():
    return jsonify(store.snapshot()["alert_feed"])


@app.route("/api/trigger-test", methods=["POST"])
@login_required
@admin_required
def api_trigger_test():
    return jsonify(store.trigger_telegram_test())


@app.route("/api/refresh-markets", methods=["POST"])
@login_required
@admin_required
def api_refresh_markets():
    return jsonify(store.trigger_market_refresh())


@app.route("/api/force-cycle", methods=["POST"])
@login_required
@admin_required
def api_force_cycle():
    return jsonify(store.trigger_force_cycle())


@app.route("/api/set-threshold", methods=["POST"])
@login_required
@admin_required
def api_set_threshold():
    data = request.get_json(force=True, silent=True) or {}
    try:
        val = float(data.get("threshold", 0))
        if val <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "message": "ערך לא תקין"}), 400
    store.set_threshold(val)
    msg = f"סף ההתראה עודכן ל-{val}%"
    store.set_action_msg(msg)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/muted")
@login_required
@admin_required
def api_get_muted():
    return jsonify(store.get_muted())


@app.route("/api/mute", methods=["POST"])
@login_required
@admin_required
def api_mute():
    data = request.get_json(force=True, silent=True) or {}
    label = (data.get("event_label") or "").strip()
    if not label:
        return jsonify({"ok": False, "message": "חסר event_label"}), 400
    store.mute(label)
    db.add_muted_label(label)
    return jsonify({"ok": True, "message": f"מושתק: {label}"})


@app.route("/api/unmute", methods=["POST"])
@login_required
@admin_required
def api_unmute():
    data = request.get_json(force=True, silent=True) or {}
    label = (data.get("event_label") or "").strip()
    if not label:
        return jsonify({"ok": False, "message": "חסר event_label"}), 400
    store.unmute(label)
    db.remove_muted_label(label)
    return jsonify({"ok": True, "message": f"הוסר מיוט: {label}"})


@app.route("/admin/users/<int:uid>/analytics", methods=["POST"])
@login_required
@admin_required
def admin_toggle_analytics(uid):
    user = db.get_user_by_id(uid)
    if not user:
        return redirect(url_for("admin", msg="משתמש לא נמצא", cls="msg-err"))
    new_val = not bool(user.get("analytics_enabled", 0))
    db.update_user_analytics(uid, new_val)
    state = "מופעל" if new_val else "כבוי"
    return redirect(url_for("admin", msg=f"גישת אנליטיקס {state} למשתמש {user['email']}"))


# ---------------------------------------------------------------------------
# Analytics HTML
# ---------------------------------------------------------------------------

_ANALYTICS_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — Analytics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;
        --orange:#f0883e;--yellow:#d29922;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}
  header{padding:12px 20px;border-bottom:1px solid var(--border);
         display:flex;align-items:center;gap:10px;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  a.btn-back{color:var(--accent);text-decoration:none;font-size:13px;
             border:1px solid var(--border);padding:4px 12px;border-radius:6px;}
  a.btn-back:hover{background:#21262d;}
  .page{display:flex;gap:16px;padding:16px 20px;height:calc(100vh - 53px);}
  .sidebar{width:320px;flex-shrink:0;display:flex;flex-direction:column;gap:10px;overflow-y:auto;}
  .main{flex:1;display:flex;flex-direction:column;gap:12px;min-width:0;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;}
  .card h2{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
           letter-spacing:.5px;margin-bottom:10px;}
  .market-item{padding:10px 12px;border-radius:6px;border:1px solid var(--border);
               cursor:pointer;background:var(--bg);margin-bottom:6px;transition:border-color .15s;}
  .market-item:hover{border-color:var(--accent);}
  .market-item.active{border-color:var(--accent);background:rgba(88,166,255,.06);}
  .market-ev{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .market-out{font-size:11px;color:var(--muted);margin-top:2px;}
  .market-meta{display:flex;gap:8px;margin-top:5px;align-items:center;}
  .badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:700;}
  .badge-orange{background:rgba(240,136,62,.15);color:var(--orange);}
  .price{font-size:12px;color:var(--accent);font-family:Consolas,monospace;}
  .empty{color:var(--muted);padding:14px 0;text-align:center;font-size:13px;}
  .chart-card{flex:1;display:flex;flex-direction:column;}
  .chart-header{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;}
  .chart-title{font-size:15px;font-weight:600;flex:1;}
  .chart-sub{font-size:12px;color:var(--muted);}
  .chart-link{color:var(--accent);font-size:13px;text-decoration:none;}
  .chart-link:hover{text-decoration:underline;}
  .chart-wrap{flex:1;position:relative;min-height:300px;}
  .placeholder{display:flex;align-items:center;justify-content:center;
               height:100%;color:var(--muted);font-size:14px;border:1px dashed var(--border);
               border-radius:8px;}
  #refreshNote{font-size:11px;color:var(--muted);}
</style>
</head>
<body>
<header>
  <h1>📊 Analytics — שווקים חמים</h1>
  <span id="refreshNote"></span>
  <a class="btn-back" href="/">← חזרה לדאשבורד</a>
</header>
<div class="page">
  <div class="sidebar">
    <div class="card" style="flex:1">
      <h2>שווקים שהתריעו <span id="countBadge"></span></h2>
      <div id="marketList"><div class="empty">ממתין לנתונים...</div></div>
    </div>
  </div>
  <div class="main">
    <div class="card chart-card" id="chartCard">
      <div class="chart-header">
        <div>
          <div class="chart-title" id="chartTitle">בחר שוק מהרשימה</div>
          <div class="chart-sub" id="chartSub"></div>
        </div>
        <a class="chart-link" id="chartLink" href="#" target="_blank" rel="noopener" style="display:none">🔗 פתח בפולימארקט</a>
      </div>
      <div class="chart-wrap">
        <div class="placeholder" id="chartPlaceholder">← בחר שוק מהרשימה כדי לראות גרף מחיר</div>
        <canvas id="priceChart" style="display:none"></canvas>
      </div>
    </div>
  </div>
</div>

<script>
const fmtPct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
let hotMarkets = [];
let selectedToken = null;
let chartInstance = null;

async function loadHotMarkets() {
  try {
    const r = await fetch('/api/hot-markets');
    if (r.status === 401 || r.status === 403) { window.location.href = '/'; return; }
    hotMarkets = await r.json();
    renderMarketList();
    document.getElementById('countBadge').textContent = hotMarkets.length || '';
    document.getElementById('refreshNote').textContent = 'עדכון אחרון: ' + new Date().toLocaleTimeString('he-IL');
    if (selectedToken) loadChart(selectedToken);
  } catch(e) {}
}

function renderMarketList() {
  const el = document.getElementById('marketList');
  if (!hotMarkets.length) {
    el.innerHTML = '<div class="empty">עדיין אין שווקים שהתריעו</div>';
    return;
  }
  el.innerHTML = hotMarkets.map(m => {
    const ev = m.event_label || m.label;
    const out = (m.label && m.label !== ev) ? m.label : null;
    const active = m.token_id === selectedToken ? ' active' : '';
    return `<div class="market-item${active}" onclick="selectMarket('${m.token_id}')">
      <div class="market-ev" title="${ev}">${ev}</div>
      ${out ? `<div class="market-out">${out}</div>` : ''}
      <div class="market-meta">
        <span class="badge badge-orange">${m.alert_count} התראות</span>
        <span class="price">${fmtPct(m.current_price)}</span>
        ${m.pct_change != null ? `<span style="color:var(--green);font-size:11px">+${m.pct_change.toFixed(2)}%</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

function selectMarket(tokenId) {
  selectedToken = tokenId;
  renderMarketList();
  loadChart(tokenId);
}

async function loadChart(tokenId) {
  try {
    const r = await fetch('/api/chart/' + tokenId);
    if (!r.ok) return;
    const data = await r.json();
    renderChart(data);
  } catch(e) {}
}

function renderChart(data) {
  const ev = data.event_label || data.label;
  const out = (data.label && data.label !== ev) ? data.label : null;
  document.getElementById('chartTitle').textContent = ev;
  document.getElementById('chartSub').textContent = out || '';
  const linkEl = document.getElementById('chartLink');
  if (data.url) { linkEl.href = data.url; linkEl.style.display = 'inline'; }
  else { linkEl.style.display = 'none'; }

  document.getElementById('chartPlaceholder').style.display = 'none';
  const canvas = document.getElementById('priceChart');
  canvas.style.display = 'block';

  const history = data.history || [];
  const alertSet = new Set(data.alert_times || []);
  const labels = history.map(h => h.t);
  const prices = history.map(h => +(h.p * 100).toFixed(2));

  // Point radii: bigger red dots at alert times
  const pointRadii = labels.map(t => alertSet.has(t) ? 6 : 2);
  const pointColors = labels.map(t => alertSet.has(t) ? '#f85149' : '#58a6ff');

  if (chartInstance) chartInstance.destroy();
  chartInstance = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'מחיר (%)',
        data: prices,
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: pointRadii,
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ctx.parsed.y.toFixed(1) + '%' + (alertSet.has(labels[ctx.dataIndex]) ? ' 🔔' : '')
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#8b949e', maxTicksLimit: 8, maxRotation: 0 },
          grid: { color: '#21262d' }
        },
        y: {
          ticks: { color: '#8b949e', callback: v => v + '%' },
          grid: { color: '#21262d' },
          min: 0, max: 100
        }
      }
    }
  });
}

loadHotMarkets();
setInterval(loadHotMarkets, 30000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Analytics routes
# ---------------------------------------------------------------------------

@app.route("/analytics")
@login_required
@analytics_required
def analytics():
    return _ANALYTICS_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/hot-markets")
@login_required
@analytics_required
def api_hot_markets():
    return jsonify(store.get_hot_markets())


@app.route("/api/chart/<token_id>")
@login_required
@analytics_required
def api_chart(token_id):
    data = store.get_chart_data(token_id)
    if data is None:
        return jsonify({"ok": False, "message": "Token not found"}), 404
    return jsonify(data)


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

def start_dashboard(port: int = 5588) -> None:
    db.init_db()
    store.init_muted(db.get_muted_labels())
    if db.count_users() == 0:
        admin_email = os.getenv("ADMIN_EMAIL", "").strip()
        admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
        if admin_email and admin_password:
            db.create_user(admin_email, admin_password, role="admin")
            logger.info("Created first admin user: %s", admin_email)
        else:
            logger.warning(
                "No users in DB and ADMIN_EMAIL/ADMIN_PASSWORD not set in .env. "
                "Dashboard login will be unavailable until a user is created."
            )

    def _run():
        app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False, debug=False)

    threading.Thread(target=_run, daemon=True, name="dashboard").start()
    logger.info("Dashboard thread started on port %d", port)
