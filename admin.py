from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Category, InventoryItem, PaymentInvoice, Product, PurchaseOrder, User
from app.security import CredentialCipher, parse_money


@dataclass(slots=True)
class AdminStats:
    users: int
    sales_count: int
    sales_revenue: Decimal
    topups_total: Decimal
    available_stock: int


async def create_category(session_factory: async_sessionmaker[AsyncSession], name: str) -> Category:
    clean_name = " ".join(name.split())
    if not clean_name or len(clean_name) > 120:
        raise ValueError("Category name must be 1-120 characters")
    async with session_factory() as session:
        async with session.begin():
            category = Category(name=clean_name)
            session.add(category)
            await session.flush()
            return category


async def create_product(
    session_factory: async_sessionmaker[AsyncSession],
    category_id: int,
    name: str,
    price: Decimal,
    description: str,
) -> Product:
    clean_name = " ".join(name.split())
    clean_price = parse_money(price)
    if not clean_name or len(clean_name) > 160:
        raise ValueError("Product name must be 1-160 characters")
    if len(description.strip()) > 2000:
        raise ValueError("Product description must not exceed 2000 characters")
    if clean_price <= 0:
        raise ValueError("Product price must be positive")
    async with session_factory() as session:
        async with session.begin():
            if await session.get(Category, category_id) is None:
                raise ValueError("Category does not exist")
            product = Product(
                category_id=category_id,
                name=clean_name,
                price=clean_price,
                description=description.strip(),
            )
            session.add(product)
            await session.flush()
            return product


async def add_inventory(
    session_factory: async_sessionmaker[AsyncSession],
    cipher: CredentialCipher,
    product_id: int,
    credentials: list[tuple[str, str, str | None]],
) -> tuple[int, int]:
    if not credentials:
        raise ValueError("No inventory rows supplied")
    async with session_factory() as session:
        async with session.begin():
            if await session.get(Product, product_id) is None:
                raise ValueError("Product does not exist")
            rows = []
            seen: set[str] = set()
            for email, password, extra in credentials:
                email = email.strip()
                password = password.strip()
                clean_extra = extra.strip() if extra else None
                if (
                    not email
                    or not password
                    or len(email) > 320
                    or len(password) > 512
                    or (clean_extra is not None and len(clean_extra) > 1000)
                ):
                    continue
                fingerprint = cipher.fingerprint(product_id, email)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                rows.append(
                    {
                        "product_id": product_id,
                        "email_encrypted": cipher.encrypt(email),
                        "password_encrypted": cipher.encrypt(password),
                        "extra_encrypted": cipher.encrypt(clean_extra) if clean_extra else None,
                        "credential_fingerprint": fingerprint,
                        "status": "available",
                    }
                )
            if not rows:
                raise ValueError("Every inventory row was invalid")
            statement = (
                pg_insert(InventoryItem)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["product_id", "credential_fingerprint"])
            )
            result = await session.execute(statement)
            inserted = int(result.rowcount or 0)
            return inserted, len(credentials) - inserted


async def update_product_price(
    session_factory: async_sessionmaker[AsyncSession], product_id: int, price: Decimal
) -> Product:
    clean_price = parse_money(price)
    if clean_price <= 0:
        raise ValueError("Price must be positive")
    async with session_factory() as session:
        async with session.begin():
            product = await session.get(Product, product_id, with_for_update=True)
            if product is None:
                raise ValueError("Product does not exist")
            product.price = clean_price
            return product


async def list_catalog_for_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[list[Category], list[tuple[Product, int]]]:
    async with session_factory() as session:
        categories = list((await session.scalars(select(Category).order_by(Category.id))).all())
        stock = func.count(InventoryItem.id).filter(InventoryItem.status == "available")
        products = (
            await session.execute(
                select(Product, stock.label("stock"))
                .outerjoin(InventoryItem, InventoryItem.product_id == Product.id)
                .group_by(Product.id)
                .order_by(Product.id)
            )
        ).all()
        return categories, [(product, int(count)) for product, count in products]


async def get_admin_stats(
    session_factory: async_sessionmaker[AsyncSession],
) -> AdminStats:
    async with session_factory() as session:
        users = int(await session.scalar(select(func.count(User.telegram_id))) or 0)
        sales_count = int(await session.scalar(select(func.count(PurchaseOrder.id))) or 0)
        sales_revenue = await session.scalar(
            select(func.coalesce(func.sum(PurchaseOrder.price), 0))
        )
        topups_total = await session.scalar(
            select(func.coalesce(func.sum(PaymentInvoice.amount), 0)).where(
                PaymentInvoice.status == "paid"
            )
        )
        stock = await session.scalar(
            select(func.count(InventoryItem.id)).where(InventoryItem.status == "available")
        )
        return AdminStats(
            users=users,
            sales_count=sales_count,
            sales_revenue=Decimal(sales_revenue or 0),
            topups_total=Decimal(topups_total or 0),
            available_stock=int(stock or 0),
        )
