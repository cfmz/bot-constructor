# Конструктор Telegram-ботов

Без хостинга ботов пользователей — только визуальная сборка сценария и
выдача готового кода (ZIP + PDF-инструкция). Один сервис на Render (free).

## Структура

```
app.py               # FastAPI сервер: API, webhook, выдача файлов
generator.py          # Генератор кода бота (aiogram 3) из config.json
payment.py             # Ручная оплата (5₽) + CryptoBot (4₽)
pdf_instruction.py     # PDF-инструкция для пользователя
db.py                  # SQLite: сценарии и заказы
static/index.html      # Web App — визуальный конструктор
render.yaml             # Конфиг деплоя на Render
requirements.txt
```

## Как это работает

1. Пользователь открывает бота → жмёт кнопку с `web_app` → открывается
   `static/index.html` внутри Telegram.
2. Визуально собирает сценарий: триггеры (/start, текст, кнопки),
   действия (сообщения, фото, запрос контакта/геолокации, пауза,
   счёт на оплату Stars), клавиатуры (inline/reply), переменные `{{var}}`.
3. На вкладке «Оплата» выбирает тариф:
   - **5₽** — ручная оплата. Бот присылает реквизиты и кнопку
     «Я оплатил», ты подтверждаешь команду `/confirm <order_id>` в чате
     с ботом (только твой `ADMIN_ID` может это делать).
   - **4₽** — оплата через `@CryptoBot`. Сервис создаёт инвойс через
     Crypto Pay API, фронт открывает `pay_url` и опрашивает
     `/api/check_order/{id}` каждые 5 сек до статуса `paid`.
4. После подтверждения оплаты сервер:
   - генерирует `bot.py`, `engine.py`, `config.json`, `requirements.txt`,
     `.env.example`, `README.txt` (генератор универсальный: один
     `engine.py` интерпретирует любой сценарий из `config.json`, не
     нужно генерировать уникальный питон-код под каждого пользователя),
   - генерирует PDF-инструкцию (`reportlab`),
   - собирает всё в ZIP и отправляет пользователю файлом через
     `sendDocument`.

## Деплой на Render (бесплатно)

1. Залей эту папку в GitHub-репозиторий.
2. На Render.com → New → Web Service → выбери репозиторий.
3. Render подхватит `render.yaml` автоматически (Blueprint), либо
   настрой вручную:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Задай переменные окружения:
   - `BOT_TOKEN` — токен бота-конструктора (от @BotFather)
   - `ADMIN_ID` — твой Telegram user_id (узнать у @userinfobot)
   - `CRYPTOBOT_TOKEN` — из @CryptoBot → Crypto Pay → Create App
   - `WEBAPP_URL` — публичный URL сервиса, напр. `https://bot-constructor.onrender.com`
   - `DIRECT_PAYMENT_INSTRUCTIONS` — текст с твоими реквизитами для ручной оплаты
5. После первого деплоя сервер сам выставит вебхук на `WEBAPP_URL/webhook`
   (см. `startup`-хук в `app.py`).
6. В @BotFather включи Web App кнопку (Menu Button) на этот же `WEBAPP_URL`,
   либо используй inline-кнопку, которую бот сам присылает на `/start`.

## Важные ограничения free-тарифа Render

- Диск эфемерный: SQLite (`constructor.db`) может обнулиться при
  редеплое или после долгого простоя. Для продакшена — вынести
  `db.py` на Supabase/Postgres (как и в твоём другом проекте).
- Сервис засыпает без трафика — подключи UptimeRobot (пинг раз в
  ~10 минут на `/`), иначе первый запрос после простоя будет долгим.

## Локальный запуск

```bash
pip install -r requirements.txt
export BOT_TOKEN=...
export ADMIN_ID=...
export WEBAPP_URL=https://<ngrok-или-render-url>
uvicorn app:app --reload
```

Для локального теста Web App удобно прокинуть порт через ngrok/cloudflared,
так как Telegram Web App требует https.
