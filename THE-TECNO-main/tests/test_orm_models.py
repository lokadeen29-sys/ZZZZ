"""V72 phase 1 — smoke tests for the new ORM models.

These tests do NOT touch production data. They:

  1. Create a fresh in-memory SQLite DB.
  2. Run `Base.metadata.create_all()` — the same create code Alembic
     will eventually run on Postgres in phase 2.
  3. Insert + read one row per model.
  4. Verify the round-trip preserves field values.

If any of these fails, `app/db/models.py` has a typo or a wrong
column type. Run with:

    pytest tests/test_orm_models.py -v
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    AuditLog,
    Deposit,
    Game,
    Order,
    PaymentMethod,
    Product,
    ProductGroup,
    Setting,
    User,
    Wishlist,
)


@pytest.fixture()
def session():
    """Yield a SQLAlchemy session backed by an in-memory SQLite DB.

    Each test gets a fresh DB so cross-test side-effects are impossible.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Per-table round-trip tests
# ---------------------------------------------------------------------------
def test_user_round_trip(session):
    u = User(
        name="Test User",
        email="test@example.com",
        password_hash="hashed",
        role="user",
        balance=10.5,
        active=1,
        created_at=1700000000,
    )
    session.add(u)
    session.commit()

    fetched = session.query(User).filter_by(email="test@example.com").one()
    assert fetched.id is not None
    assert fetched.name == "Test User"
    assert fetched.balance == 10.5
    assert fetched.role == "user"
    assert fetched.session_version == 1  # default


def test_user_email_unique(session):
    """`email` has a UNIQUE constraint."""
    a = User(
        name="A", email="dup@x.com", password_hash="x", created_at=1
    )
    b = User(
        name="B", email="dup@x.com", password_hash="x", created_at=2
    )
    session.add(a)
    session.commit()
    session.add(b)
    with pytest.raises(Exception):  # IntegrityError on Postgres / OperationalError on sqlite
        session.commit()


def test_game_round_trip(session):
    g = Game(provider="server2", game_key="pubg", name="PUBG", emoji="🔫")
    session.add(g)
    session.commit()
    assert g.id is not None
    assert g.show_on_home == 0  # default


def test_game_unique_provider_key(session):
    a = Game(provider="server1", game_key="ff", name="Free Fire")
    b = Game(provider="server1", game_key="ff", name="Free Fire dup")
    session.add(a)
    session.commit()
    session.add(b)
    with pytest.raises(Exception):
        session.commit()


def test_product_round_trip(session):
    p = Product(
        provider="server2",
        game_key="pubg",
        provider_product_id="ext_42",
        name="60 UC",
        base_price=0.85,
        sell_price=1.20,
    )
    session.add(p)
    session.commit()
    assert p.pricing_mode == "usd"  # default
    assert p.fixed_syp_price == 0


def test_product_unique_provider_pid(session):
    a = Product(
        provider="s1",
        game_key="g",
        provider_product_id="x",
        name="A",
        base_price=1,
        sell_price=2,
    )
    b = Product(
        provider="s1",
        game_key="g",
        provider_product_id="x",
        name="B",
        base_price=1,
        sell_price=2,
    )
    session.add(a)
    session.commit()
    session.add(b)
    with pytest.raises(Exception):
        session.commit()


def test_product_group_round_trip(session):
    pg = ProductGroup(
        provider="server2",
        game_key="pubg",
        name="UC Standard",
        sort_order=1,
        active=1,
        created_at=1700000000,
    )
    session.add(pg)
    session.commit()
    assert pg.id is not None


def test_order_round_trip(session):
    o = Order(
        order_code="ORD_TEST_42",
        user_id=1,
        provider="server2",
        game_key="pubg",
        game_name="PUBG",
        product_id=1,
        product_name="60 UC",
        player_id="player_1",
        price=1.20,
        status="waiting",
        created_at=1700000000,
        updated_at=1700000000,
    )
    session.add(o)
    session.commit()
    assert o.id is not None
    assert o.status == "waiting"


def test_order_code_unique(session):
    a = Order(
        order_code="DUP",
        user_id=1,
        provider="s",
        game_key="g",
        game_name="G",
        product_id=1,
        product_name="P",
        player_id="x",
        price=1,
        created_at=1,
        updated_at=1,
    )
    b = Order(
        order_code="DUP",
        user_id=2,
        provider="s",
        game_key="g",
        game_name="G",
        product_id=1,
        product_name="P",
        player_id="x",
        price=1,
        created_at=1,
        updated_at=1,
    )
    session.add(a)
    session.commit()
    session.add(b)
    with pytest.raises(Exception):
        session.commit()


def test_deposit_round_trip(session):
    d = Deposit(
        deposit_code="DEP_TEST_99",
        user_id=1,
        amount=50,
        method="usdt",
        proof="tx-hash-xyz",
        status="pending",
        created_at=1700000000,
    )
    session.add(d)
    session.commit()
    assert d.id is not None
    assert d.currency == "USD"  # default


def test_payment_method_round_trip(session):
    pm = PaymentMethod(
        id="usdt",
        name="USDT (TRC20)",
        emoji="🪙",
        address="TXXXX...",
        instructions="Send to address above.",
        active=1,
        currency="USD",
    )
    session.add(pm)
    session.commit()
    fetched = session.query(PaymentMethod).filter_by(id="usdt").one()
    assert fetched.name == "USDT (TRC20)"


def test_setting_round_trip(session):
    s = Setting(key="usd_syp_rate", value="15000")
    session.add(s)
    session.commit()
    fetched = session.query(Setting).filter_by(key="usd_syp_rate").one()
    assert fetched.value == "15000"


def test_audit_log_metadata_alias(session):
    """The ORM uses `meta` while the DB column is `metadata` (reserved word
    in SQLAlchemy 2.x). Both should work end-to-end."""
    row = AuditLog(
        ts=1700000000,
        action="ADMIN_BALANCE_CHANGE",
        actor_id=1,
        actor_email="admin@example.com",
        target_type="user",
        target_id="42",
        ip="1.2.3.4",
        meta='{"reason": "manual top-up"}',
    )
    session.add(row)
    session.commit()
    fetched = session.query(AuditLog).first()
    assert fetched.meta == '{"reason": "manual top-up"}'


def test_wishlist_round_trip(session):
    w = Wishlist(
        user_id=1,
        provider="server2",
        game_key="pubg",
        created_at=1700000000,
    )
    session.add(w)
    session.commit()
    assert w.id is not None


def test_wishlist_unique_constraint(session):
    a = Wishlist(user_id=1, provider="s", game_key="g", created_at=1)
    b = Wishlist(user_id=1, provider="s", game_key="g", created_at=2)
    session.add(a)
    session.commit()
    session.add(b)
    with pytest.raises(Exception):
        session.commit()


# ---------------------------------------------------------------------------
# Indexes / metadata sanity
# ---------------------------------------------------------------------------
def test_all_models_registered():
    """Every model is registered under Base.metadata."""
    expected = {
        "users",
        "games",
        "products",
        "product_groups",
        "orders",
        "deposits",
        "payment_methods",
        "settings",
        "audit_log",
        "wishlist",
    }
    actual = set(Base.metadata.tables.keys())
    assert expected.issubset(actual), f"Missing: {expected - actual}"
