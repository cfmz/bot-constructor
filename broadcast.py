"""
broadcast.py
Mass message broadcast to all users who started the bot.

Admin command:
    /all <text> — sends <text> to every user who ever pressed /start,
                  including the admin themselves.
"""

import sqlite3
import os
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")


def get_all_users():
    """Return list of unique user_ids from users table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT DISTINCT user_id FROM users").fetchall()
        conn.close()
        return [r["user_id"] for r in rows]
    except Exception:
        return []


def handle_broadcast(tg, text, user_id, admin_id):
    """Handle /all command. Returns True if handled."""
    if not text or not text.startswith("/all"):
        return False

    if str(user_id) != str(admin_id):
        tg.send_message(user_id, "❌ Только администратор может делать рассылки.")
        return True

    message = text.replace("/all", "").strip()
    if not message:
        tg.send_message(admin_id, "ℹ️ Использование: <b>/all текст рассылки</b>")
        return True

    users = get_all_users()

    if admin_id not in users:
        users.append(admin_id)

    if not users:
        tg.send_message(admin_id, "ℹ️ Пока нет пользователей для рассылки.")
        return True

    tg.send_message(admin_id, f"📣 Начинаю рассылку на <b>{len(users)}</b> пользователей...")

    success = 0
    fail = 0

    for uid in users:
        try:
            tg.send_message(uid, f"📣 <b>Сообщение от создателя:</b>\n\n{message}")
            success += 1
            time.sleep(0.05)
        except Exception:
            fail += 1

    tg.send_message(admin_id,
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"✅ Отправлено: {success}\n"
        f"❌ Ошибок: {fail}"
    )

    return True