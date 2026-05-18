"""baseline_schema — V72 phase 2.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-18 00:00:00 UTC

Mirrors `database._init_db_inner` exactly. This is the FIRST revision of
the project. Running `alembic upgrade head` on an empty database produces
the same schema that `database.init_db()` produces today, plus every
ALTER TABLE upgrade we have accumulated in the legacy module.

Two important properties of this file:

  1. **Idempotent on the live SQLite DB.** Production already has every
     table and index from this revision. The deploy procedure is:

         alembic stamp 0001_baseline   # mark as applied without running

     so this `upgrade()` is *never* executed on the live SQLite. It only
     runs on:
       a) fresh dev databases (and the test suite),
       b) the empty Postgres DB during the cut-over in session 6.

  2. **Cross-engine.** Uses SQLAlchemy types (Integer/Text/Float) that
     map cleanly to both SQLite and Postgres. No `IF NOT EXISTS`, no
     `try/except` — Alembic's transactional DDL handles failures.

`downgrade()` drops every table in reverse dependency order. Since this
is the baseline, downgrading from it leaves an empty database — useful
for tests but never run in production.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Default rows — kept inline so the baseline includes them. These are the
# same INSERT OR IGNORE ... rows that `_init_db_inner` writes.
# ---------------------------------------------------------------------------
_DEFAULT_PAYMENT_METHODS = [
    {
        "id": "usdt",
        "name": "USDT (TRC20)",
        "emoji": "🪙",
        "address": "ضع عنوان USDT هنا",
        "instructions": "حوّل بالدولار إلى العنوان أدناه ثم أرسل إثبات الدفع.",
        "active": 1,
        "currency": "USD",
    },
    {
        "id": "binance",
        "name": "Binance Pay",
        "emoji": "💳",
        "address": "ضع Binance ID هنا",
        "instructions": "حوّل بالدولار عبر Binance Pay ثم أرسل إثبات الدفع.",
        "active": 1,
        "currency": "USD",
    },
    {
        "id": "sham_syr",
        "name": "شام كاش سوري",
        "emoji": "🇸🇾",
        "address": "ضع رقم الحساب هنا",
        "instructions": "حوّل بالليرة السورية ثم أرسل إثبات الدفع.",
        "active": 1,
        "currency": "SYP",
    },
    {
        "id": "sham_usd",
        "name": "شام كاش دولار",
        "emoji": "💵",
        "address": "ضع رقم الحساب هنا",
        "instructions": "حوّل بالدولار ثم أرسل إثبات الدفع.",
        "active": 1,
        "currency": "USD",
    },
    {
        "id": "syriatel",
        "name": "سيرياتيل كاش",
        "emoji": "📱",
        "address": "ضع رقم الهاتف هنا",
        "instructions": "حوّل بالليرة السورية فقط ثم أرسل إثبات الدفع.",
        "active": 1,
        "currency": "SYP",
    },
    {
        "id": "center",
        "name": "ضمن المركز",
        "emoji": "🏢",
        "address": "عنوان المركز",
        "instructions": "الدفع ضمن المركز بالليرة السورية فقط.",
        "active": 1,
        "currency": "SYP",
    },
]

_DEFAULT_SETTINGS = [
    ("support_contact", "@support"),
    ("usd_syp_rate", "15000"),
    ("manual_orders", "0"),
    ("enable_player_check", "0"),
    ("show_server1", "1"),
    ("show_server2", "1"),
    ("email_verification_enabled", "0"),
    ("public_catalog_enabled", "1"),
    ("profit_margin", "1.20"),
    ("site_theme", "theme-aurora"),
    ("local_catalog_seeded", "0"),
]


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------
def upgrade() -> None:
    # --- users -----------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
        sa.Column(
            "balance", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        # email verification (V35+)
        sa.Column(
            "email_verified",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("email_token", sa.Text(), nullable=True),
        sa.Column("email_token_created_at", sa.Integer(), nullable=True),
        # password reset (V36+)
        sa.Column("reset_token", sa.Text(), nullable=True),
        sa.Column("reset_token_created_at", sa.Integer(), nullable=True),
        # pending email change (V40+)
        sa.Column("pending_email", sa.Text(), nullable=True),
        sa.Column("pending_email_token", sa.Text(), nullable=True),
        sa.Column("pending_email_created_at", sa.Integer(), nullable=True),
        # admin 2FA (V51 task B)
        sa.Column("totp_secret", sa.Text(), nullable=True),
        sa.Column(
            "totp_enabled",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("totp_backup_codes", sa.Text(), nullable=True),
        sa.Column("totp_enabled_at", sa.Integer(), nullable=True),
        # google OAuth (V42 batch2)
        sa.Column("google_sub", sa.Text(), nullable=True),
        # session invalidation (V53)
        sa.Column(
            "session_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("idx_users_email", "users", ["email"])
    op.create_index("idx_users_email_token", "users", ["email_token"])
    op.create_index("idx_users_reset_token", "users", ["reset_token"])
    op.create_index("idx_users_google_sub", "users", ["google_sub"])

    # --- games -----------------------------------------------------------
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("game_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=True, server_default="🎮"),
        sa.Column("image_url", sa.Text(), nullable=True, server_default=""),
        sa.Column(
            "active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        # V42+: per-game pricing currency override
        sa.Column(
            "pricing_currency", sa.Text(), nullable=True, server_default="GLOBAL"
        ),
        # V55: admin-controlled homepage visibility
        sa.Column(
            "show_on_home",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "home_sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint(
            "provider", "game_key", name="uq_games_provider_game_key"
        ),
    )
    # Note: legacy ensure_indexes() uses (active, sort_order) but games has no
    # sort_order column; the legacy index silently failed via try/except. The
    # ORM index definition uses (active, name) which is what the listing
    # queries actually need. We mirror the working version.
    op.create_index("idx_games_active", "games", ["active", "name"])

    # --- products --------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("game_key", sa.Text(), nullable=False),
        sa.Column("provider_product_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_price", sa.Float(), nullable=False),
        sa.Column("sell_price", sa.Float(), nullable=False),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("group_id", sa.Integer(), nullable=True),
        # V42+: pricing variants
        sa.Column(
            "fixed_syp_price",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "pricing_mode", sa.Text(), nullable=True, server_default="usd"
        ),
        sa.Column(
            "manual_price_syp",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_product_id",
            name="uq_products_provider_pid",
        ),
    )
    op.create_index(
        "idx_products_game", "products", ["provider", "game_key"]
    )
    op.create_index("idx_products_game_key", "products", ["game_key"])
    op.create_index(
        "idx_products_active_sort", "products", ["active", "sort_order"]
    )

    # --- product_groups --------------------------------------------------
    op.create_table(
        "product_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("game_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True, server_default=""),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "provider", "game_key", "name", name="uq_product_groups_pgn"
        ),
    )

    # --- orders ----------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_code", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("game_key", sa.Text(), nullable=False),
        sa.Column("game_name", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("player_id", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="waiting"
        ),
        sa.Column("provider_order_id", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("order_code", name="uq_orders_order_code"),
    )
    op.create_index("idx_orders_user_id", "orders", ["user_id"])
    op.create_index("idx_orders_status", "orders", ["status"])
    op.create_index("idx_orders_created_at", "orders", ["created_at"])
    op.create_index(
        "idx_orders_user_created",
        "orders",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_orders_status_created",
        "orders",
        ["status", sa.text("created_at DESC")],
    )

    # --- deposits --------------------------------------------------------
    op.create_table(
        "deposits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("deposit_code", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("proof", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending"
        ),
        sa.Column("created_at", sa.Integer(), nullable=False),
        # V42+: multi-currency support
        sa.Column("currency", sa.Text(), nullable=True, server_default="USD"),
        sa.Column(
            "amount_usd", sa.Float(), nullable=True, server_default=sa.text("0")
        ),
        # V53: IDOR fix — store proof filename in DB
        sa.Column("proof_filename", sa.Text(), nullable=True),
        sa.UniqueConstraint("deposit_code", name="uq_deposits_deposit_code"),
    )
    op.create_index("idx_deposits_user_id", "deposits", ["user_id"])
    op.create_index("idx_deposits_status", "deposits", ["status"])
    op.create_index(
        "idx_deposits_user_created",
        "deposits",
        ["user_id", sa.text("created_at DESC")],
    )

    # --- payment_methods -------------------------------------------------
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False, server_default="💳"),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "instructions", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "active", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "currency", sa.Text(), nullable=False, server_default="USD"
        ),
    )

    # --- settings --------------------------------------------------------
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_index("idx_settings_key", "settings", ["key"])

    # --- audit_log -------------------------------------------------------
    # Note: column is named `metadata` in the DB. The ORM aliases it to
    # `meta` because `metadata` is reserved on declarative classes. The
    # storage column name MUST stay `metadata` to match `audit.py` writes.
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.Text(), nullable=True),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_audit_ts", "audit_log", [sa.text("ts DESC")]
    )
    op.create_index(
        "idx_audit_actor",
        "audit_log",
        ["actor_id", sa.text("ts DESC")],
    )
    op.create_index(
        "idx_audit_target", "audit_log", ["target_type", "target_id"]
    )
    op.create_index(
        "idx_audit_action",
        "audit_log",
        ["action", sa.text("ts DESC")],
    )

    # --- wishlist (V42 batch2) ------------------------------------------
    op.create_table(
        "wishlist",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("game_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint(
            "user_id", "provider", "game_key", name="uq_wishlist_user_game"
        ),
    )
    op.create_index(
        "idx_wishlist_user",
        "wishlist",
        ["user_id", sa.text("created_at DESC")],
    )

    # ---------------------------------------------------------------
    # Seed default rows. We use SQL `INSERT ... ON CONFLICT DO NOTHING`
    # via core inserts so the same migration is safe to run on a DB
    # that already has the rows (e.g. when running tests in series).
    # ---------------------------------------------------------------
    payment_methods = sa.table(
        "payment_methods",
        sa.column("id", sa.Text),
        sa.column("name", sa.Text),
        sa.column("emoji", sa.Text),
        sa.column("address", sa.Text),
        sa.column("instructions", sa.Text),
        sa.column("active", sa.Integer),
        sa.column("currency", sa.Text),
    )
    op.bulk_insert(payment_methods, _DEFAULT_PAYMENT_METHODS)

    settings = sa.table(
        "settings",
        sa.column("key", sa.Text),
        sa.column("value", sa.Text),
    )
    op.bulk_insert(
        settings,
        [{"key": k, "value": v} for k, v in _DEFAULT_SETTINGS],
    )


# ---------------------------------------------------------------------------
# downgrade — drop tables in reverse FK-safe order.
# ---------------------------------------------------------------------------
def downgrade() -> None:
    op.drop_index("idx_wishlist_user", table_name="wishlist")
    op.drop_table("wishlist")

    op.drop_index("idx_audit_action", table_name="audit_log")
    op.drop_index("idx_audit_target", table_name="audit_log")
    op.drop_index("idx_audit_actor", table_name="audit_log")
    op.drop_index("idx_audit_ts", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("idx_settings_key", table_name="settings")
    op.drop_table("settings")

    op.drop_table("payment_methods")

    op.drop_index("idx_deposits_user_created", table_name="deposits")
    op.drop_index("idx_deposits_status", table_name="deposits")
    op.drop_index("idx_deposits_user_id", table_name="deposits")
    op.drop_table("deposits")

    op.drop_index("idx_orders_status_created", table_name="orders")
    op.drop_index("idx_orders_user_created", table_name="orders")
    op.drop_index("idx_orders_created_at", table_name="orders")
    op.drop_index("idx_orders_status", table_name="orders")
    op.drop_index("idx_orders_user_id", table_name="orders")
    op.drop_table("orders")

    op.drop_table("product_groups")

    op.drop_index("idx_products_active_sort", table_name="products")
    op.drop_index("idx_products_game_key", table_name="products")
    op.drop_index("idx_products_game", table_name="products")
    op.drop_table("products")

    op.drop_index("idx_games_active", table_name="games")
    op.drop_table("games")

    op.drop_index("idx_users_google_sub", table_name="users")
    op.drop_index("idx_users_reset_token", table_name="users")
    op.drop_index("idx_users_email_token", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
