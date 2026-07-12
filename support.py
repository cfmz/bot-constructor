"""
support.py
In-app support ticket system for the bot-constructor.

Commands (for users):
    /help <text>  — creates a support ticket with the given text.

Admin flow:
    1. Admin receives a message with user's question and inline buttons:
       [Ответить] [Закрыть]
    2. "Ответить" — admin types a reply, it's forwarded to the user.
    3. "Закрыть" — user gets a goodbye message, ticket is closed.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.db")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_support():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                issue TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                admin_chat_id INTEGER,
                admin_message_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def create_ticket(user_id, username, first_name, issue):
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (user_id, username, first_name, issue, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
            (user_id, username, first_name, issue, datetime.utcnow().isoformat())
        )
        conn.commit()
        return cur.lastrowid


def get_ticket(ticket_id):
    with _db() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return dict(row) if row else None


def get_open_ticket(user_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


def close_ticket(ticket_id):
    with _db() as conn:
        conn.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
        conn.commit()


def save_admin_msg_id(ticket_id, chat_id, msg_id):
    with _db() as conn:
        conn.execute(
            "UPDATE tickets SET admin_chat_id = ?, admin_message_id = ? WHERE id = ?",
            (chat_id, msg_id, ticket_id)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def handle_ticket(tg, user_id, username, first_name, issue, admin_id):
    """Create a ticket and notify admin."""
    if not issue or not issue.strip():
        tg.send_message(user_id, "ℹ️ Напиши <b>/help твой вопрос</b> — и я передам его администратору.")
        return

    existing = get_open_ticket(user_id)
    if existing:
        tg.send_message(user_id, "⏳ У вас уже есть открытое обращение. Дождитесь ответа.")
        return

    ticket_id = create_ticket(user_id, username, first_name, issue)

    tg.send_message(user_id,
        f"✅ <b>Обращение #{ticket_id} создано</b>\n\n"
        f"Я передал ваш вопрос администратору. Ответ придёт сюда же."
    )

    if not admin_id:
        return

    user_info = first_name or "Пользователь"
    if username:
        user_info += f" (@{username})"
    else:
        user_info += f" (ID: {user_id})"

    text = (
        f"🔔 <b>Новое обращение #{ticket_id}</b>\n\n"
        f"👤 {user_info}\n"
        f"📝 <b>Вопрос:</b> {issue}\n\n"
        f"<i>Нажми «Ответить» и напиши сообщение — оно перешлётся пользователю.</i>"
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "✏️ Ответить", "callback_data": f"ticket_reply:{ticket_id}"},
                {"text": "❌ Закрыть", "callback_data": f"ticket_close:{ticket_id}"}
            ]
        ]
    }

    try:
        result = tg.send_message_raw(admin_id, text, markup)
        if result and result.get("message_id"):
            save_admin_msg_id(ticket_id, admin_id, result["message_id"])
    except Exception:
        pass


def handle_callback(tg, cq, admin_id):
    """Handle [Ответить] / [Закрыть] buttons."""
    data = cq.get("data", "")
    cq_id = cq.get("id", "")
    from_id = cq["from"]["id"]
    msg = cq.get("message", {})

    if str(from_id) != str(admin_id):
        return False

    if data.startswith("ticket_reply:"):
        ticket_id = int(data.split(":")[1])
        ticket = get_ticket(ticket_id)

        if not ticket:
            tg.answer_callback(cq_id, "Обращение не найдено")
            return True

        if ticket["status"] == "closed":
            tg.answer_callback(cq_id, "Обращение уже закрыто")
            return True

        tg.answer_callback(cq_id, "Ответьте на это сообщение — ваш ответ перешлётся пользователю", show_alert=True)
        return True

    if data.startswith("ticket_close:"):
        ticket_id = int(data.split(":")[1])
        ticket = get_ticket(ticket_id)

        if not ticket:
            tg.answer_callback(cq_id, "Обращение не найдено")
            return True

        if ticket["status"] == "closed":
            tg.answer_callback(cq_id, "Уже закрыто")
            return True

        close_ticket(ticket_id)
        tg.answer_callback(cq_id, "Обращение закрыто")

        tg.send_message(ticket["user_id"],
            f"✅ <b>Ваше обращение #{ticket_id} закрыто.</b>\n\n"
            f"Если остались вопросы — создайте новое через /help.\n"
            f"Всего хорошего! 👋"
        )

        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        if chat_id and msg_id:
            old_text = msg.get("text", "") or msg.get("caption", "") or ""
            tg.edit_message_text(chat_id, msg_id, old_text + "\n\n❌ <b>Обращение закрыто</b>")

        return True

    return False


def handle_admin_reply(tg, message, admin_id):
    """Forward admin's reply to the user."""
    reply_to = message.get("reply_to_message")
    if not reply_to:
        return False

    from_id = message["from"]["id"]
    if str(from_id) != str(admin_id):
        return False

    reply_text = message.get("text") or message.get("caption") or ""

    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE admin_message_id = ? AND status = 'open'",
            (reply_to["message_id"],)
        ).fetchone()

    if not row:
        return False

    ticket = dict(row)

    tg.send_message(ticket["user_id"],
        f"📩 <b>Ответ от администратора:</b>\n\n{reply_text}\n\n"
        f"<i>Если вопрос решён — администратор закроет обращение.</i>"
    )

    tg.send_message(admin_id, f"✅ Ответ отправлен пользователю {ticket['first_name'] or ticket['user_id']}.")

    return True