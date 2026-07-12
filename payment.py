"""
payment.py
Async integration with CryptoBot's Crypto Pay API (https://help.crypt.bot/crypto-pay-api).

Env vars used:
    CRYPTO_PAY_TOKEN  - API token issued by @CryptoBot -> Crypto Pay -> Create App
    CRYPTO_PAY_BASE   - defaults to production; set to
                        https://testnet-pay.crypt.bot/api for testnet
"""

import os
import hmac
import hashlib
from typing import Optional

import httpx

CRYPTO_PAY_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN", "")
CRYPTO_PAY_BASE = os.environ.get("CRYPTO_PAY_BASE", "https://pay.crypt.bot/api")


class CryptoPayError(Exception):
    """Raised whenever the Crypto Pay API returns ok: false, or the call itself fails."""


async def _request(method: str, params: Optional[dict] = None) -> dict:
    if not CRYPTO_PAY_TOKEN:
        raise CryptoPayError("CRYPTO_PAY_TOKEN is not configured on the server")

    url = f"{CRYPTO_PAY_BASE.rstrip('/')}/{method}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=params or {})
    except httpx.HTTPError as e:
        raise CryptoPayError(f"network error calling {method}: {e}") from e

    try:
        data = resp.json()
    except ValueError as e:
        raise CryptoPayError(f"non-JSON response from {method}: {resp.text[:200]}") from e

    if not data.get("ok"):
        err = data.get("error", {})
        raise CryptoPayError(err.get("name") or err.get("code") or "crypto_pay_api_error")

    return data["result"]


async def create_invoice(
    amount: str,
    fiat: str = "RUB",
    description: str = "",
    payload: str = "",
    expires_in: int = 3600,
) -> dict:
    """
    Creates an invoice priced in fiat currency; the payer settles in the crypto
    asset of their choice at the current rate (currency_type="fiat").

    `payload` should be your internal order id — it comes back unchanged on
    the invoice_paid webhook and on getInvoices, so you can correlate it.

    Returns the raw invoice object from the API. The fields you'll typically
    want are:
        result["invoice_id"]
        result["bot_invoice_url"]   -> open this inside Telegram (WebApp.openLink / openInvoice-like flow)
        result["pay_url"]           -> generic web payment page
    """
    params = {
        "currency_type": "fiat",
        "fiat": fiat,
        "amount": str(amount),
        "description": (description or "")[:1024] or None,
        "payload": (payload or "")[:4096] or None,
        "expires_in": expires_in,
    }
    params = {k: v for k, v in params.items() if v is not None}
    return await _request("createInvoice", params)


async def get_invoice(invoice_id: int) -> Optional[dict]:
    """Fetches a single invoice by id. Returns None if not found."""
    result = await _request("getInvoices", {"invoice_ids": str(invoice_id)})
    items = result.get("items", [])
    return items[0] if items else None


def verify_webhook_signature(body: bytes, signature_header: str) -> bool:
    """
    Verifies the `crypto-pay-api-signature` header CryptoBot sends with
    webhook POST requests.

    Per the Crypto Pay API docs, the signing key is
        sha256(CRYPTO_PAY_TOKEN)
    (raw digest bytes, not hex), and the signature is
        HMAC-SHA256(key=that digest, msg=raw request body).hexdigest()
    """
    if not CRYPTO_PAY_TOKEN or not signature_header:
        return False
    secret = hashlib.sha256(CRYPTO_PAY_TOKEN.encode("utf-8")).digest()
    computed = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature_header)
