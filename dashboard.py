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
  GET      /ai-chat            → AI chat page (admin or ai_enabled)
  POST     /api/ai-chat        → AI chat API (admin or ai_enabled)
  POST     /api/admin/ai-command → Natural language admin commands (admin only)
  POST     /admin/users/<id>/ai  → Toggle AI access (admin only)
"""

import logging
import os
import threading
import time as _time
from datetime import datetime, timezone
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


_PLAN_ORDER = {"free": 0, "basic": 1, "pro": 2, "api": 3}
_PLAN_LABELS = {"free": "Free", "basic": "Basic $15", "pro": "Pro $39", "api": "API $99"}


class User(UserMixin):
    def __init__(self, data: dict):
        self.id = data["id"]
        self.email = data["email"]
        self.role = data["role"]
        self.analytics_enabled = bool(data.get("analytics_enabled", 0))
        self.ai_enabled = bool(data.get("ai_enabled", 0))
        self.plan = data.get("plan") or "free"
        self.plan_expires = data.get("plan_expires") or ""
        self.api_key = data.get("api_key") or ""
        self.stripe_customer_id = data.get("stripe_customer_id") or ""

    def is_admin(self) -> bool:
        return self.role == "admin"

    def _plan_active(self, min_plan: str) -> bool:
        """Return True if user's plan is >= min_plan and not expired."""
        if _PLAN_ORDER.get(self.plan, 0) < _PLAN_ORDER.get(min_plan, 99):
            return False
        if self.plan_expires:
            try:
                exp = datetime.fromisoformat(self.plan_expires)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    return False
            except Exception:
                pass
        return True

    def can_analytics(self) -> bool:
        return self.role == "admin" or self.analytics_enabled or self._plan_active("pro")

    def can_ai(self) -> bool:
        return self.role == "admin" or self.ai_enabled or self._plan_active("pro")

    def can_realtime(self) -> bool:
        return self.role == "admin" or self._plan_active("basic")

    def can_api(self) -> bool:
        return self.role == "admin" or self._plan_active("api")


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


def ai_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_ai():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "message": "AI access required"}), 403
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
        <th>אימייל</th><th>תפקיד</th><th>תוכנית</th><th>אנליטיקס</th><th>AI Chat</th><th>נוצר</th><th>פעולות</th>
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
          <form method="POST" action="/admin/users/{{ u.id }}/plan" style="display:inline;display:flex;gap:4px;align-items:center">
            <select name="plan" style="padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:11px">
              {% for p in ['free','basic','pro','api'] %}
                <option value="{{ p }}" {% if u.plan == p %}selected{% endif %}>{{ p }}</option>
              {% endfor %}
            </select>
            <button type="submit" class="btn" style="padding:3px 8px;font-size:11px">שמור</button>
          </form>
        </td>
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
        <td>
          {% if u.role == 'admin' %}
            <span style="color:var(--muted);font-size:12px">תמיד</span>
          {% else %}
            <form method="POST" action="/admin/users/{{ u.id }}/ai" style="display:inline">
              <button type="submit" class="btn {% if u.ai_enabled %}btn-green{% endif %}" style="min-width:60px">
                {% if u.ai_enabled %}✓ מופעל{% else %}כבוי{% endif %}
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
  <!-- AI Admin command -->
  <div class="card">
    <h2>🤖 פקודת AI לניהול</h2>
    <p style="font-size:12px;color:var(--muted);margin-bottom:10px">
      הקלד פקודה בשפה חופשית — לדוגמא: "תוריד ל-user@example.com את ה-AI" / "תן לuser@example.com גישת אנליטיקס"
    </p>
    <div id="aiCmdResult" style="display:none;padding:10px;border-radius:6px;margin-bottom:10px;font-size:13px;"></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <input type="text" id="aiCmdInput" placeholder="הקלד פקודה..." style="flex:1;min-width:200px">
      <button class="btn-submit" id="aiCmdBtn" onclick="runAiCommand()">🤖 הפעל</button>
    </div>
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

async function runAiCommand() {
  const inp = document.getElementById('aiCmdInput');
  const cmd = inp.value.trim();
  if (!cmd) return;
  const btn = document.getElementById('aiCmdBtn');
  btn.disabled = true; btn.textContent = '⏳ מעבד...';
  const res = document.getElementById('aiCmdResult');
  res.style.display = 'none';
  try {
    const r = await fetch('/api/admin/ai-command', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: cmd})
    });
    const d = await r.json();
    res.style.display = 'block';
    if (d.ok) {
      res.style.background = 'rgba(63,185,80,.1)';
      res.style.border = '1px solid var(--green)';
      res.style.color = 'var(--green)';
      res.textContent = '✅ ' + (d.explanation || d.message);
      inp.value = '';
      setTimeout(() => location.reload(), 1500);
    } else {
      res.style.background = 'rgba(248,81,73,.1)';
      res.style.border = '1px solid var(--red)';
      res.style.color = 'var(--red)';
      res.textContent = '❌ ' + (d.explanation || d.message || d.error || 'שגיאה');
    }
  } catch(e) {
    res.style.display = 'block';
    res.textContent = 'שגיאת רשת';
  }
  btn.disabled = false; btn.textContent = '🤖 הפעל';
}
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
    <button class="btn btn-orange" id="btnCycle"    onclick="forceCycle()" data-i18n="btn_cycle">⚡ הפעל מחזור עכשיו</button>
    <button class="btn btn-green"  id="btnTelegram" onclick="triggerTelegram()" data-i18n="btn_telegram">📨 בדיקת טלגרם</button>
    <button class="btn btn-blue"   id="btnRefresh"  onclick="triggerRefresh()" data-i18n="btn_refresh">🔄 רענן שווקים</button>
    <div class="threshold-bar">
      <label data-i18n="threshold_label">סף:</label>
      <input type="number" id="thresholdInput" min="0.1" max="100" step="0.1" value="10">
      <span style="color:var(--muted)">%</span>
      <button onclick="setThreshold()" data-i18n="btn_update">עדכן</button>
    </div>
    <span id="actionMsg"></span>
  </div>

  <div class="user-bar">
    <span id="userEmail" style="color:var(--text)"></span>
    <span id="roleBadge" class="role-badge"></span>
    <span id="planBadge" style="display:none;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:700;background:rgba(210,153,34,.15);color:#d29922"></span>
    <a href="/pricing" id="upgradeLink" style="display:none;color:#f0883e;font-size:11px;border:1px solid #f0883e;padding:2px 8px;border-radius:5px">⬆ Upgrade</a>
    <a href="/analytics" id="analyticsLink" style="display:none" data-i18n="nav_analytics">📊 אנליטיקס</a>
    <a href="/ai-chat" id="aiChatLink" style="display:none">🤖 AI Chat</a>
    <a href="/watchlist">⭐ Watchlist</a>
    <a href="/settings" data-i18n="nav_settings">⚙️ הגדרות</a>
    <a href="/pricing" data-i18n="nav_plans">💎 תוכניות</a>
    <a href="/admin" id="adminLink" style="display:none" data-i18n="nav_admin">🔧 ניהול</a>
    <a href="/logout" data-i18n="nav_logout">יציאה</a>
    <button id="langToggle" onclick="toggleLang()" style="padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--muted);font-size:11px;cursor:pointer;font-weight:700">EN</button>
  </div>
</header>

