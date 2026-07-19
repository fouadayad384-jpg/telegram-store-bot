from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OutboxEvent, User, WalletLedger


@dataclass(slots=True)
class ReferralVerification:
    newly_verified: bool
    referral_counted: bool
    referrer_id: int | None
    successful_referrals: int
    reward_added: Decimal


async def register_user(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_id: int,
    username: str | None,
    full_name: str,
    referral_code: str | None,
) -> tuple[User, bool]:
    async with session_factory() as session:
        async with session.begin():
            user = await session.get(User, telegram_id, with_for_update=True)
            if user is not None:
                user.username = username
                user.full_name = full_name
                return user, False

            referrer_id: int | None = None
            if referral_code:
                referrer = await session.scalar(
                    select(User).where(User.referral_code == referral_code)
                )
                if referrer is not None and referrer.telegram_id != telegram_id:
                    referrer_id = referrer.telegram_id
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                referral_code=uuid.uuid4().hex[:12],
                referrer_id=referrer_id,
            )
            session.add(user)
            await session.flush()
            return user, True


async def verify_channel_membership(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    threshold: int,
    reward_per_block: Decimal,
) -> ReferralVerification:
    async with session_factory() as session:
        async with session.begin():
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise RuntimeError("User not registered")
            if user.channel_verified_at is not None:
                return ReferralVerification(
                    newly_verified=False,
                    referral_counted=False,
                    referrer_id=user.referrer_id,
                    successful_referrals=0,
                    reward_added=Decimal("0.00"),
                )

            now = datetime.now(UTC)
            user.channel_verified_at = now
            if user.referrer_id is None or user.referral_verified_at is not None:
                return ReferralVerification(True, False, None, 0, Decimal("0.00"))

            referrer = await session.get(User, user.referrer_id, with_for_update=True)
            if referrer is None or referrer.telegram_id == user.telegram_id:
                user.referrer_id = None
                return ReferralVerification(True, False, None, 0, Decimal("0.00"))

            user.referral_verified_at = now
            referrer.successful_referrals += 1
            earned_blocks = referrer.successful_referrals // threshold
            new_blocks = max(0, earned_blocks - referrer.rewarded_referral_blocks)
            reward_total = reward_per_block * new_blocks

            if new_blocks:
                previous_blocks = referrer.rewarded_referral_blocks
                referrer.rewarded_referral_blocks = earned_blocks
                referrer.wallet_balance += reward_total
                for block_number in range(previous_blocks + 1, earned_blocks + 1):
                    session.add(
                        WalletLedger(
                            user_id=referrer.telegram_id,
                            entry_type="referral_reward",
                            amount=reward_per_block,
                            balance_after=referrer.wallet_balance
                            - reward_per_block * (earned_blocks - block_number),
                            reference=f"referral-block-{block_number}",
                            idempotency_key=f"referral:{referrer.telegram_id}:block:{block_number}",
                        )
                    )
                    session.add_all(
                        [
                            OutboxEvent(
                                kind="referral_reward_user",
                                dedupe_key=f"referral-user:{referrer.telegram_id}:{block_number}",
                                payload={
                                    "user_id": referrer.telegram_id,
                                    "amount": str(reward_per_block),
                                    "count": referrer.successful_referrals,
                                },
                            ),
                            OutboxEvent(
                                kind="feed_referral",
                                dedupe_key=f"feed-referral:{referrer.telegram_id}:{block_number}",
                                payload={
                                    "user_id": referrer.telegram_id,
                                    "amount": str(reward_per_block),
                                },
                            ),
                        ]
                    )

            return ReferralVerification(
                newly_verified=True,
                referral_counted=True,
                referrer_id=referrer.telegram_id,
                successful_referrals=referrer.successful_referrals,
                reward_added=reward_total,
            )


async def get_referral_stats(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> tuple[str, int, int]:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise RuntimeError("User not registered")
        return user.referral_code, user.successful_referrals, user.rewarded_referral_blocks
