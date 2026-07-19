from __future__ import annotations

import secrets
from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Category, PurchaseOrder
from app.services.store import ProductView


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 المتجر", callback_data="menu:store")
    builder.button(text="💰 محفظتي", callback_data="menu:wallet")
    builder.button(text="👥 الإحالات", callback_data="menu:referral")
    builder.button(text="📦 طلباتي", callback_data="menu:orders")
    builder.adjust(2)
    return builder.as_markup()


def membership_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 الاشتراك بالقناة", url=channel_url)],
            [InlineKeyboardButton(text="✅ تحقق من الاشتراك", callback_data="membership:verify")],
        ]
    )


def categories_keyboard(categories: Sequence[Category]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=f"📁 {category.name}", callback_data=f"category:{category.id}")
    builder.button(text="⬅️ الرئيسية", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def products_keyboard(products: Sequence[ProductView]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product in products:
        stock_icon = "✅" if product.stock else "❌"
        builder.button(
            text=f"{stock_icon} {product.name} — ${product.price:.2f}",
            callback_data=f"product:{product.id}",
        )
    builder.button(text="⬅️ الأقسام", callback_data="menu:store")
    builder.adjust(1)
    return builder.as_markup()


def product_keyboard(product: ProductView) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if product.stock:
        intent_nonce = secrets.token_hex(6)
        builder.button(
            text="⚡ شراء وتسليم فوري",
            callback_data=f"buy:{product.id}:{intent_nonce}",
        )
    builder.button(text="⬅️ رجوع", callback_data=f"category:{product.category_id}")
    builder.adjust(1)
    return builder.as_markup()


def wallet_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amount in (5, 10, 20, 50):
        builder.button(text=f"${amount}", callback_data=f"topup:{amount}")
    builder.button(text="✍️ مبلغ مخصص", callback_data="topup:custom")
    builder.button(text="⬅️ الرئيسية", callback_data="menu:home")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def payment_keyboard(checkout_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟡 الدفع عبر Binance Pay", url=checkout_url)],
            [InlineKeyboardButton(text="⬅️ المحفظة", callback_data="menu:wallet")],
        ]
    )


def orders_keyboard(orders: Sequence[PurchaseOrder]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.button(
            text=f"🔐 {order.product_name} — ${order.price:.2f}",
            callback_data=f"order:{order.id}",
        )
    builder.button(text="⬅️ الرئيسية", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ قسم", callback_data="admin:add_category")
    builder.button(text="➕ منتج", callback_data="admin:add_product")
    builder.button(text="📥 إضافة مخزون", callback_data="admin:add_stock")
    builder.button(text="💲 تعديل سعر", callback_data="admin:set_price")
    builder.button(text="📋 الكتالوج", callback_data="admin:catalog")
    builder.button(text="📊 الإحصائيات", callback_data="admin:stats")
    builder.adjust(2)
    return builder.as_markup()
