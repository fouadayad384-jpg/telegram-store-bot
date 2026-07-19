from __future__ import annotations

import uuid
from decimal import Decimal
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.binance_pay import BinancePayClient, BinancePayError
from app.config import Settings
from app.keyboards import (
    categories_keyboard,
    main_menu,
    membership_keyboard,
    orders_keyboard,
    payment_keyboard,
    product_keyboard,
    products_keyboard,
    wallet_keyboard,
)
from app.presentation import credential_message
from app.security import CredentialCipher, parse_money
from app.services.payments import create_topup_invoice, get_wallet_balance
from app.services.referrals import get_referral_stats, register_user, verify_channel_membership
from app.services.store import (
    InsufficientBalance,
    MembershipRequired,
    OutOfStock,
    ProductNotFound,
    get_order_credentials,
    get_product,
    list_categories,
    list_products,
    list_user_orders,
    purchase_product,
)

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)


class UserStates(StatesGroup):
    custom_topup = State()


async def _is_channel_member(bot: Bot, settings: Settings, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(settings.required_channel_id, user_id)
    except Exception:
        return False
    if member.status in {
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    }:
        return True
    return member.status == ChatMemberStatus.RESTRICTED and bool(
        getattr(member, "is_member", False)
    )


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    referral_code = None
    if command.args and command.args.startswith("ref_"):
        referral_code = command.args[4:20]
    user, _ = await register_user(
        session_factory,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        referral_code,
    )
    if user.channel_verified_at is None:
        await message.answer(
            "أهلًا بك في المتجر الآلي 👋\n\n"
            "للاستمرار واحتساب الإحالات بصورة صحيحة، اشترك بالقناة ثم اضغط تحقق.",
            reply_markup=membership_keyboard(settings.required_channel_url),
        )
        return
    await message.answer("أهلًا بك مجددًا. اختر الخدمة المطلوبة:", reply_markup=main_menu())


@router.message(Command("menu"))
async def menu_command(message: Message) -> None:
    await message.answer("القائمة الرئيسية:", reply_markup=main_menu())


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("القائمة الرئيسية:", reply_markup=main_menu())


@router.callback_query(F.data == "membership:verify")
async def verify_membership(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if callback.from_user.is_bot:
        await callback.answer("لا يمكن احتساب حسابات البوتات.", show_alert=True)
        return
    if not await _is_channel_member(bot, settings, callback.from_user.id):
        await callback.answer("لم يظهر اشتراكك بعد. اشترك ثم أعد المحاولة.", show_alert=True)
        return
    result = await verify_channel_membership(
        session_factory,
        callback.from_user.id,
        settings.referral_threshold,
        parse_money(settings.referral_reward_usd),
    )
    text = "✅ تم التحقق من اشتراكك. يمكنك استخدام المتجر الآن."
    if result.referral_counted:
        text += "\nوتم احتساب دعوتك لصاحب الرابط مرة واحدة."
    await callback.answer("تم التحقق", show_alert=False)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=main_menu())


@router.callback_query(F.data == "menu:store")
async def show_store(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    categories = await list_categories(session_factory)
    await callback.answer()
    text = "🛍 <b>أقسام المتجر</b>\n\nاختر القسم المطلوب:"
    if not categories:
        text = "لا توجد أقسام متاحة حاليًا."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=categories_keyboard(categories))


@router.callback_query(F.data.startswith("category:"))
async def show_category(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    try:
        category_id = int(callback.data.split(":", 1)[1])  # type: ignore[union-attr]
    except (ValueError, IndexError):
        await callback.answer("قسم غير صالح", show_alert=True)
        return
    products = await list_products(session_factory, category_id)
    await callback.answer()
    text = "اختر المنتج. علامة ✅ تعني أن المخزون متوفر."
    if not products:
        text = "لا توجد منتجات متاحة في هذا القسم."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=products_keyboard(products))


@router.callback_query(F.data.startswith("product:"))
async def show_product(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    try:
        product_id = int(callback.data.split(":", 1)[1])  # type: ignore[union-attr]
    except (ValueError, IndexError):
        await callback.answer("منتج غير صالح", show_alert=True)
        return
    product = await get_product(session_factory, product_id)
    if product is None:
        await callback.answer("المنتج غير متاح", show_alert=True)
        return
    await callback.answer()
    text = (
        f"<b>{escape(product.name)}</b>\n\n"
        f"{escape(product.description) or 'تسليم رقمي تلقائي.'}\n\n"
        f"السعر: <b>${product.price:.2f}</b>\n"
        f"المخزون: <b>{product.stock}</b>"
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=product_keyboard(product))


@router.callback_query(F.data.startswith("buy:"))
async def buy_product(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _is_channel_member(bot, settings, callback.from_user.id):
        await callback.answer("يجب أن يبقى اشتراكك بالقناة فعالًا لإتمام الشراء.", show_alert=True)
        return
    try:
        _, product_text, intent_nonce = callback.data.split(":", 2)  # type: ignore[union-attr]
        product_id = int(product_text)
        if len(intent_nonce) != 12 or not intent_nonce.isalnum():
            raise ValueError
        order_id, balance, created = await purchase_product(
            session_factory,
            callback.from_user.id,
            product_id,
            request_key=f"telegram-buy:{callback.from_user.id}:{intent_nonce}",
        )
    except (ValueError, IndexError):
        await callback.answer("طلب الشراء غير صالح أو قديم.", show_alert=True)
        return
    except InsufficientBalance as exc:
        await callback.answer(
            f"رصيدك ${exc.balance:.2f} والسعر ${exc.price:.2f}. اشحن المحفظة أولًا.",
            show_alert=True,
        )
        return
    except OutOfStock:
        await callback.answer("نفد المخزون للتو. لم يُخصم أي مبلغ.", show_alert=True)
        return
    except MembershipRequired:
        await callback.answer("تحقق من اشتراكك بالقناة أولًا.", show_alert=True)
        return
    except ProductNotFound:
        await callback.answer("المنتج لم يعد متاحًا.", show_alert=True)
        return
    await callback.answer("تم الشراء بنجاح" if created else "سبق تنفيذ هذا الطلب")
    if callback.message:
        if not created:
            await callback.message.answer(
                "ℹ️ سبق تنفيذ ضغطة الشراء هذه، ولم يُخصم أي مبلغ إضافي.\n"
                f"رقم الطلب: <code>{order_id}</code>",
                reply_markup=main_menu(),
            )
            return
        await callback.message.answer(
            "✅ تم خصم المبلغ وحجز نسخة فريدة من المخزون.\n"
            "سيصلك الحساب في رسالة محمية خلال لحظات.\n\n"
            f"رقم الطلب: <code>{order_id}</code>\n"
            f"الرصيد المتبقي: <b>${balance:.2f}</b>",
            reply_markup=main_menu(),
        )


@router.callback_query(F.data == "menu:wallet")
async def show_wallet(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    balance = await get_wallet_balance(session_factory, callback.from_user.id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"💰 <b>محفظتك</b>\n\nالرصيد الحالي: <b>${balance:.2f}</b>\n\nاختر مبلغ الشحن:",
            reply_markup=wallet_keyboard(),
        )


async def _send_invoice(
    message: Message,
    user_id: int,
    request_key: str,
    amount: Decimal,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    binance: BinancePayClient,
) -> None:
    webhook_url = (
        f"{settings.public_base_url}/webhooks/binance-pay" if settings.public_base_url else ""
    )
    try:
        invoice = await create_topup_invoice(
            session_factory,
            binance,
            user_id,
            request_key,
            amount,
            parse_money(settings.min_topup_usd),
            parse_money(settings.max_topup_usd),
            webhook_url,
        )
    except ValueError:
        await message.answer(
            f"المبلغ يجب أن يكون بين ${settings.min_topup_usd} و${settings.max_topup_usd}."
        )
        return
    except BinancePayError:
        await message.answer("تعذر إنشاء فاتورة Binance Pay الآن. حاول مرة أخرى بعد قليل.")
        return
    await message.answer(
        "🟡 <b>فاتورة Binance Pay جاهزة</b>\n\n"
        f"المبلغ: <b>{invoice.amount:.2f} {invoice.currency}</b>\n"
        f"رقم الفاتورة: <code>{invoice.merchant_trade_no}</code>\n\n"
        "بعد الدفع والتأكيد من Binance سيُضاف الرصيد تلقائيًا. لا ترسل دفعة خارج رابط الفاتورة.",
        reply_markup=payment_keyboard(invoice.checkout_url),
    )


@router.callback_query(F.data.regexp(r"^topup:\d+(?:\.\d+)?$"))
async def topup_preset(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    binance: BinancePayClient,
) -> None:
    amount = parse_money(callback.data.split(":", 1)[1])  # type: ignore[union-attr]
    await callback.answer()
    if callback.message:
        await _send_invoice(
            callback.message,
            callback.from_user.id,
            f"telegram-callback:{callback.id}",
            amount,
            settings,
            session_factory,
            binance,
        )


@router.callback_query(F.data == "topup:custom")
async def ask_custom_topup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserStates.custom_topup)
    await callback.answer()
    if callback.message:
        await callback.message.answer("أرسل مبلغ الشحن بالدولار، مثال: <code>15.50</code>")


@router.message(UserStates.custom_topup, ~F.text.startswith("/"))
async def receive_custom_topup(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    binance: BinancePayClient,
) -> None:
    try:
        amount = parse_money(message.text or "")
    except ValueError:
        await message.answer("أرسل رقمًا صحيحًا مثل <code>15.50</code> أو استخدم /cancel.")
        return
    await state.clear()
    if message.from_user:
        await _send_invoice(
            message,
            message.from_user.id,
            f"telegram-message:{message.chat.id}:{message.message_id}",
            amount,
            settings,
            session_factory,
            binance,
        )


@router.callback_query(F.data == "menu:referral")
async def show_referral(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    code, count, blocks = await get_referral_stats(session_factory, callback.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{code}"
    reward = parse_money(settings.referral_reward_usd)
    progress = count % settings.referral_threshold
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "👥 <b>نظام الإحالة</b>\n\n"
            f"رابطك: <code>{escape(link)}</code>\n"
            f"الدعوات الناجحة: <b>{count}</b>\n"
            f"التقدم الحالي: <b>{progress}/{settings.referral_threshold}</b>\n"
            f"المكافآت المستلمة: <b>{blocks}</b> × <b>${reward:.2f}</b>\n\n"
            "تُحتسب الدعوة مرة واحدة فقط بعد اشتراك العضو الجديد بالقناة والتحقق منه.",
            reply_markup=main_menu(),
        )


@router.callback_query(F.data == "menu:orders")
async def show_orders(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    orders = await list_user_orders(session_factory, callback.from_user.id)
    await callback.answer()
    text = "📦 <b>آخر طلباتك</b>\n\nاضغط على أي طلب لاستعادة بياناته:"
    if not orders:
        text = "لا توجد طلبات سابقة."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=orders_keyboard(orders))


@router.callback_query(F.data.startswith("order:"))
async def show_order_credentials(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: CredentialCipher,
) -> None:
    try:
        order_id = uuid.UUID(callback.data.split(":", 1)[1])  # type: ignore[union-attr]
    except (ValueError, IndexError):
        await callback.answer("رقم طلب غير صالح", show_alert=True)
        return
    credentials = await get_order_credentials(
        session_factory, cipher, order_id, callback.from_user.id
    )
    if credentials is None:
        await callback.answer("الطلب غير موجود", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            credential_message(credentials), protect_content=True, reply_markup=main_menu()
        )


@router.message(Command("cancel"))
async def cancel_state(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("تم إلغاء العملية.", reply_markup=main_menu())
