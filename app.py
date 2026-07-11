"""
Главный сервер конструктора Telegram-ботов.
Работает как один Web Service на Render (бесплатный тариф).

Переменные окружения (задать в Render -> Environment):
  BOT_TOKEN              — токен ЭТОГО бота (бот-конструктор, шлёт файлы,
                            уведомления, обрабатывает /confirm)
  ADMIN_ID               — твой Telegram user_id (для ручных подтверждений)
  CRYPTOBOT_TOKEN         — токен из @CryptoBot -> Crypto Pay -> Create App
  WEBAPP_URL              — публичный URL этого сервиса (https://xxx.onrender.com)
  DIRECT_PAYMENT_INSTRUCTIONS (необяз.) — текст с реквизитами для ручной оплаты

Запуск локально:
  pip install -r requirements.txt
  uvicorn app:app --reload
"""
import io
import os
import re
import uuid
import zipfile
import asyncio
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import payment
from generator import generate_bot_files
from pdf_instruction import build_pdf

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# Куда временно сохраняются файлы, загруженные пользователем в конструкторе
# (фото/видео/документы/голосовые/стикеры), до момента сборки ZIP.
# ВНИМАНИЕ: на Render free tier диск эфемерный — файлы живут, пока сервис
# не перезапустится/не уснёт надолго. Обычно этого достаточно, т.к. сборка
# сценария и оплата происходят в рамках одной сессии.
MEDIA_DIR = Path("uploads_media")
MEDIA_DIR.mkdir(exist_ok=True)

MAX_MEDIA_SIZE = 20 * 1024 * 1024  # 20 МБ — лимит на файл, который принимает Bot API

app = FastAPI(title="Telegram Bot Constructor")
db.init_db()

app.mount("/static", StaticFiles(directory="static"), name="static")


def _safe_filename(name: str) -> str:
    name = os.path.basename(name or "file")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name or "file"


@app.get("/", response_class=HTMLResponse)
async def index():
    return Path("static/index.html").read_text(encoding="utf-8")


# ---------- Telegram Bot API helper (для уведомлений, без aiogram) ----------
import httpx

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


async def tg_send_message(chat_id: int | str, text: str, reply_markup: dict | None = None):
    if not BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/sendMessage", json=payload)


async def tg_send_document(chat_id: int | str, filename: str, content: bytes, caption: str = ""):
    if not BOT_TOKEN:
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TG_API}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (filename, content)},
        )


# --------------------------------- API ---------------------------------

@app.post("/api/upload_media")
async def upload_media(file: UploadFile = File(...)):
    """
    Принимает фото/видео/документ/голосовое/стикер из Web App,
    сохраняет на диск, возвращает media_id — на него дальше ссылается
    сценарий. Реальный файл попадёт в ZIP только на этапе выдачи заказа.
    """
    content = await file.read()
    if len(content) > MAX_MEDIA_SIZE:
        raise HTTPException(413, "Файл слишком большой (максимум 20 МБ)")

    media_id = uuid.uuid4().hex[:12]
    safe_name = _safe_filename(file.filename)
    dest = MEDIA_DIR / f"{media_id}__{safe_name}"
    dest.write_bytes(content)

    return {
        "media_id": media_id,
        "filename": safe_name,
        "preview_url": f"/api/media_preview/{media_id}",
        "content_type": file.content_type,
    }


@app.get("/api/media_preview/{media_id}")
async def media_preview(media_id: str):
    matches = list(MEDIA_DIR.glob(f"{media_id}__*"))
    if not matches:
        raise HTTPException(404, "Файл не найден")
    path = matches[0]
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


class SaveScenarioBody(BaseModel):
    user_id: int
    config: dict


@app.post("/api/save_scenario")
async def save_scenario(body: SaveScenarioBody):
    scenario_id = db.save_scenario(body.user_id, body.config)
    return {"scenario_id": scenario_id}


class CreateOrderBody(BaseModel):
    user_id: int
    scenario_id: str
    tariff: str  # 'direct' | 'cryptobot'


@app.post("/api/create_order")
async def create_order(body: CreateOrderBody):
    scenario = db.get_scenario(body.scenario_id)
    if not scenario:
        raise HTTPException(404, "Сценарий не найден")

    order_id = db.create_order(body.scenario_id, body.user_id, body.tariff)

    if body.tariff == "direct":
        # Ручная оплата: показываем инструкцию с реквизитами
        await tg_send_message(
            body.user_id,
            f"💳 Оплата 5₽\n\n{payment.DIRECT_PAYMENT_INSTRUCTIONS}\n\n"
            f"Номер заказа: <code>{order_id}</code>\n"
            f"После перевода нажми кнопку ниже.",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "Я оплатил ✅", "callback_data": f"paid_direct:{order_id}"}
                ]]
            },
        )
        return {"order_id": order_id, "method": "direct"}

    elif body.tariff == "cryptobot":
        invoice = await payment.create_cryptobot_invoice(4, order_id)
        db.set_order_status(order_id, "pending", payment_ref=str(invoice["invoice_id"]))
        return {"order_id": order_id, "method": "cryptobot", "pay_url": invoice["pay_url"]}

    raise HTTPException(400, "Неизвестный тариф")


@app.get("/api/check_order/{order_id}")
async def check_order(order_id: str):
    """Фронт может поллить этот эндпоинт, чтобы узнать, оплачен ли заказ (для CryptoBot)."""
    order = db.get_order(order_id)
    if not order:
        raise HTTPException(404, "Заказ не найден")

    if order["status"] == "pending" and order["tariff"] == "cryptobot" and order["payment_ref"]:
        status = await payment.check_cryptobot_invoice(order["payment_ref"])
        if status == "paid":
            db.set_order_status(order_id, "paid")
            order["status"] = "paid"
            await _deliver_order(order_id)

    return {"status": order["status"]}


