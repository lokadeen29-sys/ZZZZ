"""ORM models — exact mirror of the SQLite schema in `database.py`.

Critical rule: the column types and names match `database._init_db_inner`
EXACTLY. If `users.created_at` is `INTEGER` (unix epoch) in SQLite, it is
`Integer` here too. We do NOT translate to `DateTime` because that would
desync the ORM from the live data and force a one-shot rewrite later.

How to verify these models match the live DB:

    python tools/verify_orm_models.py

That script queries SQLite via raw sqlite3, then via SQLAlchemy, and
compares row counts + a sample row from each table.

Tables (11 total, matching `database._init_db_inner`):
  - users
  - games
  - products
  - product_groups
  - orders
  - deposits
  - payment_methods
  - settings
  - audit_log
  - wishlist

`schema_migrations` (alembic_version) is NOT defined here — Alembic creates
that automatically in phase 2.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text as sa_text,
)

from app.db.base import Base


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class User(Base):
    """`users` table — matches `database._init_db_inner` exactly.

    All timestamp-like columns are INTEGER (unix epoch in seconds), kept
    that way to match the legacy `int(time.time())` writes scattered
    across `database.py`.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    phone = Column(Text)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default="user")
    balance = Column(Float, nullable=False, default=0)
    active = Column(Integer, nullable=False, default=1)

    # Email verification (V35+)
    email_verified = Column(Integer, nullable=False, default=0)
    email_token = Column(Text)
    email_token_created_at = Column(Integer)

    # Password reset (V36+)
    reset_token = Column(Text)
    reset_token_created_at = Column(Integer)

    # Pending email change (V40+)
    pending_email = Column(Text)
    pending_email_token = Column(Text)
    pending_email_created_at = Column(Integer)

    # Admin 2FA (V51 task B)
    totp_secret = Column(Text)
    totp_enabled = Column(Integer, nullable=False, default=0)
    totp_backup_codes = Column(Text)
    totp_enabled_at = Column(Integer)

    # Google OAuth (V42 batch2)
    google_sub = Column(Text)

    # Session invalidation (V53)
    session_version = Column(Integer, nullable=False, default=1)

    created_at = Column(Integer, nullable=False)


Index("idx_users_email", User.email)
Index("idx_users_email_token", User.email_token)
Index("idx_users_reset_token", User.reset_token)
Index("idx_users_google_sub", User.google_sub)


# ---------------------------------------------------------------------------
# games
# ---------------------------------------------------------------------------
class Game(Base):
    """`games` table — provider + game_key form the natural key."""

    __tablename__ = "games"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(Text, nullable=False)
    game_key = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    emoji = Column(Text, default="🎮")
    image_url = Column(Text, default="")
    active = Column(Integer, nullable=False, default=1)

    # V42+: per-game pricing currency override
    pricing_currency = Column(Text, default="GLOBAL")

    # V55: admin-controlled homepage visibility
    show_on_home = Column(Integer, nullable=False, default=0)
    home_sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("provider", "game_key", name="uq_games_provider_game_key"),
    )


Index("idx_games_active", Game.active, Game.name)


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------
class Product(Base):
    """`products` table — packages sold for each game.

    Note: there is no `sort_order` index in the legacy schema, but
    `(active, sort_order)` is a frequent filter; we add it now to make
    listings cheap on Postgres without changing existing behaviour on
    SQLite.
    """

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(Text, nullable=False)
    game_key = Column(Text, nullable=False)
    provider_product_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    base_price = Column(Float, nullable=False)
    sell_price = Column(Float, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Integer, nullable=False, default=1)

    # Group within a game (e.g. "PUBG UC packs" vs "PUBG specials")
    group_id = Column(Integer)

    # Pricing variants (V42+)
    fixed_syp_price = Column(Float, nullable=False, default=0)
    pricing_mode = Column(Text, default="usd")
    manual_price_syp = Column(Float, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_product_id", name="uq_products_provider_pid"
        ),
    )


Index("idx_products_game", Product.provider, Product.game_key)
Index("idx_products_game_key", Product.game_key)
Index("idx_products_active_sort", Product.active, Product.sort_order)


