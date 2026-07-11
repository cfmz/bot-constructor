"""
Два тарифа:
  - "direct"    (5р) — ручная оплата: пользователь переводит тебе деньги
                 сам (карта / что укажешь), жмёт "Я оплатил", ты
                 подтверждаешь заказ командой в своём боте /confirm <order_id>
  - "cryptobot" (4р) — автооплата через @CryptoBot API (крипта),
                 подтверждение приходит автоматически по опросу статуса
"""
import os
import httpx

CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_API = "https://pay.crypt.bot/api"

# Реквизиты для ручной оплаты — поменяй на свои
DIRECT_PAYMENT_INSTRUCTIONS = os.getenv(
    "DIRECT_PAYMENT_INSTRUCTIONS",
    "Переведи 5₽ по номеру карты XXXX XXXX XXXX XXXX и нажми «Я оплатил»."
)


async def create_cryptobot_invoice(amount_rub: float, order_id: str) -> dict:
    """
    Создаёт инвойс в CryptoBot на сумму в USDT (грубая конвертация,
    можно захардкодить фиксированную сумму в валюте вместо рублей).
    Возвращает {"pay_url": ..., "invoice_id": ...}
    """
    if not CRYPTOBOT_TOKEN:
        raise RuntimeError("CRYPTOBOT_TOKEN не задан в переменных окружения")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CRYPTOBOT_API}/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN},
            json={
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": str(amount_rub),
                "accepted_assets": "USDT,TON",
                "description": f"Бот-конструктор, заказ {order_id}",
                "payload": order_id,
                "expires_in": 3600,
            },
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"CryptoBot error: {data}")
        result = data["result"]
        return {"pay_url": result["pay_url"], "invoice_id": result["invoice_id"]}


async def check_cryptobot_invoice(invoice_id: str) -> str:
    """Возвращает статус: 'active' | 'paid' | 'expired'"""
    if not CRYPTOBOT_TOKEN:
        raise RuntimeError("CRYPTOBOT_TOKEN не задан")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CRYPTOBOT_API}/getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN},
            params={"invoice_ids": invoice_id},
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"CryptoBot error: {data}")
        items = data["result"]["items"]
        if not items:
            return "unknown"
        return items[0]["status"]
