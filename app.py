"""
app.py
Flask backend for the bot-constructor webapp (index.html).

Env vars:
    MAIN_BOT_TOKEN          - token of the Telegram bot that hosts this WebApp
    TELEGRAM_WEBHOOK_SECRET - optional; if set, /webhook checks the header
    CRYPTO_PAY_TOKEN        - see payment.py
    PUBLIC_BASE_URL         - e.g. https://yourdomain.com (no trailing slash)
    ADMIN_ID                - Telegram user ID of the support admin
"""

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from flask import Flask, jsonify, request, send_file, g

import generator
import payment
import support
import gift
import broadcast

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
GENERATED_DIR = BASE_DIR / "generated"
DB_PATH = BASE_DIR / "orders.db"
UPLOADS_DIR.mkdir(exist_ok=True)
GENERATED_DIR.mkdir(exist_ok=True)

MAIN_BOT_TOKEN = os.environ.get("MAIN_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
WEBAPP_URL = PUBLIC_BASE_URL
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
TELEGRAM_API = f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}"

FREE_ALLOWED_ID = 7113397602
STARS_PRICE = 7
CRYPTO_PRICE_RUB = "4"
MAX_UPLOAD_MB = 25

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# -------------------- init --------------------
support.init_support()
gift.init_gift()


# -------------------- DB helpers --------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            tariff TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            config TEXT NOT NULL,
            file_path TEXT,
            crypto_invoice_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_db()


def error(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


# -------------------- Telegram API helpers --------------------
def tg_call(method: str, payload: dict) -> dict:
    if not MAIN_BOT_TOKEN:
        raise RuntimeError("MAIN_BOT_TOKEN is not configured")
    resp = httpx.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"telegram {method} failed"))
    return data["result"]


def tg_send_message(chat_id: int, text: str, reply_markup=None):
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        tg_call("sendMessage", payload)
    except Exception:
        pass


def tg_send_message_raw(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)


def tg_answer_callback(cq_id: str, text: str, show_alert: bool = False):
    try:
        tg_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": text, "show_alert": show_alert})
    except Exception:
        pass


def tg_edit_message_text(chat_id: int, msg_id: int, text: str):
    try:
        tg_call("editMessageText", {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"})
    except Exception:
        pass


def tg_create_stars_invoice_link(order_id: str, title: str, description: str, amount_xtr: int) -> str:
    result = tg_call(
        "createInvoiceLink",
        {
            "title": title[:32] or "Заказ бота",
            "description": description[:255] or "Готовый Telegram-бот",
            "payload": order_id,
            "provider_token": "",
            "currency": "XTR",
            "prices": [{"label": title[:32] or "Бот", "amount": amount_xtr}],
        },
    )
    return result


def tg_send_document(chat_id: int, file_path: Path, caption: str = ""):
    with open(file_path, "rb") as f:
        resp = httpx.post(
            f"{TELEGRAM_API}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (file_path.name, f, "application/zip")},
            timeout=60,
        )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "sendDocument failed"))
    return data["result"]


# -------------------- TgHelper --------------------
class TgHelper:
    def send_message(self, chat_id, text, reply_markup=None):
        tg_send_message(chat_id, text, reply_markup)

    def send_message_raw(self, chat_id, text, reply_markup=None):
        return tg_send_message_raw(chat_id, text, reply_markup)

    def answer_callback(self, cq_id, text, show_alert=False):
        tg_answer_callback(cq_id, text, show_alert)

    def edit_message_text(self, chat_id, msg_id, text):
        tg_edit_message_text(chat_id, msg_id, text)


tg_helper = TgHelper()


# -------------------- Order helpers --------------------
def validate_config(config: dict) -> str | None:
    if not isinstance(config, dict):
        return "config must be an object"
    if not config.get("bot_name"):
        return "bot_name is required"
    if not config.get("bot_token"):
        return "bot_token is required"
    if not config.get("triggers"):
        return "at least one trigger is required"
    return None


def build_and_store_bot(order_id: str, config: dict) -> Path:
    zip_bytes = generator.generate_bot_zip(config, uploads_dir=str(UPLOADS_DIR))
    out_path = GENERATED_DIR / f"{order_id}.zip"
    out_path.write_bytes(zip_bytes)
    return out_path


