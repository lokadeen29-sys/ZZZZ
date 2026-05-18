"""V51 task C — orders, wallet and balance integrity tests.

Covers:
  - `create_order` deducts balance atomically
  - Insufficient balance raises InsufficientBalance
  - order_code starts with ORD and is unique across many orders
  - /wallet deposit rejects over-limit amounts
  - /wallet deposit rejects zero / negative amounts
  - set_user_balance sets value exactly
"""
from __future__ import annotations

import pytest


def _make_product(app, *, price_usd: float = 1.0):
    """Insert a product + game + group so create_order has valid rows.
    Returns (product_dict, game_dict)."""
    db = app._test_database
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO games(provider, game_key, name, active) "
            "VALUES (?,?,?,1)",
            ("local", "testgame", "Test Game"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO products(provider, game_key, provider_product_id, name, "
            "base_price, sell_price, sort_order, active) "
            "VALUES (?,?,?,?,?,?,?,1)",
            ("local", "testgame", "TP1", "Test Pack", price_usd * 0.8, price_usd, 1),
        )
        conn.commit()
    finally:
        conn.close()
    conn = db.connect()
    game = dict(conn.execute(
        "SELECT * FROM games WHERE provider=? AND game_key=?",
        ("local", "testgame"),
    ).fetchone())
    product = dict(conn.execute(
        "SELECT * FROM products WHERE provider=? AND provider_product_id=?",
        ("local", "TP1"),
    ).fetchone())
    conn.close()
    return product, game


# ---------------------------------------------------------------------------
# create_order — balance deduction + order_code uniqueness
# ---------------------------------------------------------------------------
class TestCreateOrder:
    def test_deducts_balance_on_success(self, app, make_user):
        db = app._test_database
        user = make_user(email="orders@test.local", balance=10.0)
        product, game = _make_product(app, price_usd=2.5)

        order_id, code = db.create_order(user, product, game, player_id="PID123")

        assert order_id > 0
        assert code.startswith("ORD")
        refreshed = db.get_user_by_email(user["email"])
        assert round(float(refreshed["balance"]), 4) == 7.5

    def test_raises_insufficient_balance(self, app, make_user):
        db = app._test_database
        user = make_user(email="broke@test.local", balance=1.0)
        product, game = _make_product(app, price_usd=5.0)

        with pytest.raises(db.InsufficientBalance):
            db.create_order(user, product, game, player_id="PID999")

        # Balance must NOT be touched when the order fails.
        refreshed = db.get_user_by_email(user["email"])
        assert round(float(refreshed["balance"]), 4) == 1.0

    def test_order_codes_are_unique(self, app, make_user):
        db = app._test_database
        user = make_user(email="many@test.local", balance=100.0)
        product, game = _make_product(app, price_usd=0.5)

        codes = set()
        for _ in range(20):
            _, code = db.create_order(user, product, game, player_id="PX")
            codes.add(code)
        assert len(codes) == 20
        for code in codes:
            assert code.startswith("ORD")
            # secrets.token_urlsafe(10) returns >=13 chars.
            assert len(code) >= 13


# ---------------------------------------------------------------------------
# /wallet deposit — input validation
# ---------------------------------------------------------------------------
class TestWalletDeposit:
    def test_deposit_requires_valid_amount(self, app, client, make_user, login_as):
        user = make_user(email="w1@test.local")
        login_as(user["id"])
        resp = client.post(
            "/wallet",
            data={"amount": "0", "method_id": "usdt", "proof": "tx123hash"},
            follow_redirects=False,
        )
        # Redirects back to /wallet, no deposit stored.
        assert resp.status_code in (302, 303)
        deposits = app._test_database.list_deposits_for_user(user["id"])
        assert len(deposits) == 0

    def test_deposit_rejects_over_limit(self, app, client, make_user, login_as, monkeypatch):
        # Clamp the ceiling low so we don't need millions in the request body.
        monkeypatch.setattr(app._test_module, "MAX_DEPOSIT_USD", 100.0)
        user = make_user(email="w2@test.local")
        login_as(user["id"])
        resp = client.post(
            "/wallet",
            data={"amount": "5000", "method_id": "usdt", "proof": "tx"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert len(app._test_database.list_deposits_for_user(user["id"])) == 0

    def test_deposit_proof_optional(self, app, client, make_user, login_as):
        # V67.3: الإيصال أصبح اختياريًا. عند تركه فارغًا يجب إنشاء الإيداع
        # مع نص افتراضي يوضّح أنه سيُراجع يدويًا من الإدارة.
        user = make_user(email="w3@test.local")
        login_as(user["id"])
        resp = client.post(
            "/wallet",
            data={"amount": "10", "method_id": "usdt", "proof": ""},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        deposits = app._test_database.list_deposits_for_user(user["id"])
        assert len(deposits) == 1
        assert "بدون إيصال" in (deposits[0]["proof"] or "")

    def test_deposit_requires_valid_method(self, app, client, make_user, login_as):
        user = make_user(email="w4@test.local")
        login_as(user["id"])
        resp = client.post(
            "/wallet",
            data={"amount": "10", "method_id": "not_a_real_method", "proof": "abc"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert len(app._test_database.list_deposits_for_user(user["id"])) == 0

    def test_deposit_happy_path_creates_pending_deposit(
        self, app, client, make_user, login_as
    ):
        user = make_user(email="w5@test.local")
        login_as(user["id"])
        resp = client.post(
            "/wallet",
            data={"amount": "25", "method_id": "usdt", "proof": "tx-abc-123"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        deposits = app._test_database.list_deposits_for_user(user["id"])
        assert len(deposits) == 1
        assert deposits[0]["status"] == "pending"
        assert deposits[0]["amount"] == 25
        assert deposits[0]["deposit_code"]


# ---------------------------------------------------------------------------
# Balance helpers
# ---------------------------------------------------------------------------
class TestBalanceHelpers:
    def test_set_user_balance(self, app, make_user):
        db = app._test_database
        user = make_user(email="bal@test.local", balance=0.0)
        db.set_user_balance(user["id"], 42.5)
        refreshed = db.get_user_by_id(user["id"])
        assert float(refreshed["balance"]) == 42.5