<div class="grid">

  <div class="card">
    <h2 data-i18n="status_title">סטטוס הבוט</h2>
    <div class="stat-grid">
      <div class="stat"><div class="stat-label" data-i18n="stat_cycles">מחזורים</div><div class="stat-value" id="cycleCount">—</div></div>
      <div class="stat"><div class="stat-label" data-i18n="stat_markets">שווקים במעקב</div><div class="stat-value" id="mktCount">—</div></div>
      <div class="stat"><div class="stat-label" data-i18n="stat_last_update">עדכון אחרון</div><div class="stat-value" style="font-size:15px" id="lastUpdate">—</div></div>
      <div class="stat"><div class="stat-label" data-i18n="stat_since">פעיל מאז</div><div class="stat-value" style="font-size:15px" id="startTime">—</div></div>
    </div>
  </div>

  <div class="card">
    <h2><span data-i18n="alerts_title">התראות חיות</span> <span id="alertBadge" class="badge" style="display:none">0</span></h2>
    <div id="feed"><div class="empty" data-i18n="empty_feed">ממתין להתראות...</div></div>
  </div>

  <div class="card card-full">
    <h2>
      <span data-i18n="market_stats_title">סטטיסטיקת שוק</span>
      <span style="font-weight:400;text-transform:none;font-size:11px;color:var(--muted)" data-i18n="market_stats_sub">
        — מחירים = הסתברות (0%–100%) · מקובצים לפי אירוע
      </span>
      <span id="mktBadge" class="badge" style="margin-right:auto"></span>
    </h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th data-i18n="col_topic">נושא</th><th data-i18n="col_outcome">תשובה</th>
          <th data-i18n="col_max_price">מחיר גבוה ביותר באירוע (%)</th>
          <th data-i18n="col_max_change">שינוי מקסימלי בחלון 5 דק׳</th>
          <th data-i18n="col_alerts_sent">התראות שנשלחו לטלגרם</th>
          <th>⭐</th>
        </tr></thead>
        <tbody id="marketBody"><tr><td colspan="6" class="empty" data-i18n="empty_markets">ממתין...</td></tr></tbody>
      </table>
    </div>
    <div class="pager">
      <button id="prevBtn" onclick="changePage(-1)" disabled data-i18n="btn_prev">◀ הקודם</button>
      <span id="pageInfo">—</span>
      <button id="nextBtn" onclick="changePage(1)" disabled data-i18n="btn_next">הבא ▶</button>
    </div>
  </div>

  <div class="card card-full">
    <h2 data-i18n="history_title">היסטוריית התראות</h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th data-i18n="col_time">שעה (UTC)</th><th data-i18n="col_topic">נושא</th><th data-i18n="col_outcome">תשובה</th><th data-i18n="col_jump">קפיצה</th><th data-i18n="col_price_before">מחיר לפני</th><th data-i18n="col_price_after">מחיר אחרי</th><th></th>
        </tr></thead>
        <tbody id="historyBody"><tr><td colspan="7" class="empty" data-i18n="empty_history">אין היסטוריה עדיין.</td></tr></tbody>
      </table>
    </div>
  </div>

</div>

<script>
const LANG = {
  he: {
    nav_analytics:'📊 אנליטיקס', nav_settings:'⚙️ הגדרות', nav_plans:'💎 תוכניות',
    nav_admin:'🔧 ניהול', nav_logout:'יציאה',
    status_title:'סטטוס הבוט', alerts_title:'התראות חיות',
    market_stats_title:'סטטיסטיקת שוק', history_title:'היסטוריית התראות',
    market_stats_sub:'— מחירים = הסתברות (0%–100%) · מקובצים לפי אירוע',
    stat_cycles:'מחזורים', stat_markets:'שווקים במעקב',
    stat_last_update:'עדכון אחרון', stat_since:'פעיל מאז',
    col_topic:'נושא', col_outcome:'תשובה',
    col_max_price:'מחיר גבוה ביותר באירוע (%)',
    col_max_change:'שינוי מקסימלי בחלון 5 דק׳',
    col_alerts_sent:'התראות שנשלחו לטלגרם',
    col_time:'שעה (UTC)', col_jump:'קפיצה',
    col_price_before:'מחיר לפני', col_price_after:'מחיר אחרי',
    btn_prev:'◀ הקודם', btn_next:'הבא ▶',
    btn_cycle:'⚡ הפעל מחזור עכשיו', btn_cycle_load:'⚡ מפעיל...',
    btn_telegram:'📨 בדיקת טלגרם', btn_telegram_load:'📨 שולח...',
    btn_refresh:'🔄 רענן שווקים', btn_refresh_load:'🔄 מרענן...',
    threshold_label:'סף:', btn_update:'עדכן',
    empty_feed:'ממתין להתראות...', empty_markets:'ממתין לנתונים...',
    empty_history:'אין היסטוריה עדיין.', warming_up:'מחמם...',
    page_x:'עמוד', of_x:'מתוך', markets_x:'שווקים', filtered_x:'מסונן',
    refresh_x:'רענון:', free_plan_note:'תוכנית Free — נתונים מתעדכנים בעיכוב 10 דקות.',
    upgrade_link:'שדרג לReal-time', net_error:'שגיאת רשת',
    threshold_invalid:'ערך סף לא תקין',
    add_watchlist:'הוסף ל-Watchlist', rm_watchlist:'הסר מ-Watchlist',
    mute_event:'השתק אירוע זה', open_poly:'פתח בפולימארקט',
  },
  en: {
    nav_analytics:'📊 Analytics', nav_settings:'⚙️ Settings', nav_plans:'💎 Plans',
    nav_admin:'🔧 Admin', nav_logout:'Logout',
    status_title:'Bot Status', alerts_title:'Live Alerts',
    market_stats_title:'Market Statistics', history_title:'Alert History',
    market_stats_sub:'— Prices = probability (0%–100%) · grouped by event',
    stat_cycles:'Cycles', stat_markets:'Markets Tracked',
    stat_last_update:'Last Update', stat_since:'Active Since',
    col_topic:'Topic', col_outcome:'Outcome',
    col_max_price:'Highest Price in Event (%)',
    col_max_change:'Max Change (5 min window)',
    col_alerts_sent:'Telegram Alerts Sent',
    col_time:'Time (UTC)', col_jump:'Change',
    col_price_before:'Price Before', col_price_after:'Price After',
    btn_prev:'◀ Prev', btn_next:'Next ▶',
    btn_cycle:'⚡ Force Cycle Now', btn_cycle_load:'⚡ Running...',
    btn_telegram:'📨 Test Telegram', btn_telegram_load:'📨 Sending...',
    btn_refresh:'🔄 Refresh Markets', btn_refresh_load:'🔄 Refreshing...',
    threshold_label:'Threshold:', btn_update:'Update',
    empty_feed:'Waiting for alerts...', empty_markets:'Waiting for data...',
    empty_history:'No history yet.', warming_up:'Warming up...',
    page_x:'Page', of_x:'of', markets_x:'markets', filtered_x:'filtered',
    refresh_x:'Refreshed:', free_plan_note:'Free plan — data is delayed 10 minutes.',
    upgrade_link:'Upgrade to Real-time', net_error:'Network error',
    threshold_invalid:'Invalid threshold value',
    add_watchlist:'Add to Watchlist', rm_watchlist:'Remove from Watchlist',
    mute_event:'Mute this event', open_poly:'Open on Polymarket',
  }
};
let curLang = localStorage.getItem('polybot_lang') || 'he';
function t(k) { return (LANG[curLang]||{})[k] || LANG.he[k] || k; }
function toggleLang() {
  curLang = curLang === 'he' ? 'en' : 'he';
  localStorage.setItem('polybot_lang', curLang);
  applyLangUI();
}
function applyLangUI() {
  document.documentElement.lang = curLang;
  document.documentElement.dir = curLang === 'he' ? 'rtl' : 'ltr';
  const lt = document.getElementById('langToggle');
  if (lt) lt.textContent = curLang === 'he' ? 'EN' : 'HE';
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  renderMarketPage();
}

const fmtPct = v => v == null ? null : (v * 100).toFixed(1) + '%';
const fmtChg = v => v == null
  ? `<span class="wm">${t('warming_up')}</span>`
  : `<span class="${v>=0?'up':'dn'}">${v>=0?'+':''}${v.toFixed(2)}%</span>`;

let isAdmin = false;
let watchlistTokens = new Set();
let userKeywords = [], userMinPct = 0;

async function loadMyWatchlist() {
  try {
    const r = await fetch('/api/watchlist');
    if (r.ok) {
      const items = await r.json();
      watchlistTokens = new Set(items.map(i => i.token_id));
    }
  } catch(e) {}
}

