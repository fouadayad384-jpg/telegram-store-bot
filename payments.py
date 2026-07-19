from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.binance_pay import BinancePayClient, BinancePayError
from app.models import OutboxEvent, PaymentInvoice, User, WalletLedger
from app.security import parse_money


class PaymentValidationError(RuntimeError):
    pass


@dataclass(slots=True)
class InvoiceView:
    id: uuid.UUID
    merchant_trade_no: str
    amount: Decimal
    currency: str
    checkout_url: str
    expires_at: datetime


@dataclass(slots=True)
class PaymentResult:
    action: str
    credited: bool = False
    user_id: int | None = None
    amount: Decimal = Decimal("0.00")


async def create_topup_invoice(
    session_factory: async_sessionmaker[AsyncSession],
    binance: BinancePayClient,
    user_id: int,
    request_key: str,
    amount: Decimal,
    min_amount: Decimal,
    max_amount: Decimal,
    webhook_url: str,
) -> InvoiceView:
    amount = parse_money(amount)
    if amount < min_amount or amount > max_amount:
        raise ValueError(f"Top-up amount must be between {min_amount} and {max_amount}")

    merchant_trade_no = f"T{uuid.uuid4().hex[:31]}"
    invoice: PaymentInvoice | None = None
    async with session_factory() as session:
        async with session.begin():
            if await session.get(User, user_id, with_for_update=True) is None:
                raise RuntimeError("User not registered")
            invoice = await session.scalar(
                select(PaymentInvoice).where(PaymentInvoice.request_key == request_key)
            )
            if invoice is None:
                invoice = PaymentInvoice(
                    request_key=request_key,
                    user_id=user_id,
                    merchant_trade_no=merchant_trade_no,
                    amount=amount,
                    currency=binance.currency,
                    status="creating",
                )
                session.add(invoice)
                await session.flush()
                created = True
            else:
                if invoice.user_id != user_id or invoice.amount != amount:
                    raise RuntimeError("Top-up request key was reused with different data")
                created = False

    if invoice is None:  # Defensive; every successful transaction above assigns it.
        raise RuntimeError("Invoice initialization failed")

    if not created:
        for _ in range(20):
            async with session_factory() as session:
                stored = await session.get(PaymentInvoice, invoice.id)
                if (
                    stored
                    and stored.checkout_url
                    and stored.expires_at
                    and stored.status in {"pending", "paid"}
                ):
                    return InvoiceView(
                        stored.id,
                        stored.merchant_trade_no,
                        stored.amount,
                        stored.currency,
                        stored.checkout_url,
                        stored.expires_at,
                    )
                if stored and stored.status in {"failed", "expired"}:
                    raise BinancePayError("This top-up request is no longer payable")
            await asyncio.sleep(0.25)
        raise BinancePayError("This top-up request is still being created")

    try:
        result = await binance.create_topup_order(invoice.merchant_trade_no, amount, webhook_url)
        data = result.get("data")
        if not isinstance(data, dict):
            raise BinancePayError("Binance returned an invalid create-order response")
        checkout_url = str(data.get("checkoutUrl") or data.get("universalUrl") or "")
        prepay_id = str(data.get("prepayId") or "")
        expire_ms = int(data.get("expireTime") or 0)
        if not checkout_url or not prepay_id:
            raise BinancePayError("Binance did not return a checkout URL")
        expires_at = (
            datetime.fromtimestamp(expire_ms / 1000, tz=UTC)
            if expire_ms
            else datetime.now(UTC) + timedelta(minutes=binance.expiry_minutes)
        )
    except Exception:
        async with session_factory() as session:
            async with session.begin():
                stored = await session.get(PaymentInvoice, invoice.id, with_for_update=True)
                if stored and stored.status == "creating":
                    stored.status = "failed"
        raise

    async with session_factory() as session:
        async with session.begin():
            stored = await session.get(PaymentInvoice, invoice.id, with_for_update=True)
            if stored is None:
                raise RuntimeError("Invoice disappeared while it was being created")
            if stored.prepay_id and stored.prepay_id != prepay_id:
                raise RuntimeError("Invoice was linked to a different Binance order")
            stored.prepay_id = prepay_id
            stored.checkout_url = checkout_url
            stored.expires_at = expires_at
            if stored.status != "paid":
                stored.status = "pending"
    return InvoiceView(
        invoice.id,
        invoice.merchant_trade_no,
        amount,
        binance.currency,
        checkout_url,
        expires_at,
    )


def _notification_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise PaymentValidationError("Invalid notification data") from exc
    if not isinstance(data, dict):
        raise PaymentValidationError("Invalid notification data")
    return data