def _embed_uploaded_media(config: dict, zf: zipfile.ZipFile) -> dict:
    """
    Находит все действия, ссылающиеся на загруженный файл (media.source
    == 'upload'), копирует реальный файл в архив под media/, и
    переписывает имя файла в конфиге на то, что реально лежит в ZIP —
    чтобы engine.py нашёл его по относительному пути media/<filename>.
    """
    for handler in config.get("handlers", []):
        for action in handler.get("actions", []):
            media = action.get("media")
            if not media or media.get("source") != "upload":
                continue
            media_id = media.get("media_id", "")
            matches = list(MEDIA_DIR.glob(f"{media_id}__*"))
            if not matches:
                continue  # файл потерялся (например, сервис перезапустился) — пропускаем
            src = matches[0]
            arc_filename = f"{media_id}_{src.name.split('__', 1)[1]}"
            zf.write(src, f"media/{arc_filename}")
            media["filename"] = arc_filename
    return config


async def _build_zip_from_config(config: dict) -> tuple[bytes, str]:
    bot_name = config.get("bot_name", "Мой бот")
    pdf_bytes = build_pdf(bot_name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        config = _embed_uploaded_media(config, zf)  # пишет файлы в media/ и правит config
        files = generate_bot_files(config)           # генерирует код уже с финальным config.json
        for fname, content in files.items():
            zf.writestr(fname, content)
        zf.writestr("Инструкция.pdf", pdf_bytes)
    buf.seek(0)
    safe_name = "".join(c for c in bot_name if c.isalnum() or c in " _-").strip() or "bot"
    return buf.read(), f"{safe_name}.zip"


async def _build_zip(order_id: str) -> tuple[bytes, str]:
    order = db.get_order(order_id)
    scenario = db.get_scenario(order["scenario_id"])
    return await _build_zip_from_config(scenario["config"])


FREE_USER_ID = int(os.getenv("FREE_USER_ID", "0"))


@app.get("/api/download_free/{scenario_id}")
async def download_free(scenario_id: str):
    """Мгновенная скачка без оплаты — только для тестового ID (FREE_USER_ID в env)."""
    scenario = db.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(404, "Сценарий не найден")
    if not FREE_USER_ID or scenario["user_id"] != FREE_USER_ID:
        raise HTTPException(403, "Бесплатный тариф недоступен для этого пользователя")

    zip_bytes, filename = await _build_zip_from_config(scenario["config"])
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _deliver_order(order_id: str):
    order = db.get_order(order_id)
    zip_bytes, filename = await _build_zip(order_id)
    await tg_send_document(
        order["user_id"], filename, zip_bytes,
        caption="Готово! 🎉 Держи архив с кодом бота и инструкцией.",
    )
    db.set_order_status(order_id, "delivered")


@app.get("/api/download/{order_id}")
async def download(order_id: str):
    """Прямая скачка (например, если пользователь открывает Web App в браузере)."""
    order = db.get_order(order_id)
    if not order:
        raise HTTPException(404, "Заказ не найден")
    if order["status"] not in ("paid", "delivered"):
        raise HTTPException(402, "Заказ ещё не оплачен")

    zip_bytes, filename = await _build_zip(order_id)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------- Telegram webhook ---------------------------
# Обрабатывает: /start, /confirm <order_id> (только ADMIN_ID),
# callback "paid_direct:<order_id>" от пользователя

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()

    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        user_id = cq["from"]["id"]
        if data.startswith("paid_direct:"):
            order_id = data.split(":", 1)[1]
            order = db.get_order(order_id)
            if order:
                await tg_send_message(
                    user_id, "Спасибо! Проверяю оплату, обычно это занимает пару минут ⏳"
                )
                if ADMIN_ID:
                    await tg_send_message(
                        ADMIN_ID,
                        f"🔔 Новая ручная оплата\nЗаказ: <code>{order_id}</code>\n"
                        f"От: {user_id}\n\nПодтвердить: /confirm {order_id}",
                    )
        return JSONResponse({"ok": True})

    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "")
        user_id = msg["from"]["id"]

        if text.startswith("/confirm") and str(user_id) == str(ADMIN_ID):
            parts = text.split()
            if len(parts) == 2:
                order_id = parts[1]
                order = db.get_order(order_id)
                if not order:
                    await tg_send_message(user_id, "Заказ не найден")
                else:
                    db.set_order_status(order_id, "paid")
                    await _deliver_order(order_id)
                    await tg_send_message(user_id, f"Заказ {order_id} подтверждён и доставлен ✅")
            return JSONResponse({"ok": True})

        if text.startswith("/start"):
            await tg_send_message(
                user_id,
                "Привет! Это конструктор Telegram-ботов.\n"
                "Открой Web App, чтобы визуально собрать своего бота.",
                reply_markup={
                    "inline_keyboard": [[
                        {"text": "🚀 Открыть конструктор", "web_app": {"url": WEBAPP_URL}}
                    ]]
                } if WEBAPP_URL else None,
            )

    return JSONResponse({"ok": True})


@app.on_event("startup")
async def set_webhook_on_startup():
    if BOT_TOKEN and WEBAPP_URL:
        async with httpx.AsyncClient() as client:
            await client.post(f"{TG_API}/setWebhook", json={"url": f"{WEBAPP_URL}/webhook"})