async function loadMySettings() {
  try {
    const r = await fetch('/api/my-settings');
    if (r.ok) {
      const s = await r.json();
      userKeywords = s.keywords ? s.keywords.split(',').map(k => k.trim().toLowerCase()).filter(Boolean) : [];
      userMinPct = parseFloat(s.min_pct) || 0;
    }
  } catch(e) {}
}

function matchesFilter(event_label, label, pct_change) {
  if (userMinPct > 0 && pct_change != null && Math.abs(pct_change) < userMinPct) return false;
  if (userKeywords.length === 0) return true;
  const text = ((event_label || '') + ' ' + (label || '')).toLowerCase();
  return userKeywords.some(k => text.includes(k));
}

async function toggleWatchlist(btn, tokenId, eventLabel, label) {
  btn.disabled = true;
  try {
    const r = await fetch('/api/watchlist/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token_id: tokenId, event_label: eventLabel, label: label})
    });
    const d = await r.json();
    if (d.added) { watchlistTokens.add(tokenId); btn.textContent = '⭐'; btn.title = t('rm_watchlist'); }
    else { watchlistTokens.delete(tokenId); btn.textContent = '☆'; btn.title = t('add_watchlist'); }
  } catch(e) {}
  btn.disabled = false;
}

function applyAuthUI(admin, email, role, threshold, canAnalytics, canAi, plan, canRealtime) {
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
  if (canAnalytics) document.getElementById('analyticsLink').style.display = 'inline';
  if (canAi) document.getElementById('aiChatLink').style.display = 'inline';
  if (plan && plan !== 'free' && role !== 'admin') {
    const pb = document.getElementById('planBadge');
    pb.textContent = plan.charAt(0).toUpperCase() + plan.slice(1);
    pb.style.display = 'inline-block';
  }
  if (!canRealtime && role !== 'admin') {
    document.getElementById('upgradeLink').style.display = 'inline';
    const feed = document.getElementById('feed');
    if (feed) {
      const note = document.createElement('div');
      note.style.cssText = 'font-size:11px;color:var(--yellow);padding:6px 10px;background:rgba(210,153,34,.08);border-radius:5px;margin-bottom:8px';
      note.innerHTML = `⏱ ${t('free_plan_note')} <a href="/pricing" style="color:var(--accent)">${t('upgrade_link')}</a>`;
      feed.insertBefore(note, feed.firstChild);
    }
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
    if (!map[key]) map[key] = { event_label: ev, label: out, prices: [], pct_changes: [], alert_count: 0, token_id: m.token_id || '' };
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
    token_id: g.token_id,
  })).sort((a,b) => (b.alert_count - a.alert_count) || a.event_label.localeCompare(b.event_label));
}

function renderMarketPage() {
  const filtered = userKeywords.length > 0
    ? allGrouped.filter(m => matchesFilter(m.event_label, m.label, m.max_pct))
    : allGrouped;
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  currentPage = Math.min(currentPage, pages);
  const slice = filtered.slice((currentPage-1)*PAGE_SIZE, currentPage*PAGE_SIZE);
  document.getElementById('pageInfo').textContent = total ? `${t('page_x')} ${currentPage} ${t('of_x')} ${pages}` : '—';
  document.getElementById('prevBtn').disabled = currentPage <= 1;
  document.getElementById('nextBtn').disabled = currentPage >= pages;
  document.getElementById('mktBadge').textContent = total + ' ' + t('markets_x') + (userKeywords.length ? ' (' + t('filtered_x') + ')' : '');
  const tbody = document.getElementById('marketBody');
  if (!slice.length) { tbody.innerHTML = `<tr><td colspan="6" class="empty">${t('empty_markets')}</td></tr>`; return; }
  tbody.innerHTML = slice.map(m => {
    const inWl = m.token_id && watchlistTokens.has(m.token_id);
    const wlBtn = m.token_id ? `<button onclick="toggleWatchlist(this,'${m.token_id}','${(m.event_label||'').replace(/'/g,"\\'")}','${(m.label||'').replace(/'/g,"\\'")}'); event.stopPropagation();"
      style="background:none;border:none;cursor:pointer;font-size:15px;padding:0 2px;opacity:.8"
      title="${inWl ? t('rm_watchlist') : t('add_watchlist')}">${inWl ? '⭐' : '☆'}</button>` : '';
    return `<tr>
      <td class="lbl" title="${m.event_label}">${m.event_label}</td>
      <td class="lbl">${m.label}</td>
      <td class="mono">${m.max_price != null ? fmtPct(m.max_price) : '—'}</td>
      <td>${fmtChg(m.max_pct)}</td>
      <td>${m.alert_count > 0 ? `<span class="badge badge-orange">${m.alert_count}</span>` : '<span class="mu">0</span>'}</td>
      <td>${wlBtn}</td>
    </tr>`;
  }).join('');
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
    document.getElementById('last-refresh').textContent = t('refresh_x') + ' ' + new Date().toLocaleTimeString(curLang === 'he' ? 'he-IL' : 'en-US');

    if (firstLoad) {
      applyAuthUI(data.is_admin, data.user_email, data.user_role, data.threshold, data.can_analytics, data.can_ai, data.user_plan, data.can_realtime);
      firstLoad = false;
    }

    allGrouped = groupByLabel(data.market_stats);
    document.getElementById('mktCount').textContent = allGrouped.length;
    renderMarketPage();

    const hbody = document.getElementById('historyBody');
    const filteredFeed = data.alert_feed.filter(a => matchesFilter(a.event_label, a.label, a.pct_change));
    if (!filteredFeed.length) {
      hbody.innerHTML = `<tr><td colspan="7" class="empty">${t('empty_history')}</td></tr>`;
    } else {
      hbody.innerHTML = filteredFeed.map(a => {
        const ev = a.event_label || a.label;
        const out = (a.label && a.label !== ev) ? a.label : '—';
        const link = a.url ? `<a href="${a.url}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:14px" title="${t('open_poly')}">🔗</a>` : '';
        const muteBtn = isAdmin ? `<button onclick="muteEvent(this,'${ev.replace(/'/g,"\\'")}',event)" style="background:none;border:none;cursor:pointer;font-size:13px;opacity:.5;padding:0 2px" title="${t('mute_event')}">🔕</button>` : '';
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
  } catch(e) { showMsg(t('net_error')); }
  btn.textContent = origText; btn.disabled = false;
}

const forceCycle      = () => apiPost('/api/force-cycle',     'btnCycle',    t('btn_cycle_load'),    t('btn_cycle'));
const triggerTelegram = () => apiPost('/api/trigger-test',    'btnTelegram', t('btn_telegram_load'), t('btn_telegram'));
const triggerRefresh  = () => apiPost('/api/refresh-markets', 'btnRefresh',  t('btn_refresh_load'),  t('btn_refresh'));

async function setThreshold() {
  if (!isAdmin) return;
  const val = parseFloat(document.getElementById('thresholdInput').value);
  if (isNaN(val) || val <= 0) { showMsg(t('threshold_invalid')); return; }
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

loadMyWatchlist();
loadMySettings();
applyLangUI();
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


def _apply_plan_filter(feed: list, user) -> list:
    """Apply free-plan restrictions: 10-min delay + max 5 items."""
    if user.is_admin() or user.can_realtime():
        return feed
    now = _time.time()
    delayed = [a for a in feed if now - a.get("ts", 0) >= 600]
    return delayed[:5]


@app.route("/api/status")
@login_required
def api_status():
    data = store.snapshot()
    data["alert_feed"] = _apply_plan_filter(data["alert_feed"], current_user)
    data["is_admin"] = current_user.is_admin()
    data["user_email"] = current_user.email
    data["user_role"] = current_user.role
    data["can_analytics"] = current_user.can_analytics()
    data["can_ai"] = current_user.can_ai()
    data["user_plan"] = current_user.plan
    data["can_realtime"] = current_user.can_realtime()
    return jsonify(data)


@app.route("/api/feed")
@login_required
def api_feed():
    feed = store.snapshot()["alert_feed"]
    return jsonify(_apply_plan_filter(feed, current_user))


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


@app.route("/admin/users/<int:uid>/ai", methods=["POST"])
@login_required
@admin_required
def admin_toggle_ai(uid):
    user = db.get_user_by_id(uid)
    if not user:
        return redirect(url_for("admin", msg="משתמש לא נמצא", cls="msg-err"))
    new_val = not bool(user.get("ai_enabled", 0))
    db.update_user_ai(uid, new_val)
    state = "מופעל" if new_val else "כבוי"
    return redirect(url_for("admin", msg=f"גישת AI {state} למשתמש {user['email']}"))


@app.route("/api/admin/ai-command", methods=["POST"])
@login_required
@admin_required
def api_admin_ai_command():
    import ai_client
    data = request.get_json(force=True, silent=True) or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"ok": False, "message": "חסרה פקודה"}), 400

    users = db.get_all_users()
    result = ai_client.parse_admin_command(command, users)

    if "error" in result:
        return jsonify({"ok": False, "explanation": result["error"]})

    action = result.get("action", "unknown")
    user_id = result.get("user_id")
    value = result.get("value")
    explanation = result.get("explanation", "")

    if action == "unknown" or user_id is None:
        return jsonify({"ok": False, "explanation": explanation or "לא הצלחתי להבין את הפקודה"})

    if action == "toggle_analytics":
        db.update_user_analytics(int(user_id), bool(value))
    elif action == "toggle_ai":
        db.update_user_ai(int(user_id), bool(value))
    elif action == "toggle_role":
        db.update_user_role(int(user_id), str(value))
    elif action == "delete_user":
        if int(user_id) == current_user.id:
            return jsonify({"ok": False, "explanation": "אי אפשר למחוק את עצמך"})
        db.delete_user(int(user_id))
    else:
        return jsonify({"ok": False, "explanation": f"פעולה לא מוכרת: {action}"})

    return jsonify({"ok": True, "explanation": explanation})


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
# AI Chat
# ---------------------------------------------------------------------------

_AI_CHAT_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — AI Chat</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;
       font-size:14px;height:100vh;display:flex;flex-direction:column;}
  header{padding:12px 20px;border-bottom:1px solid var(--border);
         display:flex;align-items:center;gap:10px;flex-shrink:0;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  a.btn-back{color:var(--accent);text-decoration:none;font-size:13px;
             border:1px solid var(--border);padding:4px 12px;border-radius:6px;}
  a.btn-back:hover{background:#21262d;}
  .chat-area{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:12px;}
  .msg{max-width:80%;padding:10px 14px;border-radius:10px;line-height:1.5;font-size:13px;}
  .msg-user{align-self:flex-end;background:rgba(88,166,255,.15);border:1px solid rgba(88,166,255,.3);
            color:var(--text);}
  .msg-ai{align-self:flex-start;background:var(--card);border:1px solid var(--border);
          color:var(--text);white-space:pre-wrap;}
  .msg-system{align-self:center;color:var(--muted);font-size:12px;font-style:italic;}
  .input-bar{padding:12px 20px;border-top:1px solid var(--border);
             display:flex;gap:8px;flex-shrink:0;}
  .input-bar textarea{flex:1;padding:9px 12px;background:var(--bg);border:1px solid var(--border);
                      border-radius:8px;color:var(--text);font-size:13px;
                      font-family:inherit;resize:none;min-height:44px;max-height:120px;
                      line-height:1.4;}
  .input-bar textarea:focus{outline:none;border-color:var(--accent);}
  .send-btn{padding:9px 18px;background:var(--accent);color:#0d1117;
            border:none;border-radius:8px;font-weight:700;font-size:13px;
            cursor:pointer;align-self:flex-end;white-space:nowrap;}
  .send-btn:hover{opacity:.9;}
  .send-btn:disabled{opacity:.4;cursor:not-allowed;}
  .typing{color:var(--muted);font-size:12px;font-style:italic;align-self:flex-start;}
</style>
</head>
<body>
<header>
  <h1>🤖 AI Chat — Polymarket</h1>
  <a class="btn-back" href="/">← חזרה לדאשבורד</a>
</header>
<div class="chat-area" id="chatArea">
  <div class="msg msg-system">שלום! אני יכול לענות על שאלות לגבי השווקים הפעילים. מה תרצה לדעת?</div>
</div>
<div class="input-bar">
  <textarea id="msgInput" placeholder="שאל שאלה על שוק, תחזה, נושא..." rows="1"
            onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();sendMsg();}"></textarea>
  <button class="send-btn" id="sendBtn" onclick="sendMsg()">שלח ▶</button>
</div>
<script>
const chatArea = document.getElementById('chatArea');

function appendMsg(text, cls) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
  return div;
}