def finalize_paid_order(db, order_row) -> Path:
    order_id = order_row["id"]
    if order_row["file_path"] and Path(order_row["file_path"]).is_file():
        return Path(order_row["file_path"])
    config = json.loads(order_row["config"])
    out_path = build_and_store_bot(order_id, config)
    db.execute("UPDATE orders SET status = 'paid', file_path = ? WHERE id = ?", (str(out_path), order_id))
    db.commit()
    return out_path


# -------------------- Routes --------------------
@app.route("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.route("/api/upload_media", methods=["POST"])
def upload_media():
    try:
        f = request.files.get("file")
        if not f or not f.filename:
            return error("no file provided")
        ext = os.path.splitext(f.filename)[1][:10]
        name = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOADS_DIR / name
        f.save(dest)
        return jsonify({"ok": True, "url": f"/media/{name}"})
    except Exception as e:
        return error(f"upload failed: {e}", 500)


@app.route("/media/<path:filename>")
def serve_media(filename):
    path = UPLOADS_DIR / filename
    if not path.is_file():
        return error("not found", 404)
    return send_file(path)


@app.route("/api/create_order", methods=["POST"])
def create_order():
    try:
        body = request.get_json(force=True, silent=True) or {}
        config = body.get("config")
        tariff = body.get("tariff", "stars")
        user_id = body.get("user_id")

        problem = validate_config(config)
        if problem:
            return error(problem)

        if tariff not in ("free", "stars", "crypto"):
            return error("unknown tariff")

        order_id = uuid.uuid4().hex
        db = get_db()

        if tariff == "free":
            if user_id != FREE_ALLOWED_ID and not gift.has_free_order(user_id):
                return error("free tariff is available by invitation only", 403)
            db.execute(
                "INSERT INTO orders (id, user_id, tariff, status, config) VALUES (?, ?, 'free', 'pending', ?)",
                (order_id, user_id, json.dumps(config, ensure_ascii=False)),
            )
            db.commit()
            row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            finalize_paid_order(db, row)
            if user_id != FREE_ALLOWED_ID:
                gift.consume_gift(user_id)
            url = f"{PUBLIC_BASE_URL}/download/{order_id}" if PUBLIC_BASE_URL else f"/download/{order_id}"
            return jsonify({"ok": True, "status": "free_download", "url": url})

        if tariff == "stars":
            db.execute(
                "INSERT INTO orders (id, user_id, tariff, status, config) VALUES (?, ?, 'stars', 'pending', ?)",
                (order_id, user_id, json.dumps(config, ensure_ascii=False)),
            )
            db.commit()
            try:
                link = tg_create_stars_invoice_link(
                    order_id,
                    title=config.get("bot_name") or "Ваш бот",
                    description="Готовый Telegram-бот по вашему сценарию",
                    amount_xtr=STARS_PRICE,
                )
            except Exception as e:
                return error(f"could not create stars invoice: {e}", 502)
            return jsonify({"ok": True, "status": "stars_invoice", "invoice_link": link})

        # crypto
        db.execute(
            "INSERT INTO orders (id, user_id, tariff, status, config) VALUES (?, ?, 'crypto', 'pending', ?)",
            (order_id, user_id, json.dumps(config, ensure_ascii=False)),
        )
        db.commit()
        try:
            invoice = asyncio.run(
                payment.create_invoice(
                    amount=CRYPTO_PRICE_RUB,
                    fiat="RUB",
                    description=f"Бот «{config.get('bot_name') or 'без названия'}»",
                    payload=order_id,
                )
            )
        except payment.CryptoPayError as e:
            return error(f"could not create crypto invoice: {e}", 502)

        db.execute("UPDATE orders SET crypto_invoice_id = ? WHERE id = ?", (invoice.get("invoice_id"), order_id))
        db.commit()
        pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
        return jsonify({"ok": True, "status": "crypto_link", "url": pay_url})

    except Exception as e:
        return error(f"unexpected server error: {e}", 500)


@app.route("/download/<order_id>")
def download(order_id):
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return error("order not found", 404)
    if row["status"] != "paid":
        return error("order is not paid yet", 402)
    path = Path(row["file_path"]) if row["file_path"] else None
    if not path or not path.is_file():
        try:
            path = finalize_paid_order(db, row)
        except Exception as e:
            return error(f"could not generate bot: {e}", 500)
    return send_file(path, as_attachment=True, download_name="telegram_bot.zip")


@app.route("/api/order_status/<order_id>")
def order_status(order_id):
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return error("order not found", 404)
    if row["status"] != "paid" and row["tariff"] == "crypto" and row["crypto_invoice_id"]:
        try:
            invoice = asyncio.run(payment.get_invoice(row["crypto_invoice_id"]))
        except payment.CryptoPayError:
            invoice = None
        if invoice and invoice.get("status") == "paid":
            finalize_paid_order(db, row)
            row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return jsonify({"ok": True, "status": row["status"]})


@app.route("/api/crypto_webhook", methods=["POST"])
def crypto_webhook():
    raw = request.get_data()
    signature = request.headers.get("crypto-pay-api-signature", "")
    if not payment.verify_webhook_signature(raw, signature):
        return error("invalid signature", 403)
    update = request.get_json(force=True, silent=True) or {}
    if update.get("update_type") != "invoice_paid":
        return jsonify({"ok": True})
    invoice = update.get("payload", {})
    order_id = invoice.get("payload")
    if not order_id:
        return jsonify({"ok": True})
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return jsonify({"ok": True})
    path = finalize_paid_order(db, row)
    if row["user_id"]:
        try:
            tg_send_document(row["user_id"], path, caption="Оплата получена, ваш бот готов 🎉")
        except Exception:
            pass
    return jsonify({"ok": True})


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    if TELEGRAM_WEBHOOK_SECRET:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header != TELEGRAM_WEBHOOK_SECRET:
            return error("invalid secret token", 403)

    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or {}

    # successful payment (Stars)
    if "successful_payment" in message:
        sp = message["successful_payment"]
        order_id = sp.get("invoice_payload")
        chat_id = message["chat"]["id"]
        db = get_db()
        row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row:
            path = finalize_paid_order(db, row)
            try:
                tg_send_document(chat_id, path, caption="Оплата получена, ваш бот готов 🎉")
            except Exception as e:
                tg_send_message(chat_id, f"Оплата прошла, но не удалось отправить файл: {e}")
        return jsonify({"ok": True})

    # callback_query
    if "callback_query" in update:
        cq = update["callback_query"]
        if support.handle_callback(tg_helper, cq, ADMIN_ID):
            return jsonify({"ok": True})
        return jsonify({"ok": True})

    # message
    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "") or ""
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        username = msg["from"].get("username", "")
        first_name = msg["from"].get("first_name", "Пользователь")

        # /start
        if text.startswith("/start"):
            start_param = text.replace("/start", "").strip()
            if start_param:
                gift.handle_gift_deeplink(tg_helper, user_id, username, first_name, start_param)
            tg_send_message(chat_id,
                "🤖 <b>Конструктор Telegram-ботов</b>\n\n"
                "Собери своего бота без кода — просто нажми кнопку ниже.\n\n"
                "📌 <b>Как пользоваться:</b>\n"
                "1. Открой конструктор\n"
                "2. Придумай название и вставь токен от @BotFather\n"
                "3. Добавь триггеры и действия\n"
                "4. Настрой кнопки\n"
                "5. Выбери тариф и получи готовый ZIP\n\n"
                "💰 <b>Тарифы:</b> Бесплатно (по приглашению) · 7⭐ Stars · 4₽ CryptoBot\n\n"
                "ℹ️ Напиши /help ваш вопрос для связи с поддержкой.",
                reply_markup={
                    "inline_keyboard": [[
                        {"text": "🚀 Открыть конструктор", "web_app": {"url": WEBAPP_URL}}
                    ]]
                } if WEBAPP_URL else None
            )
            return jsonify({"ok": True})

        # /help
        if text.startswith("/help"):
            issue = text.replace("/help", "").strip()
            support.handle_ticket(tg_helper, user_id, username, first_name, issue, ADMIN_ID)
            return jsonify({"ok": True})

        # /gift
        if gift.handle_gift_command(tg_helper, text, user_id, ADMIN_ID, PUBLIC_BASE_URL):
            return jsonify({"ok": True})

        # /all
        if broadcast.handle_broadcast(tg_helper, text, user_id, ADMIN_ID):
            return jsonify({"ok": True})

        # admin reply to ticket
        if support.handle_admin_reply(tg_helper, msg, ADMIN_ID):
            return jsonify({"ok": True})

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=bool(os.environ.get("DEBUG")))