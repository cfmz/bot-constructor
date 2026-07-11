"""
Генератор готового кода Telegram-бота (aiogram 3) из визуального конфига.

Идея: не генерировать "плоский" питон-код построчно под каждый сценарий
(это ад в поддержке), а сгенерировать ОДИН универсальный движок (engine.py),
который на старте читает config.json и строит хендлеры динамически.
Пользователь получает bot.py + engine.py + config.json + requirements.txt +
.env.example + README.txt — простой, читаемый, работающий проект.

Формат config.json — см. описание в задаче пользователя.
"""
import json

REQUIREMENTS = "aiogram==3.13.1\npython-dotenv==1.0.1\n"

ENV_EXAMPLE = "BOT_TOKEN=вставь_сюда_токен_от_BotFather\n"

BOT_PY = '''"""
Точка входа. Не редактируй engine.py, если не уверен(а) — вся логика
сценария лежит в config.json, его можно менять и без кода.
"""
import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from engine import build_router

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Не найден BOT_TOKEN. Заполни файл .env (см. README.txt)")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
'''

# engine.py — универсальный интерпретатор сценария.
ENGINE_PY = '''"""
Универсальный движок сценария. Читает config.json и строит роутер aiogram.
Правки логики бота обычно достаточно делать в config.json, не здесь.
"""
import json
import asyncio
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

VARIABLES = {k: v for k, v in CONFIG.get("variables", {}).items()}


class WaitInput(StatesGroup):
    waiting = State()


def get_media(media: dict | None):
    """
    Возвращает то, что можно передать в aiogram send_photo/video/... —
    либо путь к локальному файлу из папки media/ (если файл был
    загружен в конструкторе), либо прямую ссылку.
    """
    if not media:
        return None
    if media.get("source") == "upload":
        path = BASE_DIR / "media" / media["filename"]
        return FSInputFile(str(path))
    return media.get("url")


def render(text: str, message: Message | None = None) -> str:
    """Подставляет {{переменные}} и системные плейсхолдеры в текст."""
    if not text:
        return text
    out = text
    if message is not None:
        out = out.replace("{{first_name}}", message.from_user.first_name or "")
        out = out.replace("{{last_name}}", message.from_user.last_name or "")
        out = out.replace("{{username}}", ("@" + message.from_user.username) if message.from_user.username else "")
        out = out.replace("{{user_id}}", str(message.from_user.id))
    for key, val in VARIABLES.items():
        out = out.replace("{{" + key + "}}", str(val) if val is not None else "")
    return out


def build_keyboard(name: str, kind_hint: str | None = None):
    kb = CONFIG.get("keyboards", {}).get(name)
    if not kb:
        return None
    if kb["type"] == "inline":
        rows = []
        for row in kb["buttons"]:
            btn_row = []
            for b in row:
                if "callback" in b:
                    btn_row.append(InlineKeyboardButton(text=b["text"], callback_data=b["callback"]))
                elif "url" in b:
                    btn_row.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
                elif "pay" in b:
                    btn_row.append(InlineKeyboardButton(text=b["text"], pay=True))
            rows.append(btn_row)
        return InlineKeyboardMarkup(inline_keyboard=rows)
    else:  # reply
        rows = []
        for row in kb["buttons"]:
            btn_row = []
            for b in row:
                if b.get("request_contact"):
                    btn_row.append(KeyboardButton(text=b["text"], request_contact=True))
                elif b.get("request_location"):
                    btn_row.append(KeyboardButton(text=b["text"], request_location=True))
                else:
                    btn_row.append(KeyboardButton(text=b["text"]))
            rows.append(btn_row)
        return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def run_actions(actions: list, message: Message, bot, state: FSMContext):
    for action in actions:
        atype = action.get("type")

        if atype == "send_message":
            kb = build_keyboard(action["keyboard"]) if action.get("keyboard") else None
            await message.answer(render(action.get("text", ""), message), reply_markup=kb)

        elif atype == "send_photo":
            kb = build_keyboard(action["keyboard"]) if action.get("keyboard") else None
            caption = render(action.get("text", ""), message) or None
            await message.answer_photo(get_media(action.get("media")), caption=caption, reply_markup=kb)

        elif atype == "send_video":
            kb = build_keyboard(action["keyboard"]) if action.get("keyboard") else None
            caption = render(action.get("text", ""), message) or None
            await message.answer_video(get_media(action.get("media")), caption=caption, reply_markup=kb)

        elif atype == "send_document":
            kb = build_keyboard(action["keyboard"]) if action.get("keyboard") else None
            caption = render(action.get("text", ""), message) or None
            await message.answer_document(get_media(action.get("media")), caption=caption, reply_markup=kb)

        elif atype == "send_voice":
            await message.answer_voice(get_media(action.get("media")))

        elif atype == "send_sticker":
            await message.answer_sticker(get_media(action.get("media")))

        elif atype == "typing_action":
            await bot.send_chat_action(message.chat.id, "typing")
            await asyncio.sleep(1)

        elif atype == "delete_message":
            try:
                await message.delete()
            except Exception:
                pass  # нет прав удалять — просто пропускаем

        elif atype == "request_contact":
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=action.get("button_text", "Отправить контакт"), request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True,
            )
            await message.answer(render(action.get("text", "Поделись контактом:"), message), reply_markup=kb)

        elif atype == "request_location":
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=action.get("button_text", "Отправить геолокацию"), request_location=True)]],
                resize_keyboard=True, one_time_keyboard=True,
            )
            await message.answer(render(action.get("text", "Поделись геолокацией:"), message), reply_markup=kb)

        elif atype == "remove_keyboard":
            await message.answer(render(action.get("text", "Ок"), message), reply_markup=ReplyKeyboardRemove())

        elif atype == "pause":
            await asyncio.sleep(float(action.get("seconds", 1)))

        elif atype == "ask_and_save":
            # Задать вопрос и сохранить следующий ответ пользователя в переменную
            await message.answer(render(action["text"], message))
            await state.set_state(WaitInput.waiting)
            await state.update_data(var_name=action["var"], next_actions=action.get("next_actions", []))

        elif atype == "send_invoice":
            # Оплата через Telegram Stars (валюта XTR, без провайдера)
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=action.get("title", "Товар"),
                description=action.get("description", ""),
                payload=action.get("payload", "invoice"),
                currency="XTR",
                prices=[LabeledPrice(label=action.get("title", "Товар"), amount=int(action["price"]))],
                provider_token="",  # для Stars не нужен
            )


def build_router() -> Router:
    router = Router()

    # /start и /help + произвольные команды
    for handler in CONFIG.get("handlers", []):
        if handler["type"] == "command":
            cmd = handler["command"]
            filt = CommandStart() if cmd == "start" else Command(cmd)

            def make_cmd_handler(actions):
                async def _h(message: Message, state: FSMContext):
                    await run_actions(actions, message, message.bot, state)
                return _h

            router.message.register(make_cmd_handler(handler["actions"]), filt)

        elif handler["type"] == "text_equals":
            text_val = handler["text"]

            def make_eq_handler(actions):
                async def _h(message: Message, state: FSMContext):
                    await run_actions(actions, message, message.bot, state)
                return _h

            router.message.register(make_eq_handler(handler["actions"]), F.text == text_val)

        elif handler["type"] == "text_contains":
            substr = handler["text"].lower()

            def make_contains_handler(actions, substr=substr):
                async def _h(message: Message, state: FSMContext):
                    await run_actions(actions, message, message.bot, state)
                return _h

            router.message.register(
                make_contains_handler(handler["actions"]),
                F.text.func(lambda t, s=substr: t is not None and s in t.lower()),
            )

        elif handler["type"] == "callback":
            data_val = handler["data"]

            def make_cb_handler(actions):
                async def _h(call: CallbackQuery, state: FSMContext):
                    await call.answer()
                    await run_actions(actions, call.message, call.bot, state)
                return _h

            router.callback_query.register(make_cb_handler(handler["actions"]), F.data == data_val)

        elif handler["type"] in (
            "photo_received", "video_received", "voice_received",
            "document_received", "location_received", "contact_received",
        ):
            filt_map = {
                "photo_received": F.photo, "video_received": F.video,
                "voice_received": F.voice, "document_received": F.document,
                "location_received": F.location, "contact_received": F.contact,
            }

            def make_media_handler(actions):
                async def _h(message: Message, state: FSMContext):
                    await run_actions(actions, message, message.bot, state)
                return _h

            router.message.register(make_media_handler(handler["actions"]), filt_map[handler["type"]])

        elif handler["type"] == "new_chat_member":
            # Новый участник в группе (бот должен быть добавлен в группу
            # с отключённым Privacy Mode в @BotFather, чтобы это видеть)
            def make_ncm_handler(actions):
                async def _h(message: Message, state: FSMContext):
                    await run_actions(actions, message, message.bot, state)
                return _h

            router.message.register(make_ncm_handler(handler["actions"]), F.new_chat_members)

        elif handler["type"] == "any_message":
            # Ловит любое сообщение, которое не подошло ни под один
            # триггер выше. Поэтому порядок триггеров важен: если
            # "любое сообщение" стоит не последним, оно перехватит всё
            # раньше остальных.
            def make_any_handler(actions):
                async def _h(message: Message, state: FSMContext):
                    await run_actions(actions, message, message.bot, state)
                return _h

            router.message.register(make_any_handler(handler["actions"]))

    # Обработка ответа, ожидаемого через ask_and_save
    @router.message(WaitInput.waiting)
    async def _save_input(message: Message, state: FSMContext):
        data = await state.get_data()
        var_name = data.get("var_name")
        if var_name:
            VARIABLES[var_name] = message.text
        await state.clear()
        next_actions = data.get("next_actions") or []
        if next_actions:
            await run_actions(next_actions, message, message.bot, state)

    # Успешная оплата Stars
    @router.message(F.successful_payment)
    async def _paid(message: Message):
        await message.answer("Оплата прошла успешно! Спасибо 🎉")

    return router
'''