async function sendMsg() {
  const inp = document.getElementById('msgInput');
  const btn = document.getElementById('sendBtn');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  inp.style.height = '';
  appendMsg(text, 'msg-user');
  btn.disabled = true;
  const typing = appendMsg('מקליד...', 'typing');
  try {
    const r = await fetch('/api/ai-chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    chatArea.removeChild(typing);
    if (r.status === 403) { appendMsg('אין לך גישה ל-AI Chat.', 'msg-system'); }
    else {
      const d = await r.json();
      appendMsg(d.reply || '...', 'msg-ai');
    }
  } catch(e) {
    chatArea.removeChild(typing);
    appendMsg('שגיאת רשת. נסה שוב.', 'msg-system');
  }
  btn.disabled = false;
  inp.focus();
}

// Auto-resize textarea
document.getElementById('msgInput').addEventListener('input', function() {
  this.style.height = '';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});
</script>
</body>
</html>"""


_SETTINGS_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — הגדרות</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}
  header{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  a.btn-back{color:var(--accent);text-decoration:none;font-size:13px;
             border:1px solid var(--border);padding:4px 12px;border-radius:6px;}
  a.btn-back:hover{background:#21262d;}
  .page{padding:20px;max-width:600px;margin:0 auto;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;}
  h2{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;
     letter-spacing:.5px;margin-bottom:14px;}
  label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px;}
  input{width:100%;padding:8px 12px;background:var(--bg);border:1px solid var(--border);
        border-radius:6px;color:var(--text);font-size:13px;margin-bottom:14px;}
  input:focus{outline:none;border-color:var(--accent);}
  .hint{font-size:11px;color:var(--muted);margin-top:-10px;margin-bottom:14px;}
  .btn-save{padding:8px 22px;background:var(--accent);color:#0d1117;border:none;
            border-radius:6px;font-weight:700;font-size:13px;cursor:pointer;}
  .btn-save:hover{opacity:.9;}
  .msg{padding:8px 12px;border-radius:6px;margin-bottom:14px;font-size:13px;}
  .msg-ok{background:rgba(63,185,80,.1);border:1px solid var(--green);color:var(--green);}
  .msg-err{background:rgba(248,81,73,.1);border:1px solid var(--red);color:var(--red);}
</style>
</head>
<body>
<header>
  <h1>⚙️ הגדרות אישיות</h1>
  <a class="btn-back" href="/">← חזרה לדאשבורד</a>
</header>
<div class="page">
  <div id="msgBox"></div>
  <div class="card">
    <h2>פילטר התראות</h2>
    <label>מילות מפתח (מופרדות בפסיק)</label>
    <input type="text" id="kwInput" placeholder="לדוגמא: טראמפ, ביטקויין, איראן">
    <p class="hint">רק התראות שמכילות לפחות מילה אחת מהרשימה יוצגו בדאשבורד. ריק = הצג הכל.</p>
    <label>סף מינימלי להצגה (%)</label>
    <input type="number" id="minPctInput" placeholder="0 = ברירת מחדל" min="0" max="100" step="0.5">
    <p class="hint">הצג רק התראות עם קפיצה של לפחות X%. 0 = השתמש בהגדרת הבוט הגלובלית.</p>
    <button class="btn-save" onclick="saveSettings()">שמור הגדרות</button>
  </div>
  <div class="card" style="font-size:12px;color:var(--muted);line-height:1.6">
    <h2>הערות</h2>
    <p>• הפילטרים חלים רק על <b>תצוגת הדאשבורד שלך</b> — לא על התראות הטלגרם.</p>
    <p>• הגדרות נשמרות לחשבונך ומסונכרנות בכל כניסה.</p>
    <p>• ה-Watchlist מאפשר לך לעקוב אחרי שווקים ספציפיים — נגיש דרך הכפתור ⭐ בטבלת השווקים.</p>
  </div>
</div>
<script>
async function loadSettings() {
  const r = await fetch('/api/my-settings');
  if (r.ok) {
    const s = await r.json();
    document.getElementById('kwInput').value = s.keywords || '';
    document.getElementById('minPctInput').value = s.min_pct || '';
  }
}
async function saveSettings() {
  const keywords = document.getElementById('kwInput').value.trim();
  const min_pct = parseFloat(document.getElementById('minPctInput').value) || 0;
  const r = await fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({keywords, min_pct})
  });
  const d = await r.json();
  const box = document.getElementById('msgBox');
  box.innerHTML = `<div class="msg ${d.ok ? 'msg-ok' : 'msg-err'}">${d.ok ? '✅ הגדרות נשמרו בהצלחה' : '❌ ' + d.message}</div>`;
  setTimeout(() => box.innerHTML = '', 4000);
}
loadSettings();
</script>
</body>
</html>"""

_WATCHLIST_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — Watchlist</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--orange:#f0883e;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}
  header{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  a.btn-back{color:var(--accent);text-decoration:none;font-size:13px;
             border:1px solid var(--border);padding:4px 12px;border-radius:6px;}
  a.btn-back:hover{background:#21262d;}
  .page{display:flex;gap:16px;padding:16px 20px;height:calc(100vh - 53px);}
  .sidebar{width:300px;flex-shrink:0;display:flex;flex-direction:column;gap:10px;overflow-y:auto;}
  .main{flex:1;display:flex;flex-direction:column;gap:12px;min-width:0;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;}
  .card h2{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;}
  .wl-item{padding:10px 12px;border-radius:6px;border:1px solid var(--border);cursor:pointer;
           background:var(--bg);margin-bottom:6px;transition:border-color .15s;display:flex;align-items:center;gap:8px;}
  .wl-item:hover{border-color:var(--accent);}
  .wl-item.active{border-color:var(--accent);background:rgba(88,166,255,.06);}
  .wl-label{flex:1;overflow:hidden;}
  .wl-ev{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .wl-out{font-size:11px;color:var(--muted);margin-top:2px;}
  .wl-rm{background:none;border:none;cursor:pointer;font-size:14px;opacity:.5;padding:0;flex-shrink:0;}
  .wl-rm:hover{opacity:1;}
  .price{font-size:12px;color:var(--accent);font-family:Consolas,monospace;}
  .empty{color:var(--muted);padding:14px 0;text-align:center;font-size:13px;}
  .chart-card{flex:1;display:flex;flex-direction:column;}
  .chart-header{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;}
  .chart-title{font-size:15px;font-weight:600;flex:1;}
  .chart-sub{font-size:12px;color:var(--muted);}
  .chart-link{color:var(--accent);font-size:13px;text-decoration:none;}
  .chart-link:hover{text-decoration:underline;}
  .chart-wrap{flex:1;position:relative;min-height:300px;}
  .placeholder{display:flex;align-items:center;justify-content:center;height:100%;
               color:var(--muted);font-size:14px;border:1px dashed var(--border);border-radius:8px;}
</style>
</head>
<body>
<header>
  <h1>⭐ Watchlist שלי</h1>
  <span id="refreshNote" style="font-size:11px;color:var(--muted)"></span>
  <a class="btn-back" href="/">← חזרה לדאשבורד</a>
</header>
<div class="page">
  <div class="sidebar">
    <div class="card" style="flex:1">
      <h2>שווקים במעקב <span id="countBadge"></span></h2>
      <div id="wlList"><div class="empty">טוען...</div></div>
    </div>
  </div>
  <div class="main">
    <div class="card chart-card">
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
let wlItems = [];
let selectedToken = null;
let chartInstance = null;

async function loadWatchlist() {
  try {
    const r = await fetch('/api/watchlist');
    if (r.status === 401) { window.location.href = '/login'; return; }
    wlItems = await r.json();
    renderList();
    document.getElementById('countBadge').textContent = wlItems.length || '';
    document.getElementById('refreshNote').textContent = 'עדכון: ' + new Date().toLocaleTimeString('he-IL');
    if (selectedToken) loadChart(selectedToken);
  } catch(e) {}
}

function renderList() {
  const el = document.getElementById('wlList');
  if (!wlItems.length) {
    el.innerHTML = '<div class="empty">הWatchlist ריק. הוסף שווקים דרך הטבלה בדאשבורד.</div>';
    document.getElementById('countBadge').textContent = '';
    return;
  }
  el.innerHTML = wlItems.map(m => {
    const active = m.token_id === selectedToken ? ' active' : '';
    return `<div class="wl-item${active}" onclick="selectMarket('${m.token_id}')">
      <div class="wl-label">
        <div class="wl-ev" title="${m.event_label}">${m.event_label || m.token_id}</div>
        ${m.label ? `<div class="wl-out">${m.label}</div>` : ''}
      </div>
      <button class="wl-rm" onclick="removeFromWl(event,'${m.token_id}')" title="הסר מ-Watchlist">✕</button>
    </div>`;
  }).join('');
}

function selectMarket(tokenId) {
  selectedToken = tokenId;
  renderList();
  loadChart(tokenId);
}

async function removeFromWl(e, tokenId) {
  e.stopPropagation();
  await fetch('/api/watchlist/toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token_id: tokenId})
  });
  if (selectedToken === tokenId) { selectedToken = null; resetChart(); }
  loadWatchlist();
}

async function loadChart(tokenId) {
  try {
    const r = await fetch('/api/watchlist-chart/' + tokenId);
    if (!r.ok) {
      showNoData();
      return;
    }
    const data = await r.json();
    if (!data.history || !data.history.length) { showNoData(); return; }
    renderChart(data);
  } catch(e) { showNoData(); }
}

function showNoData() {
  document.getElementById('chartPlaceholder').textContent = 'אין עדיין נתוני מחיר. ממתין לנתונים...';
  document.getElementById('chartPlaceholder').style.display = 'flex';
  document.getElementById('priceChart').style.display = 'none';
}

function resetChart() {
  document.getElementById('chartTitle').textContent = 'בחר שוק מהרשימה';
  document.getElementById('chartSub').textContent = '';
  document.getElementById('chartLink').style.display = 'none';
  document.getElementById('chartPlaceholder').textContent = '← בחר שוק מהרשימה כדי לראות גרף מחיר';
  document.getElementById('chartPlaceholder').style.display = 'flex';
  document.getElementById('priceChart').style.display = 'none';
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
  const pointRadii = labels.map(t => alertSet.has(t) ? 6 : 2);
  const pointColors = labels.map(t => alertSet.has(t) ? '#f85149' : '#58a6ff');
  if (chartInstance) chartInstance.destroy();
  chartInstance = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets: [{ label: 'מחיר (%)', data: prices,
      borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)',
      fill: true, tension: 0.3, pointRadius: pointRadii,
      pointBackgroundColor: pointColors, pointBorderColor: pointColors, borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: ctx => ctx.parsed.y.toFixed(1) + '%' + (alertSet.has(labels[ctx.dataIndex]) ? ' 🔔' : '') } } },
      scales: {
        x: { ticks: { color: '#8b949e', maxTicksLimit: 8, maxRotation: 0 }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', callback: v => v + '%' }, grid: { color: '#21262d' }, min: 0, max: 100 }
      }
    }
  });
}

