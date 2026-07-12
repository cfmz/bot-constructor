"""
gift.py
One-time gift links for free bot creation.

Admin commands:
    /gift              — create a new gift link
    /gift list         — show all gift links
    /gift delete <id>  — revoke a gift link

Flow:
    1. Admin creates a gift link.
    2. Any user opens the link in Telegram.
    3. System saves that user_id as "has free order".
    4. When the user creates a bot with "free" tariff, the gift is consumed.
    5. After that, the user must pay again.
"""

import sqlite3
import uuid
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gifts.db")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_gift():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gifts (
                id TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                claimed_by INTEGER,
                claimed_at TEXT,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def create_gift(admin_id):
    """Create a new gift code. Returns the code string."""
    gift_id = uuid.uuid4().hex[:8]
    with _db() as conn:
        conn.execute(
            "INSERT INTO gifts (id, created_by, created_at) VALUES (?, ?, ?)",
            (gift_id, admin_id, datetime.utcnow().isoformat())
        )
        conn.commit()
    return gift_id


def claim_gift(gift_id, user_id):
    """
    Called when a user opens the gift link.
    Saves that this user has a free order available.
    Returns True if the gift was valid and unclaimed, False otherwise.
    """
    with _db() as conn:
        row = conn.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,)).fetchone()
        if not row:
            return False

        gift = dict(row)

        # Already claimed by someone else
        if gift["claimed_by"] is not None and gift["claimed_by"] != user_id:
            return False

        # Already used (bot created)
        if gift["used"]:
            return False

        # Claim for this user
        if gift["claimed_by"] is None:
            conn.execute(
                "UPDATE gifts SET claimed_by = ?, claimed_at = ? WHERE id = ?",
                (user_id, datetime.utcnow().isoformat(), gift_id)
            )
            conn.commit()

        return True


def has_free_order(user_id):
    """
    Check if user has an active (claimed but unused) gift.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM gifts WHERE claimed_by = ? AND used = 0 LIMIT 1",
            (user_id,)
        ).fetchone()
        return row is not None


def consume_gift(user_id):
    """
    Mark the user's gift as used. Call after successful free order.
    Returns True if a gift was consumed, False if no active gift found.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM gifts WHERE claimed_by = ? AND used = 0 LIMIT 1",
            (user_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE gifts SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return True


def list_gifts(admin_id):
    """Return all gifts created by this admin."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM gifts WHERE created_by = ? ORDER BY created_at DESC LIMIT 50",
            (admin_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_gift(gift_id, admin_id):
    """Delete a gift (revoke)."""
    with _db() as conn:
        conn.execute(
            "DELETE FROM gifts WHERE id = ? AND created_by = ?",
            (gift_id, admin_id)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Telegram command handler
# ---------------------------------------------------------------------------
def handle_gift_command(tg, text, user_id, admin_id, base_url):
    """
    Handle /gift commands in Telegram.
    Returns True if the message was handled, False otherwise.
    """
    if not text or not text.startswith("/gift"):
        return False

    parts = text.strip().split()

    # /gift — create new
    if len(parts) == 1:
        if str(user_id) != str(admin_id):
            tg.send_message(user_id, "❌ Только администратор может создавать подарочные ссылки.")
            return True

        gift_id = create_gift(admin_id)
        link = f"https://t.me/{(base_url or 'bot')}?start=gift_{gift_id}"

        tg.send_message(admin_id,
            f"🎁 <b>Подарочная ссылка создана</b>\n\n"
            f"Отправьте пользователю эту ссылку:\n"
            f"<code>{link}</code>\n\n"
            f"Когда пользователь перейдёт по ней, он сможет создать <b>одного бота бесплатно</b>.\n"
            f"После использования ссылка сгорит.\n\n"
            f"<i>Код: {gift_id}</i>"
        )
        return True

    # /gift list
    if len(parts) == 2 and parts[1] == "list":
        if str(user_id) != str(admin_id):
            tg.send_message(user_id, "❌ Только администратор.")
            return True

        gifts = list_gifts(admin_id)
        if not gifts:
            tg.send_message(admin_id, "🎁 У вас пока нет подарочных ссылок.")
            return True

        msg = "🎁 <b>Подарочные ссылки:</b>\n\n"
        for g in gifts:
            code = g["id"]
            if g["used"]:
                status = "✅ Использована"
            elif g["claimed_by"]:
                status = f"📌 Ожидает (пользователь {g['claimed_by']})"
            else:
                status = "🆕 Свободна"

            msg += f"<code>{code}</code> — {status}\n"

        msg += "\n<i>Удалить: /gift delete КОД</i>"
        tg.send_message(admin_id, msg)
        return True

    # /gift delete <code>
    if len(parts) == 3 and parts[1] == "delete":
        if str(user_id) != str(admin_id):
            tg.send_message(user_id, "❌ Только администратор.")
            return True

        code = parts[2]
        delete_gift(code, admin_id)
        tg.send_message(admin_id, f"✅ Подарочная ссылка <code>{code}</code> удалена.")
        return True

    return False


# ---------------------------------------------------------------------------
# Web route handler (called from /start deep-link)
# ---------------------------------------------------------------------------
def handle_gift_deeplink(tg, user_id, username, first_name, start_param):
    """
    Check if /start has a gift code and claim it.
    start_param: the text after /start (e.g. "gift_abc123")
    """
    if not start_param or not start_param.startswith("gift_"):
        return False

    gift_id = start_param.replace("gift_", "").strip()
    if not gift_id:
        return False

    if claim_gift(gift_id, user_id):
        tg.send_message(user_id,
            f"🎁 <b>Подарочная ссылка активирована!</b>\n\n"
            f"Теперь вы можете создать <b>одного бота бесплатно</b>.\n"
            f"Откройте конструктор и выберите тариф «Бесплатно»."
        )
    else:
        tg.send_message(user_id,
            "😔 Эта подарочная ссылка уже использована или недействительна."
        )

    return True