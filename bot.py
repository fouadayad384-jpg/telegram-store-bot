from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand

from app.config import Settings
from app.routers.admin import router as admin_router
from app.routers.user import router as user_router


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


def create_dispatcher(settings: Settings) -> Dispatcher:
    storage: BaseStorage
    if settings.redis_url:
        storage = RedisStorage.from_url(settings.redis_url)
    else:
        storage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(user_router)
    return dispatcher


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="بدء البوت"),
            BotCommand(command="menu", description="القائمة الرئيسية"),
            BotCommand(command="cancel", description="إلغاء العملية الحالية"),
            BotCommand(command="admin", description="لوحة الإدارة"),
        ]
    )
