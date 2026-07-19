from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.binance_pay import BinancePayClient, BinancePayError, BinanceWebhookError
from app.bot import create_bot, create_dispatcher, set_bot_commands
from app.config import get_settings
from app.db import create_engine_and_session
from app.security import CredentialCipher
from app.services.outbox import NotificationWorker
from app.services.payments import PaymentValidationError, process_binance_notification

logger = logging.getLogger(__name__)


def create_application() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.validate_runtime()

    engine, session_factory = create_engine_and_session(settings.database_url)
    bot = create_bot(settings)
    dispatcher = create_dispatcher(settings)
    cipher = CredentialCipher(settings.credential_encryption_key)
    binance = BinancePayClient(
        api_key=settings.binance_api_key,
        secret_key=settings.binance_secret_key,
        base_url=settings.binance_base_url,
        currency=settings.binance_currency,
        expiry_minutes=settings.binance_order_expiry_minutes,
        webhook_max_skew_seconds=settings.binance_webhook_max_skew_seconds,
    )
    worker = NotificationWorker(bot, settings, session_factory, cipher)

    dependencies = {
        "settings": settings,
        "session_factory": session_factory,
        "binance": binance,
        "cipher": cipher,
    }

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks: list[asyncio.Task[object]] = []
        await set_bot_commands(bot)
        tasks.append(asyncio.create_task(worker.run(), name="notification-worker"))
        if settings.bot_mode == "webhook":
            await bot.set_webhook(
                url=f"{settings.public_base_url}{settings.telegram_webhook_path}",
                secret_token=settings.telegram_webhook_secret,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            tasks.append(
                asyncio.create_task(
                    dispatcher.start_polling(
                        bot,
                        handle_signals=False,
                        allowed_updates=dispatcher.resolve_used_update_types(),
                        **dependencies,
                    ),
                    name="telegram-polling",
                )
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await dispatcher.storage.close()
            await binance.close()
            await bot.session.close()
            await engine.dispose()

    app = FastAPI(title="Telegram Digital Accounts Store", version="1.0.0", lifespan=lifespan)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    async def telegram_webhook(request: Request) -> dict[str, bool]:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied, settings.telegram_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
        try:
            update = Update.model_validate(await request.json(), context={"bot": bot})
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
        await dispatcher.feed_update(bot, update, **dependencies)
        return {"ok": True}

    app.add_api_route(
        settings.telegram_webhook_path,
        telegram_webhook,
        methods=["POST"],
        include_in_schema=False,
        name="telegram-webhook",
    )

    @app.post("/webhooks/binance-pay", include_in_schema=False)
    async def binance_webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()
        try:
            await binance.verify_webhook(raw_body, request.headers)
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise PaymentValidationError("Webhook body must be a JSON object")
            await process_binance_notification(session_factory, binance, payload)
        except BinanceWebhookError as exc:
            logger.warning("Rejected Binance webhook: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid Binance signature") from exc
        except (PaymentValidationError, BinancePayError, json.JSONDecodeError) as exc:
            logger.warning("Binance webhook was not processed: %s", exc)
            return JSONResponse(
                status_code=200,
                content={"returnCode": "FAIL", "returnMessage": str(exc)[:120]},
            )
        except Exception:
            logger.exception("Unexpected Binance webhook failure")
            return JSONResponse(
                status_code=200,
                content={"returnCode": "FAIL", "returnMessage": "Temporary processing error"},
            )
        return JSONResponse(
            status_code=200,
            content={"returnCode": "SUCCESS", "returnMessage": None},
        )

    app.state.settings = settings
    app.state.bot = bot
    app.state.dispatcher = dispatcher
    app.state.session_factory = session_factory
    return app


app = create_application()