async def process_binance_notification(
    session_factory: async_sessionmaker[AsyncSession],
    binance: BinancePayClient,
    payload: dict[str, Any],
) -> PaymentResult:
    if payload.get("bizType") != "PAY":
        return PaymentResult(action="ignored")

    status = str(payload.get("bizStatus") or "")
    details = _notification_data(payload)
    merchant_trade_no = str(details.get("merchantTradeNo") or "")
    if not merchant_trade_no:
        raise PaymentValidationError("Missing merchantTradeNo")

    if status in {"PAY_CLOSED", "PAY_FAIL"}:
        async with session_factory() as session:
            async with session.begin():
                invoice = await session.scalar(
                    select(PaymentInvoice)
                    .where(PaymentInvoice.merchant_trade_no == merchant_trade_no)
                    .with_for_update()
                )
                if invoice and invoice.status in {"creating", "pending"}:
                    invoice.status = "expired" if status == "PAY_CLOSED" else "failed"
        return PaymentResult(action="closed")

    if status != "PAY_SUCCESS":
        return PaymentResult(action="ignored")

    query = await binance.query_order(merchant_trade_no)
    if query.get("status") != "PAID":
        raise PaymentValidationError("Binance order is not confirmed as PAID")
    if str(query.get("merchantTradeNo")) != merchant_trade_no:
        raise PaymentValidationError("Order reference mismatch")

    paid_amount = parse_money(str(query.get("orderAmount")))
    webhook_amount = parse_money(str(details.get("totalFee")))
    paid_currency = str(query.get("currency") or "").upper()
    webhook_currency = str(details.get("currency") or "").upper()
    prepay_id = str(query.get("prepayId") or payload.get("bizIdStr") or "")
    transaction_id = str(query.get("transactionId") or details.get("transactionId") or "")
    webhook_prepay_id = str(payload.get("bizIdStr") or payload.get("bizId") or "")
    webhook_transaction_id = str(details.get("transactionId") or "")
    if not transaction_id:
        raise PaymentValidationError("Missing Binance transaction ID")
    if webhook_prepay_id and webhook_prepay_id != prepay_id:
        raise PaymentValidationError("Webhook prepay ID does not match queried order")
    if webhook_transaction_id and webhook_transaction_id != transaction_id:
        raise PaymentValidationError("Webhook transaction ID does not match queried order")

    async with session_factory() as session:
        async with session.begin():
            invoice = await session.scalar(
                select(PaymentInvoice)
                .where(PaymentInvoice.merchant_trade_no == merchant_trade_no)
                .with_for_update()
            )
            if invoice is None:
                raise PaymentValidationError("Unknown invoice")
            if paid_amount != invoice.amount or webhook_amount != invoice.amount:
                raise PaymentValidationError("Paid amount does not match invoice")
            if paid_currency != invoice.currency or webhook_currency != invoice.currency:
                raise PaymentValidationError("Paid currency does not match invoice")
            if invoice.prepay_id and prepay_id != invoice.prepay_id:
                raise PaymentValidationError("Prepay ID does not match invoice")
            if invoice.transaction_id and transaction_id != invoice.transaction_id:
                raise PaymentValidationError("Transaction ID does not match paid invoice")
            if invoice.status == "paid":
                return PaymentResult(
                    action="already_paid", user_id=invoice.user_id, amount=invoice.amount
                )

            user = await session.get(User, invoice.user_id, with_for_update=True)
            if user is None:
                raise PaymentValidationError("Invoice user no longer exists")

            user.wallet_balance += invoice.amount
            invoice.status = "paid"
            invoice.prepay_id = invoice.prepay_id or prepay_id
            invoice.transaction_id = transaction_id
            invoice.paid_at = datetime.now(UTC)
            invoice.raw_payment_data = payload
            session.add(
                WalletLedger(
                    user_id=user.telegram_id,
                    entry_type="binance_topup",
                    amount=invoice.amount,
                    balance_after=user.wallet_balance,
                    reference=merchant_trade_no,
                    idempotency_key=f"binance:{transaction_id}",
                )
            )
            session.add_all(
                [
                    OutboxEvent(
                        kind="topup_user",
                        dedupe_key=f"topup-user:{transaction_id}",
                        payload={
                            "invoice_id": str(invoice.id),
                            "user_id": user.telegram_id,
                            "amount": str(invoice.amount),
                            "balance": str(user.wallet_balance),
                        },
                    ),
                    OutboxEvent(
                        kind="feed_topup",
                        dedupe_key=f"feed-topup:{transaction_id}",
                        payload={
                            "invoice_id": str(invoice.id),
                            "user_id": user.telegram_id,
                            "amount": str(invoice.amount),
                        },
                    ),
                ]
            )
            return PaymentResult(
                action="credited", credited=True, user_id=user.telegram_id, amount=invoice.amount
            )


async def get_wallet_balance(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> Decimal:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise RuntimeError("User not registered")
        return user.wallet_balance
