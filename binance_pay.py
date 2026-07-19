from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class BinancePayError(RuntimeError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class BinanceWebhookError(BinancePayError):
    pass


def _compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    raise BinanceWebhookError(f"Missing header: {name}")


def _normalize_public_key(public_key: str) -> bytes:
    clean = public_key.strip().replace("\\n", "\n")
    if "BEGIN PUBLIC KEY" in clean:
        return clean.encode("ascii")
    compact = "".join(clean.split())
    lines = "\n".join(compact[i : i + 64] for i in range(0, len(compact), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{lines}\n-----END PUBLIC KEY-----\n".encode("ascii")


def verify_rsa_webhook_signature(
    raw_body: bytes,
    timestamp: str,
    nonce: str,
    signature_b64: str,
    public_key_pem: str,
) -> None:
    payload = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw_body + b"\n"
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key = serialization.load_pem_public_key(_normalize_public_key(public_key_pem))
        public_key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[attr-defined]
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise BinanceWebhookError("Invalid Binance webhook signature") from exc


class BinancePayClient:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str,
        currency: str,
        expiry_minutes: int,
        webhook_max_skew_seconds: int,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.currency = currency.upper()
        self.expiry_minutes = expiry_minutes
        self.webhook_max_skew_seconds = webhook_max_skew_seconds
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=15.0,
            trust_env=False,
        )
        self._certificates: dict[str, str] = {}

    async def close(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _nonce() -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(32))

    def _headers(self, body: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        nonce = self._nonce()
        payload = f"{timestamp}\n{nonce}\n{body}\n".encode()
        signature = hmac.new(self.secret_key, payload, hashlib.sha512).hexdigest().upper()
        return {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": timestamp,
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self.api_key,
            "BinancePay-Signature": signature,
        }

    async def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = _compact_json(payload)
        try:
            response = await self._http.post(
                path, content=body.encode("utf-8"), headers=self._headers(body)
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BinancePayError("Binance Pay is temporarily unavailable") from exc
        if result.get("status") != "SUCCESS" or result.get("code") != "000000":
            raise BinancePayError(
                result.get("errorMessage") or "Binance Pay rejected the request",
                str(result.get("code") or ""),
            )
        return result

    async def create_topup_order(
        self,
        merchant_trade_no: str,
        amount: Decimal,
        webhook_url: str,
    ) -> dict[str, Any]:
        expires_at = datetime.now(UTC) + timedelta(minutes=self.expiry_minutes)
        payload: dict[str, Any] = {
            "env": {"terminalType": "APP"},
            "merchantTradeNo": merchant_trade_no,
            "orderAmount": float(amount),
            "currency": self.currency,
            "description": "Telegram wallet credit",
            "goodsDetails": [
                {
                    "goodsType": "02",
                    "goodsCategory": "6000",
                    "referenceGoodsId": "WalletCredit",
                    "goodsName": "Telegram wallet credit",
                    "goodsDetail": "Digital store balance",
                }
            ],
            "orderExpireTime": int(expires_at.timestamp() * 1000),
            "supportPayCurrency": self.currency,
        }
        if webhook_url:
            payload["webhookUrl"] = webhook_url
        return await self._post("/binancepay/openapi/v3/order", payload)

    async def query_order(self, merchant_trade_no: str) -> dict[str, Any]:
        result = await self._post(
            "/binancepay/openapi/v2/order/query", {"merchantTradeNo": merchant_trade_no}
        )
        data = result.get("data")
        if not isinstance(data, dict):
            raise BinancePayError("Binance returned an invalid order response")
        return data

    async def refresh_certificates(self) -> None:
        result = await self._post("/binancepay/openapi/certificates", {})
        data: Any = result.get("data", [])
        if isinstance(data, dict):
            data = data.get("certificates", data.get("certificateList", [data]))
        if not isinstance(data, list):
            raise BinancePayError("Binance returned an invalid certificate response")
        certificates: dict[str, str] = {}
        for item in data:
            if isinstance(item, dict) and item.get("certSerial") and item.get("certPublic"):
                certificates[str(item["certSerial"])] = str(item["certPublic"])
        if not certificates:
            raise BinancePayError("No Binance webhook certificate was returned")
        self._certificates = certificates

    async def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> None:
        timestamp = _header(headers, "BinancePay-Timestamp")
        nonce = _header(headers, "BinancePay-Nonce")
        serial = _header(headers, "BinancePay-Certificate-SN")
        signature = _header(headers, "BinancePay-Signature")
        try:
            timestamp_ms = int(timestamp)
        except ValueError as exc:
            raise BinanceWebhookError("Invalid Binance webhook timestamp") from exc
        skew_ms = abs(int(time.time() * 1000) - timestamp_ms)
        if skew_ms > self.webhook_max_skew_seconds * 1000:
            raise BinanceWebhookError("Expired Binance webhook")
        if serial not in self._certificates:
            await self.refresh_certificates()
        public_key = self._certificates.get(serial)
        if public_key is None:
            raise BinanceWebhookError("Unknown Binance webhook certificate")
        verify_rsa_webhook_signature(raw_body, timestamp, nonce, signature, public_key)