loadWatchlist();
setInterval(loadWatchlist, 30000);
</script>
</body>
</html>"""


@app.route("/ai-chat")
@login_required
@ai_required
def ai_chat():
    return _AI_CHAT_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/ai-chat", methods=["POST"])
@login_required
@ai_required
def api_ai_chat():
    import ai_client
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"reply": "שלח שאלה תחילה."}), 400
    market_context = store.get_hot_markets()
    reply = ai_client.chat_with_markets(message, market_context)
    return jsonify({"reply": reply})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings")
@login_required
def settings_page():
    return _SETTINGS_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/my-settings")
@login_required
def api_my_settings():
    prefs = db.get_user_preferences(current_user.id)
    return jsonify(prefs)


@app.route("/api/settings", methods=["POST"])
@login_required
def api_save_settings():
    data = request.get_json(force=True, silent=True) or {}
    keywords = (data.get("keywords") or "").strip()
    try:
        min_pct = max(0.0, float(data.get("min_pct") or 0))
    except (ValueError, TypeError):
        min_pct = 0.0
    db.set_user_preferences(current_user.id, keywords, min_pct)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

@app.route("/watchlist")
@login_required
def watchlist_page():
    return _WATCHLIST_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/watchlist")
@login_required
def api_get_watchlist():
    items = db.get_watchlist(current_user.id)
    # Enrich with live price from store
    hot = {m["token_id"]: m for m in store.get_hot_markets()}
    for item in items:
        tid = item["token_id"]
        if tid in hot:
            item["current_price"] = hot[tid].get("current_price")
            item["alert_count"] = hot[tid].get("alert_count", 0)
        else:
            item["current_price"] = None
            item["alert_count"] = 0
    return jsonify(items)


@app.route("/api/watchlist/toggle", methods=["POST"])
@login_required
def api_watchlist_toggle():
    data = request.get_json(force=True, silent=True) or {}
    token_id = (data.get("token_id") or "").strip()
    if not token_id:
        return jsonify({"ok": False, "message": "חסר token_id"}), 400
    event_label = (data.get("event_label") or "").strip()
    label = (data.get("label") or "").strip()
    result = db.toggle_watchlist(current_user.id, token_id, event_label, label)
    if result["added"]:
        # Start price tracking even if this market hasn't alerted yet
        store.add_watch_token(token_id)
    return jsonify({"ok": True, **result})


@app.route("/api/watchlist-chart/<token_id>")
@login_required
def api_watchlist_chart(token_id):
    """Chart data for a watchlisted token — no analytics permission required."""
    # Verify token is in this user's watchlist
    wl = db.get_watchlist(current_user.id)
    if not any(item["token_id"] == token_id for item in wl):
        return jsonify({"ok": False, "message": "Not in watchlist"}), 403
    data = store.get_chart_data(token_id)
    if data is None:
        return jsonify({"ok": False, "history": [], "alert_times": []}), 404
    return jsonify(data)


# ---------------------------------------------------------------------------
# Admin — set user plan
# ---------------------------------------------------------------------------

@app.route("/admin/users/<int:uid>/plan", methods=["POST"])
@login_required
@admin_required
def admin_set_plan(uid):
    plan = request.form.get("plan", "free")
    if plan not in ("free", "basic", "pro", "api"):
        return redirect(url_for("admin", msg="תוכנית לא תקינה", cls="msg-err"))
    db.update_user_plan(uid, plan)
    user = db.get_user_by_id(uid)
    return redirect(url_for("admin", msg=f"תוכנית עודכנה ל-{plan} עבור {user['email'] if user else uid}"))


# ---------------------------------------------------------------------------
# Pricing page
# ---------------------------------------------------------------------------

_PRICING_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyBot — Plans</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;
        --orange:#f0883e;--yellow:#d29922;--purple:#bc8cff;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}
  header{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;}
  header h1{font-size:16px;font-weight:600;flex:1;}
  a.btn-back{color:var(--accent);text-decoration:none;font-size:13px;
             border:1px solid var(--border);padding:4px 12px;border-radius:6px;}
  a.btn-back:hover{background:#21262d;}
  .lang-btn{padding:3px 10px;border-radius:4px;border:1px solid var(--border);
            background:var(--card);color:var(--muted);font-size:11px;cursor:pointer;font-weight:700;}
  .page{padding:32px 20px;max-width:900px;margin:0 auto;}
  .hero{text-align:center;margin-bottom:36px;}
  .hero h2{font-size:26px;font-weight:700;margin-bottom:8px;}
  .hero p{color:var(--muted);font-size:14px;}
  .plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:32px;}
  .plan{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px 20px;
        display:flex;flex-direction:column;gap:10px;position:relative;}
  .plan.popular{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent);}
  .plan-popular-tag{position:absolute;top:-12px;right:50%;transform:translateX(50%);
                    background:var(--accent);color:#0d1117;font-size:11px;font-weight:700;
                    padding:2px 12px;border-radius:999px;}
  .plan-name{font-size:16px;font-weight:700;}
  .plan-price{font-size:28px;font-weight:800;color:var(--accent);}
  .plan-price span{font-size:13px;font-weight:400;color:var(--muted);}
  .plan-features{list-style:none;display:flex;flex-direction:column;gap:7px;flex:1;}
  .plan-features li{font-size:13px;color:var(--muted);display:flex;align-items:flex-start;gap:6px;}
  .plan-features li.yes{color:var(--text);}
  .plan-features li::before{content:'✓';color:var(--green);font-weight:700;flex-shrink:0;}
  .plan-features li.no::before{content:'✗';color:var(--red);}
  .plan-features li.no{color:var(--muted);}
  .plan-btn{width:100%;padding:10px;border-radius:8px;border:none;font-weight:700;font-size:14px;
            cursor:pointer;margin-top:8px;transition:opacity .15s;}
  .plan-btn:hover{opacity:.85;}
  .plan-btn:disabled{opacity:.4;cursor:not-allowed;}
  .btn-basic{background:var(--green);color:#0d1117;}
  .btn-pro{background:var(--accent);color:#0d1117;}
  .btn-api{background:var(--purple);color:#0d1117;}
  .current-badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;
                 font-weight:700;background:rgba(63,185,80,.15);color:var(--green);margin-top:4px;}
  .msg{padding:10px 14px;border-radius:8px;margin-bottom:20px;font-size:13px;}
  .msg-ok{background:rgba(63,185,80,.1);border:1px solid var(--green);color:var(--green);}
  .msg-err{background:rgba(248,81,73,.1);border:1px solid var(--red);color:var(--red);}
  .api-key-box{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;}
  .api-key-box h3{font-size:13px;font-weight:600;margin-bottom:10px;}
  .key-row{display:flex;gap:8px;align-items:center;}
  .key-val{flex:1;padding:7px 12px;background:var(--bg);border:1px solid var(--border);
           border-radius:6px;color:var(--accent);font-family:Consolas,monospace;font-size:12px;
           overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .btn-sm{padding:6px 14px;border-radius:5px;border:1px solid var(--border);background:var(--card);
          color:var(--text);font-size:12px;cursor:pointer;white-space:nowrap;}
  .btn-sm:hover{background:#21262d;}
  #requestMsg{padding:12px 16px;border-radius:8px;margin-top:16px;font-size:13px;
              text-align:center;display:none;}
</style>
</head>
<body>
<header>
  <h1>💎 <span data-i18n="title">תוכניות PolyBot</span></h1>
  <a class="btn-back" href="/">← <span data-i18n="back">חזרה לדאשבורד</span></a>
  <button class="lang-btn" id="langToggle" onclick="toggleLang()">EN</button>
</header>
<div class="page">
  {% if msg %}<div class="msg {{ msg_cls }}">{{ msg }}</div>{% endif %}

  <div class="hero">
    <h2 data-i18n="hero_title">בחר תוכנית שמתאימה לך</h2>
    <p data-i18n="hero_sub">כל התוכניות כוללות גישה לדאשבורד. שדרג לקבל נתונים בזמן אמת, Analytics ו-AI.</p>
  </div>

  <div class="plans">
    <!-- Free -->
    <div class="plan">
      <div class="plan-name">Free</div>
      <div class="plan-price">$0 <span data-i18n="per_month">/חודש</span></div>
      <ul class="plan-features">
        <li class="yes" data-i18n="feat_dashboard">גישה לדאשבורד</li>
        <li class="no" data-i18n="feat_free_delay">עיכוב 10 דקות בנתונים</li>
        <li class="no" data-i18n="feat_free_limit">מקסימום 5 התראות בהיסטוריה</li>
        <li class="no">Analytics</li>
        <li class="no">AI Chat</li>
        <li class="no" data-i18n="feat_watchlist">Watchlist עם גרפים</li>
        <li class="no">REST API</li>
      </ul>
      {% if current_plan == 'free' %}
        <div class="current-badge" data-i18n="current_plan">התוכנית הנוכחית שלך</div>
      {% endif %}
    </div>

    <!-- Basic -->
    <div class="plan">
      <div class="plan-name">Basic</div>
      <div class="plan-price">$15 <span data-i18n="per_month">/חודש</span></div>
      <ul class="plan-features">
        <li class="yes" data-i18n="feat_dashboard">גישה לדאשבורד</li>
        <li class="yes" data-i18n="feat_realtime">נתונים בזמן אמת</li>
        <li class="yes" data-i18n="feat_full_history">היסטוריה מלאה</li>
        <li class="yes" data-i18n="feat_watchlist">Watchlist עם גרפים</li>
        <li class="yes" data-i18n="feat_filters">הגדרות פילטר</li>
        <li class="no">Analytics</li>
        <li class="no">AI Chat</li>
        <li class="no">REST API</li>
      </ul>
      {% if current_plan == 'basic' %}
        <div class="current-badge" data-i18n="current_plan">התוכנית הנוכחית שלך</div>
      {% else %}
        <button class="plan-btn btn-basic" onclick="requestPlan('basic')" data-i18n="btn_contact">צור קשר לשדרוג</button>
      {% endif %}
    </div>

    <!-- Pro -->
    <div class="plan popular">
      <div class="plan-popular-tag" data-i18n="most_popular">הכי פופולרי</div>
      <div class="plan-name">Pro</div>
      <div class="plan-price">$39 <span data-i18n="per_month">/חודש</span></div>
      <ul class="plan-features">
        <li class="yes" data-i18n="feat_all_basic">הכל מ-Basic</li>
        <li class="yes" data-i18n="feat_analytics">Analytics — גרפי מחיר</li>
        <li class="yes" data-i18n="feat_ai_chat">AI Chat בעברית</li>
        <li class="yes" data-i18n="feat_ai_alerts">סיכום AI בהתראות טלגרם</li>
        <li class="no">REST API</li>
      </ul>
      {% if current_plan == 'pro' %}
        <div class="current-badge" data-i18n="current_plan">התוכנית הנוכחית שלך</div>
      {% else %}
        <button class="plan-btn btn-pro" onclick="requestPlan('pro')" data-i18n="btn_contact">צור קשר לשדרוג</button>
      {% endif %}
    </div>

    <!-- API -->
    <div class="plan">
      <div class="plan-name">API</div>
      <div class="plan-price">$99 <span data-i18n="per_month">/חודש</span></div>
      <ul class="plan-features">
        <li class="yes" data-i18n="feat_all_pro">הכל מ-Pro</li>
        <li class="yes">REST API — /api/v1/feed</li>
        <li class="yes" data-i18n="feat_api_key">API Key אישי</li>
        <li class="yes" data-i18n="feat_realtime_json">JSON feed בזמן אמת</li>
        <li class="yes" data-i18n="feat_integrations">אינטגרציה עם מערכות חיצוניות</li>
      </ul>
      {% if current_plan == 'api' %}
        <div class="current-badge" data-i18n="current_plan">התוכנית הנוכחית שלך</div>
      {% else %}
        <button class="plan-btn btn-api" onclick="requestPlan('api')" data-i18n="btn_contact">צור קשר לשדרוג</button>
      {% endif %}
    </div>
  </div>

  {% if current_plan == 'api' %}
  <div class="api-key-box">
    <h3>🔑 <span data-i18n="api_key_title">ה-API Key שלך</span></h3>
    <div class="key-row">
      <div class="key-val" id="apiKeyVal">{{ api_key or '—' }}</div>
      <button class="btn-sm" onclick="copyKey()" data-i18n="btn_copy">העתק</button>
      <button class="btn-sm" onclick="regenKey()" data-i18n="btn_regen">צור מחדש</button>
    </div>
    <p style="font-size:11px;color:var(--muted);margin-top:8px">
      <span data-i18n="api_usage">שימוש:</span> <code style="color:var(--accent)">GET /api/v1/feed?api_key=YOUR_KEY</code>
    </p>
  </div>
  {% endif %}

  <div id="requestMsg"></div>
</div>

<script>
const PLANG = {
  he: {
    title: 'תוכניות PolyBot', back: 'חזרה לדאשבורד',
    hero_title: 'בחר תוכנית שמתאימה לך',
    hero_sub: 'כל התוכניות כוללות גישה לדאשבורד. שדרג לקבל נתונים בזמן אמת, Analytics ו-AI.',
    per_month: '/חודש', current_plan: 'התוכנית הנוכחית שלך', most_popular: 'הכי פופולרי',
    btn_contact: 'צור קשר לשדרוג', btn_copy: 'העתק', btn_regen: 'צור מחדש',
    api_key_title: 'ה-API Key שלך', api_usage: 'שימוש:',
    feat_dashboard: 'גישה לדאשבורד', feat_free_delay: 'עיכוב 10 דקות בנתונים',
    feat_free_limit: 'מקסימום 5 התראות בהיסטוריה', feat_watchlist: 'Watchlist עם גרפים',
    feat_realtime: 'נתונים בזמן אמת', feat_full_history: 'היסטוריה מלאה',
    feat_filters: 'הגדרות פילטר', feat_all_basic: 'הכל מ-Basic',
    feat_analytics: 'Analytics — גרפי מחיר', feat_ai_chat: 'AI Chat בעברית',
    feat_ai_alerts: 'סיכום AI בהתראות טלגרם', feat_all_pro: 'הכל מ-Pro',
    feat_api_key: 'API Key אישי', feat_realtime_json: 'JSON feed בזמן אמת',
    feat_integrations: 'אינטגרציה עם מערכות חיצוניות',
    request_sent: '✅ בקשתך נשלחה! ניצור איתך קשר בקרוב.',
    request_err: 'שגיאה בשליחת הבקשה. נסה שוב.',
    confirm_regen: 'לצור API Key חדש? המפתח הישן יפסיק לעבוד.',
    copied: 'API Key הועתק',
  },
  en: {
    title: 'PolyBot Plans', back: 'Back to Dashboard',
    hero_title: 'Choose a plan that fits you',
    hero_sub: 'All plans include dashboard access. Upgrade for real-time data, Analytics & AI.',
    per_month: '/month', current_plan: 'Your current plan', most_popular: 'Most Popular',
    btn_contact: 'Contact to Upgrade', btn_copy: 'Copy', btn_regen: 'Regenerate',
    api_key_title: 'Your API Key', api_usage: 'Usage:',
    feat_dashboard: 'Dashboard access', feat_free_delay: '10-minute data delay',
    feat_free_limit: 'Max 5 alerts in history', feat_watchlist: 'Watchlist with charts',
    feat_realtime: 'Real-time data', feat_full_history: 'Full alert history',
    feat_filters: 'Filter settings', feat_all_basic: 'Everything in Basic',
    feat_analytics: 'Analytics — price charts', feat_ai_chat: 'AI Chat',
    feat_ai_alerts: 'AI summary in Telegram alerts', feat_all_pro: 'Everything in Pro',
    feat_api_key: 'Personal API Key', feat_realtime_json: 'Real-time JSON feed',
    feat_integrations: 'External integrations',
    request_sent: "✅ Request sent! We'll contact you via Telegram shortly.",
    request_err: 'Error sending request. Please try again.',
    confirm_regen: 'Generate a new API Key? The old key will stop working.',
    copied: 'API Key copied',
  }
};
let curLang = localStorage.getItem('polybot_lang') || 'he';
function pt(k) { return (PLANG[curLang]||{})[k] || PLANG.he[k] || k; }
function toggleLang() {
  curLang = curLang === 'he' ? 'en' : 'he';
  localStorage.setItem('polybot_lang', curLang);
  applyPLang();
}
function applyPLang() {
  document.documentElement.lang = curLang;
  document.documentElement.dir = curLang === 'he' ? 'rtl' : 'ltr';
  document.getElementById('langToggle').textContent = curLang === 'he' ? 'EN' : 'HE';
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = pt(el.dataset.i18n); });
}
async function requestPlan(plan) {
  const btn = event.currentTarget;
  btn.disabled = true;
  const msgEl = document.getElementById('requestMsg');
  try {
    const r = await fetch('/api/request-upgrade', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan})
    });
    const d = await r.json();
    msgEl.textContent = d.ok ? pt('request_sent') : pt('request_err');
    msgEl.style.cssText = d.ok
      ? 'display:block;padding:12px;border-radius:8px;background:rgba(63,185,80,.1);border:1px solid #3fb950;color:#3fb950;text-align:center;margin-top:16px'
      : 'display:block;padding:12px;border-radius:8px;background:rgba(248,81,73,.1);border:1px solid #f85149;color:#f85149;text-align:center;margin-top:16px';
    setTimeout(() => { msgEl.style.display = 'none'; btn.disabled = false; }, 5000);
  } catch(e) { btn.disabled = false; }
}
function copyKey() {
  navigator.clipboard.writeText(document.getElementById('apiKeyVal').textContent)
    .then(() => alert(pt('copied')));
}
async function regenKey() {
  if (!confirm(pt('confirm_regen'))) return;
  const r = await fetch('/api/stripe/regen-key', {method: 'POST'});
  const d = await r.json();
  if (d.api_key) document.getElementById('apiKeyVal').textContent = d.api_key;
}
applyPLang();
</script>
</body>
</html>"""