README_TXT = """ИНСТРУКЦИЯ ПО ЗАПУСКУ БОТА
===========================

Твой бот сгенерирован сервисом-конструктором. Он полностью твой:
код лежит локально, никакой привязки к конструктору нет.

ЧТО ВНУТРИ
----------
bot.py         — точка входа, запускает бота
engine.py      — движок, который читает твой сценарий из config.json
config.json    — сам сценарий (можно менять текст сообщений и кнопки
                 без единой строчки кода)
requirements.txt — список зависимостей
.env.example   — образец файла с токеном

ШАГ 1. Получи токен бота
-------------------------
1. Открой Telegram, найди @BotFather
2. Отправь команду /newbot
3. Придумай имя и username (должен заканчиваться на "bot")
4. BotFather пришлёт токен вида 123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
5. Скопируй его

ШАГ 2. Настрой проект
----------------------
1. Переименуй файл .env.example в .env
2. Открой .env и вставь свой токен после знака "="
   BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

ШАГ 3. Установи Python-зависимости
------------------------------------
Нужен установленный Python 3.10+ (python.org/downloads)

Открой терминал (командную строку) в папке с ботом и выполни:

    pip install -r requirements.txt

ШАГ 4. Запусти бота
---------------------
    python bot.py

Если всё сделано верно, в консоли появится "Start polling" —
бот запущен и отвечает в Telegram.

ЧТО ДЕЛАТЬ ДАЛЬШЕ (бесплатный хостинг 24/7)
---------------------------------------------
Чтобы бот работал даже когда твой компьютер выключен, залей эту
папку на GitHub и подключи к Render.com (Background Worker,
бесплатный тариф) или к любому другому хостингу, поддерживающему
Python. Команда запуска: python bot.py

ЕСЛИ ЧТО-ТО НЕ РАБОТАЕТ
-------------------------
- "BOT_TOKEN not found" — проверь, что файл называется именно .env
  (без .example на конце) и токен вставлен без пробелов
- Бот не отвечает — проверь, что процесс python bot.py запущен
  и в консоли нет ошибок (Traceback)
- Хочешь изменить тексты сообщений — открой config.json, поменяй
  нужные строки в кавычках, сохрани файл, перезапусти бота
"""


def generate_bot_files(config: dict) -> dict:
    """Возвращает {filename: content} для сборки в ZIP."""
    return {
        "bot.py": BOT_PY,
        "engine.py": ENGINE_PY,
        "config.json": json.dumps(config, ensure_ascii=False, indent=2),
        "requirements.txt": REQUIREMENTS,
        ".env.example": ENV_EXAMPLE,
        "README.txt": README_TXT,
    }
