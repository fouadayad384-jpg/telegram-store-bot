from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def normalize_database_url(value: str) -> str:
    """Use SQLAlchemy's asyncpg driver with provider-style PostgreSQL URLs."""
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str
    bot_mode: str = "webhook"  # webhook | polling
    public_base_url: str = ""
    telegram_webhook_path: str = "/webhooks/telegram"
    telegram_webhook_secret: str = Field(min_length=16)

    database_url: str = "postgresql+asyncpg://store:store@db:5432/store"
    redis_url: str | None = "redis://redis:6379/0"

    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    feed_channel_id: int
    required_channel_id: int
    required_channel_url: str

    binance_api_key: str
    binance_secret_key: str
    binance_base_url: str = "https://bpay.binanceapi.com"
    binance_currency: str = "USDT"
    binance_order_expiry_minutes: int = Field(default=60, gt=0, le=21600)
    binance_webhook_max_skew_seconds: int = Field(default=300, gt=0)

    credential_encryption_key: str
    referral_threshold: int = Field(default=20, gt=0)
    referral_reward_usd: str = "1.00"
    min_topup_usd: str = "1.00"
    max_topup_usd: str = "1000.00"
    notification_poll_seconds: float = Field(default=1.0, gt=0)

    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator("public_base_url")
    @classmethod
    def normalize_public_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def normalize_db_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("telegram_webhook_path")
    @classmethod
    def normalize_webhook_path(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"

    @field_validator("telegram_webhook_secret")
    @classmethod
    def validate_webhook_secret(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", value):
            raise ValueError("Telegram webhook secret contains unsupported characters")
        return value

    @field_validator("binance_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    def validate_runtime(self) -> None:
        if self.bot_mode not in {"webhook", "polling"}:
            raise ValueError("BOT_MODE must be 'webhook' or 'polling'")
        if self.bot_mode == "webhook" and not self.public_base_url.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS in webhook mode")
        if not self.admin_ids:
            raise ValueError("At least one ADMIN_IDS value is required")
        try:
            minimum = Decimal(self.min_topup_usd)
            maximum = Decimal(self.max_topup_usd)
            reward = Decimal(self.referral_reward_usd)
        except InvalidOperation as exc:
            raise ValueError("Money settings must be valid decimal numbers") from exc
        if minimum <= 0 or maximum < minimum or reward < 0:
            raise ValueError("Money settings are outside their allowed range")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