@app.route("/pricing")
@login_required
def pricing():
    msg = request.args.get("msg", "")
    msg_cls = "msg-ok" if request.args.get("ok") else "msg-err"
    return render_template_string(
        _PRICING_HTML,
        current_plan=current_user.plan,
        api_key=current_user.api_key,
        msg=msg,
        msg_cls=msg_cls,
    )


@app.route("/api/request-upgrade", methods=["POST"])
@login_required
def api_request_upgrade():
    data = request.get_json(force=True, silent=True) or {}
    plan = data.get("plan", "")
    if plan not in ("basic", "pro", "api"):
        return jsonify({"ok": False, "message": "Invalid plan"}), 400
    plan_labels = {"basic": "Basic ($15/mo)", "pro": "Pro ($39/mo)", "api": "API ($99/mo)"}
    text = (
        f"💎 <b>Upgrade Request</b>\n\n"
        f"👤 <code>{current_user.email}</code>\n"
        f"📋 Plan: <b>{plan_labels[plan]}</b>\n"
        f"📍 Current: {current_user.plan}"
    )
    with store._lock:
        alerter = store._alerter
    if alerter:
        try:
            alerter._send_message(text)
        except Exception:
            pass
    return jsonify({"ok": True, "message": "Request sent!"})


# ---------------------------------------------------------------------------
# Stripe routes
# ---------------------------------------------------------------------------

