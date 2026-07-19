from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import Bot
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import OutboxEvent, PurchaseOrder, User
from app.presentation import credential_message, public_name
from app.security import CredentialCipher
from app.services.store import get_order_credentials, mark_order_delivered

logger = logging.getLogger(__name__)


class NotificationWorker:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: CredentialCipher,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.session_factory = session_factory
        self.cipher = cipher

    async def run(self) -> None:
        while True:
            try:
                event_ids = await self._claim()
                if not event_ids:
                    await asyncio.sleep(self.settings.notification_poll_seconds)
                    continue
                for event_id in event_ids:
                    await self._handle_one(event_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notification worker iteration failed")
                await asyncio.sleep(self.settings.notification_poll_seconds)

    async def _claim(self, limit: int = 20) -> list[uuid.UUID]:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            async with session.begin():
                events = list(
                    (
                        await session.scalars(
                            select(OutboxEvent)
                            .where(
                                OutboxEvent.sent_at.is_(None),
                                OutboxEvent.available_at <= now,
                                or_(
                                    OutboxEvent.locked_until.is_(None),
                                    OutboxEvent.locked_until < now,
                                ),
                            )
                            .order_by(OutboxEvent.created_at)
                            .with_for_update(skip_locked=True)
                            .limit(limit)
                        )
                    ).all()
                )
                for event in events:
                    event.locked_until = now + timedelta(minutes=5)
                    event.attempts += 1
                return [event.id for event in events]

    async def _handle_one(self, event_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            event = await session.get(OutboxEvent, event_id)
            if event is None or event.sent_at is not None:
                return
            kind = event.kind
            payload = dict(event.payload)
        try:
            await self._send(kind, payload)
        except Exception as exc:
            logger.warning("Outbox event %s failed: %s", event_id, exc)
            await self._mark_failed(event_id, str(exc))
        else:
            await self._mark_sent(event_id)

    async def _send(self, kind: str, payload: dict[str, object]) -> None:
        if kind == "purchase_delivery":
            order_id = uuid.UUID(str(payload["order_id"]))
            credentials = await get_order_credentials(
                self.session_factory, self.cipher, order_id, int(payload["user_id"])
            )
            if credentials is None:
                raise RuntimeError("Order for delivery was not found")
            await self.bot.send_message(
                int(payload["user_id"]), credential_message(credentials), protect_content=True
            )
            await mark_order_delivered(self.session_factory, order_id)
            return

        if kind == "topup_user":
            await self.bot.send_message(
                int(payload["user_id"]),
                "✅ <b>تم شحن محفظتك تلقائيًا</b>\n\n"
                f"المبلغ: <b>${escape(str(payload['amount']))}</b>\n"
                f"الرصيد الحالي: <b>${escape(str(payload['balance']))}</b>",
            )
            return

        if kind == "referral_reward_user":
            await self.bot.send_message(
                int(payload["user_id"]),
                "🎁 <b>مكافأة إحالة جديدة</b>\n\n"
                f"تمت إضافة <b>${escape(str(payload['amount']))}</b> إلى محفظتك تلقائيًا.",
            )
            return

        if kind == "feed_purchase":
            order_id = uuid.UUID(str(payload["order_id"]))
            async with self.session_factory() as session:
                row = (
                    await session.execute(
                        select(PurchaseOrder, User)
                        .join(User, User.telegram_id == PurchaseOrder.user_id)
                        .where(PurchaseOrder.id == order_id)
                    )
                ).one_or_none()
            if row is None:
                raise RuntimeError("Purchase feed order was not found")
            order, user = row
            await self.bot.send_message(
                self.settings.feed_channel_id,
                "🛒 <b>عملية شراء ناجحة</b>\n\n"
                f"العميل: <b>{public_name(user.username, user.full_name, user.telegram_id)}</b>\n"
                f"المنتج: <b>{escape(order.product_name)}</b>\n"
                f"القيمة: <b>${order.price:.2f}</b>\n"
                "⚡ التسليم تلقائي",
            )
            return

        if kind in {"feed_topup", "feed_referral"}:
            user_id = int(payload["user_id"])
            async with self.session_factory() as session:
                user = await session.get(User, user_id)
            if user is None:
                raise RuntimeError("Feed user was not found")
            title = "💳 <b>شحن محفظة ناجح</b>" if kind == "feed_topup" else "🎁 <b>مكافأة إحالة</b>"
            await self.bot.send_message(
                self.settings.feed_channel_id,
                f"{title}\n\n"
                f"المستخدم: <b>{public_name(user.username, user.full_name, user.telegram_id)}</b>\n"
                f"القيمة: <b>${escape(str(payload['amount']))}</b>\n"
                "✅ تمت العملية تلقائيًا",
            )
            return

        raise RuntimeError(f"Unknown outbox event kind: {kind}")

    async def _mark_sent(self, event_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                event = await session.get(OutboxEvent, event_id, with_for_update=True)
                if event:
                    event.sent_at = datetime.now(UTC)
                    event.locked_until = None
                    event.last_error = None

    async def _mark_failed(self, event_id: uuid.UUID, error: str) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                event = await session.get(OutboxEvent, event_id, with_for_update=True)
                if event:
                    delay = min(3600, 2 ** min(event.attempts, 10))
                    event.available_at = datetime.now(UTC) + timedelta(seconds=delay)
                    event.locked_until = None
                    event.last_error = error[:2000]
