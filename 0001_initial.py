"""Initial store schema.

Revision ID: 0001_initial
Revises: None
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("wallet_balance", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("referral_code", sa.String(length=16), nullable=False),
        sa.Column("referrer_id", sa.BigInteger(), nullable=True),
        sa.Column("successful_referrals", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rewarded_referral_blocks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("channel_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("referral_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "rewarded_referral_blocks >= 0", name="ck_users_reward_blocks_nonnegative"
        ),
        sa.CheckConstraint("successful_referrals >= 0", name="ck_users_referrals_nonnegative"),
        sa.CheckConstraint("wallet_balance >= 0", name="ck_users_wallet_nonnegative"),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.telegram_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("telegram_id"),
    )
    op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)
    op.create_index("ix_users_referrer_id", "users", ["referrer_id"], unique=False)

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("price > 0", name="ck_products_price_positive"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_product_category_name"),
    )
    op.create_index("ix_products_category_id", "products", ["category_id"], unique=False)

    op.create_table(
        "inventory_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("email_encrypted", sa.Text(), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("extra_encrypted", sa.Text(), nullable=True),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="available", nullable=False),
        sa.Column("sold_to_user_id", sa.BigInteger(), nullable=True),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sold_to_user_id"], ["users.telegram_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "credential_fingerprint", name="uq_stock_fingerprint"),
    )
    op.create_index(
        "ix_inventory_items_product_id", "inventory_items", ["product_id"], unique=False
    )
    op.create_index(
        "ix_inventory_items_sold_to_user_id", "inventory_items", ["sold_to_user_id"], unique=False
    )
    op.create_index(
        "ix_inventory_available", "inventory_items", ["product_id", "status"], unique=False
    )

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.BigInteger(), nullable=False),
        sa.Column("product_name", sa.String(length=160), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inventory_item_id"),
        sa.UniqueConstraint("request_key"),
    )
    op.create_index(
        "ix_purchase_orders_product_id", "purchase_orders", ["product_id"], unique=False
    )
    op.create_index("ix_purchase_orders_user_id", "purchase_orders", ["user_id"], unique=False)

    op.create_table(
        "payment_invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("merchant_trade_no", sa.String(length=32), nullable=False),
        sa.Column("prepay_id", sa.String(length=32), nullable=True),
        sa.Column("transaction_id", sa.String(length=128), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="creating", nullable=False),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("raw_payment_data", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount > 0", name="ck_invoices_amount_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prepay_id"),
        sa.UniqueConstraint("request_key"),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index(
        "ix_payment_invoices_merchant_trade_no",
        "payment_invoices",
        ["merchant_trade_no"],
        unique=True,
    )
    op.create_index("ix_payment_invoices_user_id", "payment_invoices", ["user_id"], unique=False)

    op.create_table(
        "wallet_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("reference", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_wallet_ledger_user_id", "wallet_ledger", ["user_id"], unique=False)

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_outbox_events_kind", "outbox_events", ["kind"], unique=False)
    op.create_index(
        "ix_outbox_events_available_at", "outbox_events", ["available_at"], unique=False
    )
    op.create_index(
        "ix_outbox_events_locked_until", "outbox_events", ["locked_until"], unique=False
    )
    op.create_index("ix_outbox_events_sent_at", "outbox_events", ["sent_at"], unique=False)


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("wallet_ledger")
    op.drop_table("payment_invoices")
    op.drop_table("purchase_orders")
    op.drop_table("inventory_items")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_table("users")