_STRIPE_PRICES = {
    "basic": "STRIPE_PRICE_BASIC",
    "pro":   "STRIPE_PRICE_PRO",
    "api":   "STRIPE_PRICE_API",
}


def _get_stripe():
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        return None
    try:
        import stripe
        stripe.api_key = key
        return stripe
    except ImportError:
        return None


@app.route("/api/stripe/create-checkout", methods=["POST"])
@login_required
def api_stripe_create_checkout():
    stripe = _get_stripe()
    if not stripe:
        return jsonify({"ok": False, "message": "Stripe לא מוגדר — הוסף STRIPE_SECRET_KEY ל-.env"}), 503

    data = request.get_json(force=True, silent=True) or {}
    plan = data.get("plan", "")
    price_env = _STRIPE_PRICES.get(plan)
    if not price_env:
        return jsonify({"ok": False, "message": "תוכנית לא תקינה"}), 400

    price_id = os.getenv(price_env, "").strip()
    if not price_id:
        return jsonify({"ok": False, "message": f"חסר {price_env} ב-.env"}), 503

    base_url = request.host_url.rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=str(current_user.id),
            customer_email=current_user.email,
            metadata={"plan": plan, "user_id": str(current_user.id)},
            success_url=f"{base_url}/pricing?success=1",
            cancel_url=f"{base_url}/pricing?cancel=1",
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as exc:
        logger.error("Stripe checkout error: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.route("/api/stripe/webhook", methods=["POST"])
def api_stripe_webhook():
    stripe = _get_stripe()
    if not stripe:
        return "", 503

    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret) if webhook_secret \
            else stripe.Event.construct_from(request.get_json(force=True), stripe.api_key)
    except Exception as exc:
        logger.warning("Stripe webhook error: %s", exc)
        return "", 400

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        user_id = int(obj.get("client_reference_id") or 0)
        plan = (obj.get("metadata") or {}).get("plan", "basic")
        customer_id = obj.get("customer", "")
        if user_id:
            from datetime import timedelta
            expires = (datetime.now(timezone.utc) + timedelta(days=35)).isoformat()
            db.update_user_plan(user_id, plan, expires, customer_id)
            logger.info("Plan updated: user=%d plan=%s", user_id, plan)

    elif etype in ("invoice.payment_succeeded",):
        customer_id = obj.get("customer", "")
        user_data = db.get_user_by_stripe_customer(customer_id)
        if user_data:
            from datetime import timedelta
            expires = (datetime.now(timezone.utc) + timedelta(days=35)).isoformat()
            db.update_user_plan(user_data["id"], user_data.get("plan", "basic"), expires)

    elif etype in ("customer.subscription.deleted", "invoice.payment_failed"):
        customer_id = obj.get("customer", "")
        user_data = db.get_user_by_stripe_customer(customer_id)
        if user_data:
            db.update_user_plan(user_data["id"], "free")
            logger.info("Plan downgraded to free: user=%d", user_data["id"])

    return "", 200


