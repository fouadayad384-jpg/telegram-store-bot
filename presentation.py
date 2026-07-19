from __future__ import annotations

from html import escape

from app.security import mask_display_name
from app.services.store import OrderCredentials


def credential_message(credentials: OrderCredentials) -> str:
    extra = ""
    if credentials.extra:
        extra = f"\n<b>معلومات إضافية:</b> <code>{escape(credentials.extra)}</code>"
    return (
        "✅ <b>تم تسليم طلبك بنجاح</b>\n\n"
        f"<b>المنتج:</b> {escape(credentials.product_name)}\n"
        f"<b>البريد:</b> <code>{escape(credentials.email)}</code>\n"
        f"<b>كلمة المرور:</b> <code>{escape(credentials.password)}</code>"
        f"{extra}\n\n"
        "⚠️ احفظ البيانات الآن، وغيّر كلمة المرور إن كانت سياسة المنتج تسمح بذلك."
    )


def public_name(username: str | None, full_name: str, telegram_id: int) -> str:
    source = username or full_name or str(telegram_id)
    return escape(mask_display_name(source))
