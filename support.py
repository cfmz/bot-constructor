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

Storage:
    tickets.db (SQLite) — one table 'tickets' with (user_id, status, created_at).
    Admin replies are NOT stored; only the ticket lifecycle is tracked.

Integration with app.py:
    from support import handle_ticket, handle_callback, init_support
    Call init_support() once. Add handle_ticket() call inside /help handler.
    Add handle_callback() call in the callback_query handler.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.db")

# ---- Internal helpers ----

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

def create_ticket(user_id: int, username: str, first_name: str, issue: str) -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (user_id, username, first_name, issue, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
            (user_id, username, first_name, issue, datetime.utcnow().isoformat())
        )
        conn.commit()
        return cur.lastrowid

def get_ticket_by_id(ticket_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return dict(row) if row else None

def get_open_ticket_by_user(user_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None

def close_ticket(ticket_id: int):
    with _db() as conn:
        conn.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
        conn.commit()

def set_admin_message(ticket_id: int, admin_chat_id: int, admin_message_id: int):
    with _db() as conn:
        conn.execute(
            "UPDATE tickets SET admin_chat_id = ?, admin_message_id = ? WHERE id = ?",
            (admin_chat_id, admin_message_id, ticket_id)
        )
        conn.commit()

# ---- Main handlers (called from app.py) ----

def handle_ticket(tg, user_id: int, username: str, first_name: str, issue: str, admin_id: int):
    """
    Creates a ticket and notifies admin.
    `tg` is a helper object with methods:
        tg.send_message(chat_id, text, reply_markup=None)
        tg.send_message_raw(chat_id, text, reply_markup)  — if markup needed
    """
    if not issue or not issue.strip():
        tg.send_message(user_id, "ℹ️ Напиши <b>/help твой вопрос</b> — и я передам его администратору.")
        return

    existing = get_open_ticket_by_user(user_id)
    if existing:
        tg.send_message(user_id, "⏳ У вас уже есть открытое обращение. Дождитесь ответа или закрытия.")
        return

    ticket_id = create_ticket(user_id, username, first_name, issue)

    # Notify user
    tg.send_message(user_id,
        f"✅ <b>Обращение #{ticket_id} создано</b>\n\n"
        f"Я передал ваш вопрос администратору. Ответ придёт сюда же."
    )

    # Notify admin
    if admin_id:
        user_info = f"{first_name or 'Пользователь'}"
        if username:
            user_info += f" (@{username})"
        else:
            user_info += f" (ID: {user_id})"

        text = (
            f"🔔 <b>Новое обращение #{ticket_id}</b>\n\n"
            f"👤 {user_info}\n"
            f"📝 <b>Вопрос:</b> {issue}\n\n"
            f"<i>Ответьте на это сообщение, чтобы написать пользователю.</i>"
        )

        # Inline buttons: [Ответить] [Закрыть]
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✏️ Ответить", "callback_data": f"ticket_reply:{ticket_id}"},
                    {"text": "❌ Закрыть", "callback_data": f"ticket_close:{ticket_id}"}
                ]
            ]
        }

        result = tg.send_message_raw(admin_id, text, reply_markup)
        if result and result.get("result"):
            msg_id = result["result"]["message_id"]
            set_admin_message(ticket_id, admin_id, msg_id)


def handle_callback(tg, callback_query: dict, admin_id: int):
    """
    Handle inline button presses from admin.
    Returns True if the callback was handled, False otherwise.
    """
    data = callback_query.get("data", "")
    cq_id = callback_query.get("id", "")
    from_id = callback_query["from"]["id"]
    message = callback_query.get("message", {})

    # Only admin can interact
    if str(from_id) != str(admin_id):
        return False

    if data.startswith("ticket_reply:"):
        ticket_id = int(data.split(":")[1])
        ticket = get_ticket_by_id(ticket_id)

        if not ticket:
            tg.answer_callback(cq_id, "Обращение не найдено", show_alert=False)
            return True

        if ticket["status"] == "closed":
            tg.answer_callback(cq_id, "Обращение уже закрыто", show_alert=False)
            return True

        # Tell admin to just reply to the message
        tg.answer_callback(cq_id, "Просто ответьте на это сообщение — ваш ответ перешлётся пользователю", show_alert=True)
        return True

    if data.startswith("ticket_close:"):
        ticket_id = int(data.split(":")[1])
        ticket = get_ticket_by_id(ticket_id)

        if not ticket:
            tg.answer_callback(cq_id, "Обращение не найдено", show_alert=False)
            return True

        if ticket["status"] == "closed":
            tg.answer_callback(cq_id, "Уже закрыто", show_alert=False)
            return True

        close_ticket(ticket_id)
        tg.answer_callback(cq_id, "Обращение закрыто", show_alert=False)

        # Notify user
        tg.send_message(ticket["user_id"],
            f"✅ <b>Ваше обращение #{ticket_id} закрыто.</b>\n\n"
            f"Если остались вопросы — создайте новое обращение через /help.\n"
            f"Всего хорошего! 👋"
        )

        # Update admin message
        if message:
            chat_id = message["chat"]["id"]
            msg_id = message["message_id"]
            original_text = message.get("text", "") or message.get("caption", "")
            new_text = original_text + "\n\n❌ <b>Обращение закрыто</b>"
            tg.edit_message_text(chat_id, msg_id, new_text)

        return True

    return False


def handle_admin_reply(tg, message: dict, admin_id: int):
    """
    If admin replies to a ticket notification message, forward the reply to the user.
    Returns True if handled, False otherwise.
    """
    reply_to = message.get("reply_to_message")
    if not reply_to:
        return False

    from_id = message["from"]["id"]
    if str(from_id) != str(admin_id):
        return False

    # Find ticket by admin_message_id
    admin_msg_id = reply_to["message_id"]
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE admin_message_id = ? AND status = 'open'",
            (admin_msg_id,)
        ).fetchone()

    if not row:
        return False

    ticket = dict(row)
    reply_text = message.get("text", "") or message.get("caption", "") or "[без текста]"

    # Send to user
    tg.send_message(ticket["user_id"],
        f"📩 <b>Ответ от администратора:</b>\n\n{reply_text}\n\n"
        f"<i>Если вопрос решён — администратор закроет обращение. Вы также можете создать новое через /help.</i>"
    )

    # Confirm to admin
    tg.send_message(admin_id, f"✅ Ответ отправлен пользователю {ticket['first_name'] or ticket['user_id']}.")

    return True