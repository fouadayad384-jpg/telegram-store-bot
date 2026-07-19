from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Category,
    InventoryItem,
    OutboxEvent,
    Product,
    PurchaseOrder,
    User,
    WalletLedger,
)
from app.security import CredentialCipher


class StoreError(RuntimeError):
    pass


class ProductNotFound(StoreError):
    pass


class OutOfStock(StoreError):
    pass


class InsufficientBalance(StoreError):
    def __init__(self, balance: Decimal, price: Decimal) -> None:
        super().__init__("Insufficient wallet balance")
        self.balance = balance
        self.price = price


class MembershipRequired(StoreError):
    pass


@dataclass(slots=True)
class ProductView:
    id: int
    category_id: int
    name: str
    description: str
    price: Decimal
    stock: int


@dataclass(slots=True)
class OrderCredentials:
    order_id: uuid.UUID
    product_name: str
    email: str
    password: str
    extra: str | None
    price: Decimal
    created_at: datetime


async def list_categories(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[Category]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(Category).where(Category.is_active.is_(True)).order_by(Category.id)
        )
        return list(rows)


async def list_products(
    session_factory: async_sessionmaker[AsyncSession], category_id: int
) -> list[ProductView]:
    async with session_factory() as session:
        stock_count = func.count(InventoryItem.id).filter(InventoryItem.status == "available")
        result = await session.execute(
            select(Product, stock_count.label("stock"))
            .outerjoin(InventoryItem, InventoryItem.product_id == Product.id)
            .where(Product.category_id == category_id, Product.is_active.is_(True))
            .group_by(Product.id)
            .order_by(Product.id)
        )
        return [
            ProductView(
                id=product.id,
                category_id=product.category_id,
                name=product.name,
                description=product.description,
                price=product.price,
                stock=int(stock),
            )
            for product, stock in result.all()
        ]


async def get_product(
    session_factory: async_sessionmaker[AsyncSession], product_id: int
) -> ProductView | None:
    async with session_factory() as session:
        stock_count = func.count(InventoryItem.id).filter(InventoryItem.status == "available")
        row = (
            await session.execute(
                select(Product, stock_count.label("stock"))
                .outerjoin(InventoryItem, InventoryItem.product_id == Product.id)
                .where(Product.id == product_id, Product.is_active.is_(True))
                .group_by(Product.id)
            )
        ).one_or_none()
        if row is None:
            return None
        product, stock = row
        return ProductView(
            id=product.id,
            category_id=product.category_id,
            name=product.name,
            description=product.description,
            price=product.price,
            stock=int(stock),
        )


async def purchase_product(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    product_id: int,
    request_key: str,
) -> tuple[uuid.UUID, Decimal, bool]:
    async with session_factory() as session:
        async with session.begin():
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise StoreError("User not registered")
            if user.channel_verified_at is None:
                raise MembershipRequired("Channel membership must be verified")

            existing_order = await session.scalar(
                select(PurchaseOrder)
                .where(PurchaseOrder.request_key == request_key)
                .with_for_update()
            )
            if existing_order is not None:
                if existing_order.user_id != user_id:
                    raise StoreError("Purchase request belongs to another user")
                return existing_order.id, user.wallet_balance, False

            product = await session.scalar(
                select(Product)
                .where(Product.id == product_id, Product.is_active.is_(True))
                .with_for_update()
            )
            if product is None:
                raise ProductNotFound("Product not found")

            inventory = await session.scalar(
                select(InventoryItem)
                .where(
                    InventoryItem.product_id == product.id,
                    InventoryItem.status == "available",
                )
                .order_by(InventoryItem.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if inventory is None:
                raise OutOfStock("Product is out of stock")
            if user.wallet_balance < product.price:
                raise InsufficientBalance(user.wallet_balance, product.price)

            now = datetime.now(UTC)
            user.wallet_balance -= product.price
            inventory.status = "sold"
            inventory.sold_to_user_id = user.telegram_id
            inventory.sold_at = now

            order = PurchaseOrder(
                request_key=request_key,
                user_id=user.telegram_id,
                product_id=product.id,
                inventory_item_id=inventory.id,
                product_name=product.name,
                price=product.price,
            )
            session.add(order)
            await session.flush()
            session.add(
                WalletLedger(
                    user_id=user.telegram_id,
                    entry_type="purchase",
                    amount=-product.price,
                    balance_after=user.wallet_balance,
                    reference=str(order.id),
                    idempotency_key=f"purchase:{order.id}",
                )
            )
            session.add_all(
                [
                    OutboxEvent(
                        kind="purchase_delivery",
                        dedupe_key=f"purchase-delivery:{order.id}",
                        payload={"order_id": str(order.id), "user_id": user.telegram_id},
                    ),
                    OutboxEvent(
                        kind="feed_purchase",
                        dedupe_key=f"feed-purchase:{order.id}",
                        payload={"order_id": str(order.id)},
                    ),
                ]
            )
            return order.id, user.wallet_balance, True


async def get_order_credentials(
    session_factory: async_sessionmaker[AsyncSession],
    cipher: CredentialCipher,
    order_id: uuid.UUID,
    user_id: int | None = None,
) -> OrderCredentials | None:
    async with session_factory() as session:
        statement = (
            select(PurchaseOrder, InventoryItem)
            .join(InventoryItem, InventoryItem.id == PurchaseOrder.inventory_item_id)
            .where(PurchaseOrder.id == order_id)
        )
        if user_id is not None:
            statement = statement.where(PurchaseOrder.user_id == user_id)
        row = (await session.execute(statement)).one_or_none()
        if row is None:
            return None
        order, item = row
        return OrderCredentials(
            order_id=order.id,
            product_name=order.product_name,
            email=cipher.decrypt(item.email_encrypted),
            password=cipher.decrypt(item.password_encrypted),
            extra=cipher.decrypt(item.extra_encrypted) if item.extra_encrypted else None,
            price=order.price,
            created_at=order.created_at,
        )


async def mark_order_delivered(
    session_factory: async_sessionmaker[AsyncSession], order_id: uuid.UUID
) -> None:
    async with session_factory() as session:
        async with session.begin():
            order = await session.get(PurchaseOrder, order_id, with_for_update=True)
            if order and order.delivered_at is None:
                order.delivered_at = datetime.now(UTC)


async def list_user_orders(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, limit: int = 10
) -> list[PurchaseOrder]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.user_id == user_id)
            .order_by(PurchaseOrder.created_at.desc())
            .limit(limit)
        )
        return list(rows)