# ---------------------------------------------------------------------------
# product_groups
# ---------------------------------------------------------------------------
class ProductGroup(Base):
    """`product_groups` table — admin-defined groupings inside a game."""

    __tablename__ = "product_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(Text, nullable=False)
    game_key = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    image_url = Column(Text, default="")
    sort_order = Column(Integer, nullable=False, default=1)
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "provider", "game_key", "name", name="uq_product_groups_pgn"
        ),
    )


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------
class Order(Base):
    """`orders` table — every purchase, manual and automated."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_code = Column(Text, nullable=False, unique=True)
    user_id = Column(Integer, nullable=False)
    provider = Column(Text, nullable=False)
    game_key = Column(Text, nullable=False)
    game_name = Column(Text, nullable=False)
    product_id = Column(Integer, nullable=False)
    product_name = Column(Text, nullable=False)
    player_id = Column(Text, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(Text, nullable=False, default="waiting")
    provider_order_id = Column(Text)
    note = Column(Text)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)

    # V73: raw supplier response captured BEFORE parsing. Truncated to
    # 4 KiB by ``database.update_order_provider_response`` so a stuck
    # ``supplier_pending`` order can always be diagnosed even when the
    # order_id extractor fails.
    provider_response_raw = Column(Text)


Index("idx_orders_user_id", Order.user_id)
Index("idx_orders_status", Order.status)
Index("idx_orders_created_at", Order.created_at)
Index("idx_orders_user_created", Order.user_id, Order.created_at.desc())
Index("idx_orders_status_created", Order.status, Order.created_at.desc())

# V73: orphan-watch index. Partial on Postgres so it only covers rows the
# orphan-recovery query actually walks; on SQLite SQLAlchemy ignores the
# ``postgresql_where`` kwarg and creates a plain composite index, which is
# still cheap enough for the test suite.
Index(
    "idx_orders_orphan",
    Order.status,
    Order.provider_order_id,
    postgresql_where=sa_text("provider_response_raw IS NOT NULL"),
)


# ---------------------------------------------------------------------------
# deposits
# ---------------------------------------------------------------------------
class Deposit(Base):
    """`deposits` table — wallet top-up requests, reviewed by admin."""

    __tablename__ = "deposits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deposit_code = Column(Text, nullable=False, unique=True)
    user_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    method = Column(Text, nullable=False)
    proof = Column(Text)
    status = Column(Text, nullable=False, default="pending")
    created_at = Column(Integer, nullable=False)

    # Multi-currency support (V42+)
    currency = Column(Text, default="USD")
    amount_usd = Column(Float, default=0)

    # V53 IDOR fix — store proof filename in DB so ownership is checked
    # via DB lookup, not via filename prefix.
    proof_filename = Column(Text)


Index("idx_deposits_user_id", Deposit.user_id)
Index("idx_deposits_status", Deposit.status)
Index("idx_deposits_user_created", Deposit.user_id, Deposit.created_at.desc())


# ---------------------------------------------------------------------------
# payment_methods
# ---------------------------------------------------------------------------
class PaymentMethod(Base):
    """`payment_methods` table — admin-managed deposit methods.

    Note: `id` is a string here (e.g. "usdt", "binance", "syriatel"),
    NOT an auto-increment integer like the other tables.
    """

    __tablename__ = "payment_methods"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    emoji = Column(Text, nullable=False, default="💳")
    address = Column(Text, nullable=False, default="")
    instructions = Column(Text, nullable=False, default="")
    active = Column(Integer, nullable=False, default=1)
    currency = Column(Text, nullable=False, default="USD")


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
class Setting(Base):
    """`settings` table — key/value config bag, read by app.get_setting()."""

    __tablename__ = "settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)


Index("idx_settings_key", Setting.key)


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """`audit_log` table — append-only audit trail for admin actions.

    Rows are NEVER updated or deleted via the application API. Created
    by `audit.log_audit()` in V52.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    actor_id = Column(Integer)
    actor_email = Column(Text)
    target_type = Column(Text)
    target_id = Column(Text)
    ip = Column(Text)
    user_agent = Column(Text)
    old_value = Column(Text)
    new_value = Column(Text)
    meta = Column("metadata", Text)


Index("idx_audit_ts", AuditLog.ts.desc())
Index("idx_audit_actor", AuditLog.actor_id, AuditLog.ts.desc())
Index("idx_audit_target", AuditLog.target_type, AuditLog.target_id)
Index("idx_audit_action", AuditLog.action, AuditLog.ts.desc())


# ---------------------------------------------------------------------------
# wishlist (V42 batch2)
# ---------------------------------------------------------------------------
class Wishlist(Base):
    """`wishlist` table — user-saved games for quick access."""

    __tablename__ = "wishlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    provider = Column(Text, nullable=False)
    game_key = Column(Text, nullable=False)
    created_at = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "game_key", name="uq_wishlist_user_game"
        ),
    )


Index("idx_wishlist_user", Wishlist.user_id, Wishlist.created_at.desc())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "User",
    "Game",
    "Product",
    "ProductGroup",
    "Order",
    "Deposit",
    "PaymentMethod",
    "Setting",
    "AuditLog",
    "Wishlist",
]
