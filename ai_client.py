"""
ai_client.py — Gemini AI integration for PolyBot.

Provides:
  - generate_alert_summary(): 1-2 sentence Hebrew explanation for Telegram alerts
  - chat_with_markets(): natural language Q&A about live markets
  - admin_command(): parse natural language admin commands into structured actions
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is not None:
        return _model
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        for model_name in ("gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"):
            try:
                _model = genai.GenerativeModel(model_name)
                # Quick validation
                _model.generate_content("hi", generation_config={"max_output_tokens": 1})
                logger.info("Gemini AI initialized with model: %s", model_name)
                break
            except Exception:
                _model = None
                continue
        if _model is None:
            logger.warning("All Gemini models failed to initialize")
            return None
    except Exception as exc:
        logger.warning("Failed to init Gemini: %s", exc)
        return None
    return _model


def _call(prompt: str) -> str | None:
    model = _get_model()
    if model is None:
        return None
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as exc:
        logger.warning("Gemini API call failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Alert summary (Telegram)
# ---------------------------------------------------------------------------

def generate_alert_summary(alert: dict) -> str:
    """Return a 1-2 sentence Hebrew explanation of the price move, or ''."""
    label = alert.get("label", "")
    event_label = alert.get("event_label", label)
    pct = alert.get("pct_change", 0)
    latest = alert.get("latest_price", 0)
    window_sec = alert.get("window_seconds", 300)
    win_label = f"{round(window_sec / 60)} דקות" if window_sec >= 60 else f"{int(window_sec)} שניות"

    prompt = (
        f"אתה אנליסט של Polymarket. כתוב 1-2 משפטים קצרים בעברית שמסבירים מה המשמעות של התנועה הזו:\n"
        f"אירוע: '{event_label}'\n"
        f"תוצאה: '{label}'\n"
        f"המחיר עלה ב-{pct:.1f}% תוך {win_label} ל-{latest * 100:.1f}%.\n"
        f"הסבר מה זה אומר לגבי ההסתברות של האירוע. היה ממוקד ותמציתי."
    )
    result = _call(prompt)
    return result if result else ""


# ---------------------------------------------------------------------------
# Market chat (user-facing)
# ---------------------------------------------------------------------------

def chat_with_markets(user_message: str, market_context: list[dict]) -> str:
    """Answer a natural language question about live markets. Returns Hebrew text."""
    model = _get_model()
    if model is None:
        return "⚠️ AI לא זמין — בדוק GEMINI_API_KEY"

    ctx_lines = []
    for m in market_context[:40]:
        price_pct = round(m.get("current_price", 0) * 100, 1)
        ctx_lines.append(
            f"• {m.get('event_label', '')} | {m.get('label', '')} "
            f"| מחיר: {price_pct}% | התראות: {m.get('alert_count', 0)}"
        )
    context = "\n".join(ctx_lines) if ctx_lines else "אין נתוני שוק זמינים כרגע."

    prompt = (
        f"אתה עוזר AI לפלטפורמת Polymarket — שוק חיזויים. ענה בעברית בצורה ברורה.\n"
        f"הנה השווקים הפעילים כרגע (אלה שיצרו התראות):\n{context}\n\n"
        f"שאלת המשתמש: {user_message}"
    )

    result = _call(prompt)
    return result if result else "שגיאה בשרת ה-AI. נסה שוב."


# ---------------------------------------------------------------------------
# Admin natural language commands
# ---------------------------------------------------------------------------

def parse_admin_command(command: str, users: list[dict]) -> dict:
    """Parse a Hebrew/English admin command into a structured action dict.

    Returns one of:
      {"action": "toggle_analytics", "user_id": int, "value": bool, "explanation": str}
      {"action": "toggle_ai",        "user_id": int, "value": bool, "explanation": str}
      {"action": "toggle_role",      "user_id": int, "value": "admin"|"viewer", "explanation": str}
      {"action": "delete_user",      "user_id": int, "explanation": str}
      {"action": "unknown", "explanation": str}
      {"error": str}
    """
    model = _get_model()
    if model is None:
        return {"error": "AI לא זמין — בדוק GEMINI_API_KEY"}

    users_json = json.dumps(
        [{"id": u["id"], "email": u["email"], "role": u["role"],
          "analytics_enabled": u.get("analytics_enabled", 0),
          "ai_enabled": u.get("ai_enabled", 0)} for u in users],
        ensure_ascii=False
    )

    prompt = (
        f"You are a system that parses admin commands for PolyBot dashboard.\n"
        f"Available users (JSON): {users_json}\n\n"
        f"Admin command: \"{command}\"\n\n"
        f"Return ONLY a JSON object (no markdown, no code blocks) with:\n"
        f"  action: 'toggle_analytics' | 'toggle_ai' | 'toggle_role' | 'delete_user' | 'unknown'\n"
        f"  user_id: matched user's integer id (or null)\n"
        f"  value: for toggle_analytics/toggle_ai: true/false; for toggle_role: 'admin' or 'viewer'; else null\n"
        f"  explanation: short Hebrew sentence describing what you understood\n"
        f"Match users by partial email match or by name in the command."
    )

    raw = _call(prompt)
    if raw is None:
        return {"error": "Gemini לא הגיב"}

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # remove first and last fence lines
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Could not parse admin command JSON: %s", text)
        return {"error": f"לא הצלחתי לפרסר את הפקודה: {text[:200]}"}
