"""
db.py — SQLite user management for the PolyBot dashboard.

Users table: id, email (unique), password_hash, role ('admin' | 'viewer'), created_at
"""

import os
import sqlite3

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
        con.execute("""
            CREATE TABLE IF NOT EXISTS muted_events (
                event_label  TEXT PRIMARY KEY,
                muted_at     TEXT DEFAULT (datetime('now'))
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
            "SELECT id, email, password_hash, role FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT id, email, password_hash, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, email, role, created_at, analytics_enabled FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


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