@app.route("/api/stripe/portal", methods=["POST"])
@login_required
def api_stripe_portal():
    stripe = _get_stripe()
    if not stripe:
        return jsonify({"ok": False, "message": "Stripe לא מוגדר"}), 503
    if not current_user.stripe_customer_id:
        return jsonify({"ok": False, "message": "אין customer ID — צור מנוי תחילה"}), 400
    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=request.host_url.rstrip("/") + "/pricing",
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.route("/api/stripe/regen-key", methods=["POST"])
@login_required
def api_stripe_regen_key():
    if not current_user.can_api():
        return jsonify({"ok": False, "message": "API plan required"}), 403
    key = db.generate_api_key(current_user.id)
    return jsonify({"ok": True, "api_key": key})


# ---------------------------------------------------------------------------
# REST API feed (api plan)
# ---------------------------------------------------------------------------

@app.route("/api/v1/feed")
def api_v1_feed():
    api_key = request.args.get("api_key", "").strip()
    if not api_key:
        return jsonify({"error": "api_key required"}), 401
    user_data = db.get_user_by_api_key(api_key)
    if not user_data:
        return jsonify({"error": "invalid api_key"}), 401
    user = User(user_data)
    if not user.can_api():
        return jsonify({"error": "API plan required"}), 403
    return jsonify(store.snapshot()["alert_feed"])


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
