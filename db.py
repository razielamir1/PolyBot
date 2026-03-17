"""
db.py — SQLite user management for the PolyBot dashboard.

Users table: id, email (unique), password_hash, role ('admin' | 'viewer'), created_at
"""

import os
import sqlite3
import uuid

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                email             TEXT    UNIQUE NOT NULL,
                password_hash     TEXT    NOT NULL,
                role              TEXT    NOT NULL DEFAULT 'viewer',
                created_at        TEXT    DEFAULT (datetime('now'))
            )
        """)
        try:
            con.execute("ALTER TABLE users ADD COLUMN analytics_enabled INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        try:
            con.execute("ALTER TABLE users ADD COLUMN ai_enabled INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        try:
            con.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE users ADD COLUMN plan_expires TEXT")
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE users ADD COLUMN api_key TEXT UNIQUE")
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE users ADD COLUMN nowpayments_subscription_id TEXT")
        except Exception:
            pass
        con.execute("""
            CREATE TABLE IF NOT EXISTS muted_events (
                event_label  TEXT PRIMARY KEY,
                muted_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id   INTEGER PRIMARY KEY REFERENCES users(id),
                keywords  TEXT    DEFAULT '',
                min_pct   REAL    DEFAULT 0.0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                token_id    TEXT    NOT NULL,
                event_label TEXT    DEFAULT '',
                label       TEXT    DEFAULT '',
                added_at    TEXT    DEFAULT (datetime('now')),
                UNIQUE(user_id, token_id)
            )
        """)
        con.commit()


def get_muted_labels() -> list[str]:
    with _conn() as con:
        rows = con.execute("SELECT event_label FROM muted_events ORDER BY muted_at").fetchall()
    return [r[0] for r in rows]


def add_muted_label(event_label: str) -> None:
    with _conn() as con:
        con.execute("INSERT OR IGNORE INTO muted_events (event_label) VALUES (?)", (event_label,))
        con.commit()


def remove_muted_label(event_label: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM muted_events WHERE event_label = ?", (event_label,))
        con.commit()


def count_users() -> int:
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(email: str, password: str, role: str = "viewer") -> bool:
    """Create a new user. Returns False if email already exists."""
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                (email.lower().strip(), generate_password_hash(password), role),
            )
            con.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_user_by_email(email: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT id, email, password_hash, role, analytics_enabled, ai_enabled, plan, plan_expires, stripe_customer_id, api_key FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT id, email, password_hash, role, analytics_enabled, ai_enabled, plan, plan_expires, stripe_customer_id, api_key FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, email, role, created_at, analytics_enabled, ai_enabled, plan, plan_expires FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def update_user_ai(user_id: int, enabled: bool) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE users SET ai_enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        con.commit()


def update_user_analytics(user_id: int, enabled: bool) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE users SET analytics_enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        con.commit()


def update_user_role(user_id: int, role: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        con.commit()


def delete_user(user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
        con.commit()


def update_user_password(user_id: int, new_password: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        con.commit()


def verify_password(user: dict, password: str) -> bool:
    return check_password_hash(user["password_hash"], password)


# ---------------------------------------------------------------------------
# User preferences (keywords filter + min_pct)
# ---------------------------------------------------------------------------

def get_user_preferences(user_id: int) -> dict:
    with _conn() as con:
        row = con.execute(
            "SELECT keywords, min_pct FROM user_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else {"keywords": "", "min_pct": 0.0}


def set_user_preferences(user_id: int, keywords: str, min_pct: float) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO user_preferences (user_id, keywords, min_pct) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET keywords=excluded.keywords, min_pct=excluded.min_pct""",
            (user_id, keywords.strip(), max(0.0, float(min_pct))),
        )
        con.commit()


# ---------------------------------------------------------------------------
# Watchlist (per-user starred markets)
# ---------------------------------------------------------------------------

def get_watchlist(user_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT token_id, event_label, label, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Subscription plan management
# ---------------------------------------------------------------------------

def update_user_plan(user_id: int, plan: str, expires_iso: str | None = None, stripe_customer_id: str | None = None) -> None:
    with _conn() as con:
        if stripe_customer_id:
            con.execute(
                "UPDATE users SET plan=?, plan_expires=?, stripe_customer_id=? WHERE id=?",
                (plan, expires_iso, stripe_customer_id, user_id),
            )
        else:
            con.execute(
                "UPDATE users SET plan=?, plan_expires=? WHERE id=?",
                (plan, expires_iso, user_id),
            )
        con.commit()


def get_user_by_stripe_customer(customer_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT id, email, password_hash, role, analytics_enabled, ai_enabled, plan, plan_expires, stripe_customer_id, api_key FROM users WHERE stripe_customer_id = ?",
            (customer_id,),
        ).fetchone()
    return dict(row) if row else None


def generate_api_key(user_id: int) -> str:
    key = str(uuid.uuid4())
    with _conn() as con:
        con.execute("UPDATE users SET api_key = ? WHERE id = ?", (key, user_id))
        con.commit()
    return key


def get_user_by_api_key(api_key: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT id, email, password_hash, role, analytics_enabled, ai_enabled, plan, plan_expires, stripe_customer_id, api_key FROM users WHERE api_key = ?",
            (api_key,),
        ).fetchone()
    return dict(row) if row else None


def update_nowpayments_subscription(user_id: int, subscription_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE users SET nowpayments_subscription_id=? WHERE id=?",
            (subscription_id, user_id),
        )
        con.commit()


def toggle_watchlist(user_id: int, token_id: str, event_label: str = "", label: str = "") -> dict:
    """Add or remove a token from the user's watchlist. Returns {"added": bool}."""
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM watchlist WHERE user_id = ? AND token_id = ?", (user_id, token_id)
        ).fetchone()
        if row:
            con.execute("DELETE FROM watchlist WHERE user_id = ? AND token_id = ?", (user_id, token_id))
            con.commit()
            return {"added": False, "token_id": token_id}
        con.execute(
            "INSERT INTO watchlist (user_id, token_id, event_label, label) VALUES (?, ?, ?, ?)",
            (user_id, token_id, event_label, label),
        )
        con.commit()
        return {"added": True, "token_id": token_id}
