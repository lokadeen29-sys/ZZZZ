"""V72 phase 3 / PR #5 — behavioural parity tests for the rewritten
balance / order helpers in ``database.py``.

Scope: the three critical balance-touching functions migrated in PR #5.

  * ``set_user_balance(user_id, amount)``       — admin balance edit
  * ``change_balance(user_id, amount)``         — additive delta
  * ``create_order(user, product, game, player_id)`` — atomic
    purchase: balance check + deduction + INSERT all in one tx.

What we are guarding against
----------------------------

These functions own the wallet money trail. Any regression here
shows up as a real-money bug (double-spend, lost top-up, missed
refund), so the tests are deliberately paranoid:

  * ``create_order`` MUST be atomic. The legacy code wrapped a
    ``BEGIN IMMEDIATE`` around an ``UPDATE ... WHERE balance >= price``
    so two concurrent buyers could not both pass the check. We
    reproduce that with a single SQLAlchemy ``update()`` whose
    ``rowcount`` is checked. If we ever drop the ``WHERE balance >=
    price`` clause or revert to a Python-side pre-check, two threads
    will both deduct.
  * ``order_code`` MUST be cryptographically random (V50 C2). The
    legacy ``f"ORD{now}{user_id}"`` was trivially predictable; an
    attacker who knew a user_id + rough order time could guess
    codes and probe endpoints that treat the code as a bearer.
    These tests assert the output is unique across many calls and
    starts with ``ORD``.
  * ``set_user_balance`` MUST coerce ``user_id`` (legacy used
    ``int(user_id)`` to mirror SQLite's implicit cast).
  * ``change_balance`` MUST do ``balance + amount`` server-side, NOT
    a Python read-then-write — concurrent callers on the same user
    cannot race when the UPDATE is one statement.

Where these don't run
---------------------

``conftest.py`` boots ``app`` (and therefore the ORM engine) against
a per-test SQLite file inside ``tmp_path``. We never hit a real
network or real Postgres here. The ORM's atomicity guarantees on
SQLite are enough to verify the contract — Postgres adds row-level
locking on top, which is at least as strong.
"""

from __future__ import annotations

import pytest


# ===========================================================================
# Shared helpers
# ===========================================================================
ORDER_COLUMNS = {
    "id", "order_code", "user_id", "provider", "game_key", "game_name",
    "product_id", "product_name", "player_id", "price", "status",
    "provider_order_id", "note", "created_at", "updated_at",
}


def _user_balance(database, user_id):
    """Return the user's current balance straight from the DB (bypass any caches)."""
    conn = database.connect()
    try:
        row = conn.execute(
            "SELECT balance FROM users WHERE id=?", (user_id,)
        ).fetchone()
        return float(row["balance"]) if row else None
    finally:
        conn.close()


def _seed_game_and_product(database, *, sell_price=10.0, manual_syp=0.0):
    """Insert one game and one product. Returns (product_dict, game_dict)
    in the same shape ``database.get_product`` / ``database.get_game``
    would return them."""
    conn = database.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO games(provider, game_key, name, active) "
            "VALUES (?,?,?,1)",
            ("local", "pr5game", "PR5 Test Game"),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO products(
                provider, game_key, provider_product_id, name,
                base_price, sell_price, sort_order, active,
                manual_price_syp
            ) VALUES (?,?,?,?,?,?,?,1,?)
            """,
            (
                "local", "pr5game", "PR5_PROD_1", "PR5 Pack",
                sell_price * 0.8, sell_price, 1, manual_syp,
            ),
        )
        conn.commit()
        game = dict(conn.execute(
            "SELECT * FROM games WHERE provider=? AND game_key=?",
            ("local", "pr5game"),
        ).fetchone())
        product = dict(conn.execute(
            "SELECT * FROM products WHERE provider=? AND provider_product_id=?",
            ("local", "PR5_PROD_1"),
        ).fetchone())
    finally:
        conn.close()
    return product, game


# ===========================================================================
# set_user_balance
# ===========================================================================
class TestSetUserBalance:
    """``set_user_balance`` overwrites balance with an absolute value."""

    def test_sets_exact_value(self, app, make_user):
        db = app._test_database
        user = make_user(email="setbal@test.local", balance=0.0)

        db.set_user_balance(user["id"], 42.5)

        assert _user_balance(db, user["id"]) == 42.5

    def test_overwrites_existing_balance(self, app, make_user):
        db = app._test_database
        user = make_user(email="setbal2@test.local", balance=100.0)

        db.set_user_balance(user["id"], 7.25)

        # NOT 107.25 — set is absolute, not additive.
        assert _user_balance(db, user["id"]) == 7.25

    def test_accepts_string_user_id(self, app, make_user):
        """Legacy did ``int(user_id)`` — string IDs from form posts must work."""
        db = app._test_database
        user = make_user(email="setbal3@test.local", balance=0.0)

        db.set_user_balance(str(user["id"]), 12.0)

        assert _user_balance(db, user["id"]) == 12.0

    def test_none_amount_collapses_to_zero(self, app, make_user):
        """``float(None or 0)`` → 0.0 in the legacy code; we preserve that."""
        db = app._test_database
        user = make_user(email="setbal4@test.local", balance=50.0)

        db.set_user_balance(user["id"], None)

        assert _user_balance(db, user["id"]) == 0.0

    def test_empty_string_amount_collapses_to_zero(self, app, make_user):
        """Form POST with empty input → ``""`` → ``float("" or 0)`` = 0.0."""
        db = app._test_database
        user = make_user(email="setbal5@test.local", balance=50.0)

        db.set_user_balance(user["id"], "")

        assert _user_balance(db, user["id"]) == 0.0

    def test_missing_user_is_silent_noop(self, app):
        """Legacy issued the UPDATE regardless; the rowcount was not
        checked. Missing-user calls must NOT raise."""
        db = app._test_database

        # Should NOT raise.
        db.set_user_balance(999_999_999, 5.0)


# ===========================================================================
# change_balance
# ===========================================================================
class TestChangeBalance:
    """``change_balance`` applies a signed delta to the balance."""

    def test_positive_delta_credits_account(self, app, make_user):
        db = app._test_database
        user = make_user(email="chg1@test.local", balance=10.0)

        db.change_balance(user["id"], 5.0)

        assert _user_balance(db, user["id"]) == 15.0

    def test_negative_delta_debits_account(self, app, make_user):
        db = app._test_database
        user = make_user(email="chg2@test.local", balance=10.0)

        db.change_balance(user["id"], -3.5)

        assert _user_balance(db, user["id"]) == 6.5

    def test_no_clamping_at_zero(self, app, make_user):
        """``change_balance`` does not protect against overdraft — that
        is the caller's job. The V47 floor is enforced by
        ``create_order``, not by this helper."""
        db = app._test_database
        user = make_user(email="chg3@test.local", balance=2.0)

        db.change_balance(user["id"], -10.0)

        assert _user_balance(db, user["id"]) == -8.0

    def test_zero_delta_is_noop(self, app, make_user):
        db = app._test_database
        user = make_user(email="chg4@test.local", balance=5.0)

        db.change_balance(user["id"], 0)

        assert _user_balance(db, user["id"]) == 5.0

    def test_missing_user_is_silent_noop(self, app):
        db = app._test_database

        # Should NOT raise.
        db.change_balance(999_999_999, 5.0)


# ===========================================================================
# create_order — happy path + balance deduction
# ===========================================================================
@pytest.mark.postgres
class TestCreateOrderHappyPath:
    """Successful purchase: row inserted + balance deducted by exactly
    the stored ``price`` (which is also returned)."""

    def test_returns_int_id_and_order_code(self, app, make_user):
        db = app._test_database
        user = make_user(email="ord1@test.local", balance=20.0)
        product, game = _seed_game_and_product(db, sell_price=10.0)

        order_id, order_code = db.create_order(user, product, game, "PLAYER_X")

        assert isinstance(order_id, int)
        assert order_id > 0
        assert isinstance(order_code, str)
        assert order_code.startswith("ORD")
        # secrets.token_urlsafe(10) → at least 13 chars after the prefix.
        assert len(order_code) >= len("ORD") + 13

    def test_balance_deducted_exactly_by_price(self, app, make_user):
        db = app._test_database
        user = make_user(email="ord2@test.local", balance=10.0)
        product, game = _seed_game_and_product(db, sell_price=2.5)

        db.create_order(user, product, game, "P1")

        assert _user_balance(db, user["id"]) == 7.5

    def test_inserted_row_columns_match_legacy_shape(self, app, make_user):
        """The inserted row must have every column the legacy
        ``INSERT INTO orders (...) VALUES (...)`` populated, with the
        right types."""
        db = app._test_database
        user = make_user(email="ord3@test.local", balance=20.0)
        product, game = _seed_game_and_product(db, sell_price=4.0)

        order_id, order_code = db.create_order(
            user, product, game, "PLAYER_42"
        )

        order = db.get_order(order_id)

        # Full column set is present.
        assert ORDER_COLUMNS.issubset(order.keys())
        assert order["order_code"] == order_code
        assert order["user_id"] == user["id"]
        assert order["provider"] == "local"
        assert order["game_key"] == "pr5game"
        assert order["game_name"] == "PR5 Test Game"
        assert order["product_id"] == product["id"]
        assert order["player_id"] == "PLAYER_42"
        assert order["price"] == 4.0
        assert order["status"] == "waiting"
        # Timestamps written from ``int(time.time())`` are equal at insert.
        assert order["created_at"] == order["updated_at"]
        assert order["created_at"] > 0
        # provider_order_id / note are untouched by create_order.
        assert order["provider_order_id"] is None
        assert order["note"] is None

    def test_product_label_uses_display_name_when_present(self, app, make_user):
        """If the product dict carries ``display_name`` (the
        translated label injected by ``list_products`` / ``get_product``),
        it must end up in ``orders.product_name`` — that's the snapshot
        used by every admin queue and history page."""
        db = app._test_database
        user = make_user(email="ord4@test.local", balance=20.0)
        product, game = _seed_game_and_product(db, sell_price=1.0)
        product = dict(product)
        product["display_name"] = "حزمة خاصة"

        order_id, _ = db.create_order(user, product, game, "P1")

        order = db.get_order(order_id)
        # ``translate_product_name`` is identity for the Arabic label.
        assert "حزمة" in order["product_name"]

    def test_product_label_falls_back_to_name(self, app, make_user):
        """When ``display_name`` is absent, the label must fall back
        to ``product["name"]``."""
        db = app._test_database
        user = make_user(email="ord5@test.local", balance=20.0)
        product, game = _seed_game_and_product(db, sell_price=1.0)
        # Strip display_name if conftest happened to inject one.
        product.pop("display_name", None)

        order_id, _ = db.create_order(user, product, game, "P1")

        order = db.get_order(order_id)
        assert order["product_name"] == "PR5 Pack"


# ===========================================================================
# create_order — V47 atomic balance check
# ===========================================================================
@pytest.mark.postgres
class TestCreateOrderInsufficientBalance:
    """The V47 contract: when balance < price, no row is inserted and
    the user's balance is NOT touched."""

    def test_raises_insufficient_balance(self, app, make_user):
        db = app._test_database
        user = make_user(email="poor1@test.local", balance=1.0)
        product, game = _seed_game_and_product(db, sell_price=5.0)

        with pytest.raises(db.InsufficientBalance):
            db.create_order(user, product, game, "P1")

    def test_balance_unchanged_on_insufficient(self, app, make_user):
        db = app._test_database
        user = make_user(email="poor2@test.local", balance=1.0)
        product, game = _seed_game_and_product(db, sell_price=5.0)

        with pytest.raises(db.InsufficientBalance):
            db.create_order(user, product, game, "P1")

        # Balance must be exactly what we started with — no
        # partial deduction, no rollback drift.
        assert _user_balance(db, user["id"]) == 1.0

    def test_no_order_row_inserted_on_insufficient(self, app, make_user):
        db = app._test_database
        user = make_user(email="poor3@test.local", balance=1.0)
        product, game = _seed_game_and_product(db, sell_price=5.0)
        before = len(db.list_user_orders(user["id"]))

        with pytest.raises(db.InsufficientBalance):
            db.create_order(user, product, game, "P1")

        after = len(db.list_user_orders(user["id"]))
        assert after == before

    def test_exact_balance_succeeds(self, app, make_user):
        """Boundary: ``balance == price`` must succeed (the WHERE
        clause is ``>=``, not ``>``). Same as legacy."""
        db = app._test_database
        user = make_user(email="exact@test.local", balance=2.5)
        product, game = _seed_game_and_product(db, sell_price=2.5)

        order_id, _ = db.create_order(user, product, game, "P1")

        assert order_id > 0
        assert _user_balance(db, user["id"]) == 0.0

    def test_zero_balance_with_zero_price_succeeds(self, app, make_user):
        """Edge: ``0 >= 0`` is true, so a free product with zero
        balance must still produce an order. Legacy did the same."""
        db = app._test_database
        user = make_user(email="freebie@test.local", balance=0.0)
        product, game = _seed_game_and_product(db, sell_price=0.0)

        order_id, _ = db.create_order(user, product, game, "P1")

        assert order_id > 0


# ===========================================================================
# create_order — order_code uniqueness (V50 C2)
# ===========================================================================
@pytest.mark.postgres
class TestCreateOrderCode:
    def test_codes_are_unique_across_many_orders(self, app, make_user):
        """V50: ``order_code`` MUST come from ``secrets.token_urlsafe``.
        Twenty rapid calls must produce twenty distinct codes."""
        db = app._test_database
        user = make_user(email="uniq@test.local", balance=100.0)
        product, game = _seed_game_and_product(db, sell_price=0.5)

        codes = set()
        for _ in range(20):
            _, code = db.create_order(user, product, game, "PX")
            codes.add(code)

        assert len(codes) == 20

    def test_code_is_not_predictable_from_user_id_and_time(self, app, make_user):
        """Negative test: the code MUST NOT be of the form
        ``ORD<unix_seconds><user_id>`` (the pre-V50 pattern). Even if
        an attacker knew both, they should not be able to construct
        the code."""
        db = app._test_database
        user = make_user(email="pred@test.local", balance=10.0)
        product, game = _seed_game_and_product(db, sell_price=0.5)

        _, code = db.create_order(user, product, game, "PX")

        body = code[len("ORD"):]
        # The body must NOT be a pure decimal digit string (which
        # would mean it's just timestamp + user_id concatenated).
        assert not body.isdigit(), (
            "order_code body looks like the legacy predictable pattern "
            "(ORD<timestamp><user_id>) — V50 (C2) regression"
        )


# ===========================================================================
# create_order — error path
# ===========================================================================
class TestCreateOrderErrors:
    def test_unknown_user_id_raises_insufficient_balance(self, app):
        """A user ID that doesn't exist has ``rowcount=0`` on the
        UPDATE, so create_order must raise ``InsufficientBalance`` —
        the same surface contract as a real low-balance user. (We
        deliberately do NOT add a separate "user not found" exception;
        every caller of create_order is already authenticated.)"""
        db = app._test_database
        product, game = _seed_game_and_product(db, sell_price=1.0)
        fake_user = {"id": 999_999_999}

        with pytest.raises(db.InsufficientBalance):
            db.create_order(fake_user, product, game, "P1")

    def test_does_not_swallow_unexpected_exceptions(
        self, app, make_user, monkeypatch
    ):
        """If the INSERT fails for any reason other than insufficient
        balance, the exception MUST propagate (legacy behaviour).
        We force this by violating the UNIQUE order_code constraint
        through monkeypatching ``secrets.token_urlsafe`` to return a
        constant, then issuing two creates."""
        db = app._test_database
        user = make_user(email="errpath@test.local", balance=20.0)
        product, game = _seed_game_and_product(db, sell_price=1.0)

        monkeypatch.setattr(
            db.secrets, "token_urlsafe", lambda *a, **kw: "FIXEDCONSTANT"
        )

        # First call is fine.
        db.create_order(user, product, game, "P1")
        # Second call collides on order_code → IntegrityError →
        # must NOT be caught by our generic except.
        with pytest.raises(Exception) as excinfo:
            db.create_order(user, product, game, "P1")
        assert not isinstance(excinfo.value, db.InsufficientBalance)

    def test_rolled_back_on_insert_failure(
        self, app, make_user, monkeypatch
    ):
        """When the INSERT fails after the deduction, the deduction
        must be rolled back — otherwise the user is charged for an
        order that doesn't exist. Triggered the same way as above."""
        db = app._test_database
        user = make_user(email="rb@test.local", balance=20.0)
        product, game = _seed_game_and_product(db, sell_price=4.0)

        monkeypatch.setattr(
            db.secrets, "token_urlsafe", lambda *a, **kw: "ROLLBACKTEST"
        )

        # First create succeeds (balance: 20 → 16).
        db.create_order(user, product, game, "P1")
        assert _user_balance(db, user["id"]) == 16.0

        # Second create collides on the UNIQUE order_code.
        with pytest.raises(Exception):
            db.create_order(user, product, game, "P1")

        # Balance must STILL be 16.0 — the failed second order
        # must not have deducted.
        assert _user_balance(db, user["id"]) == 16.0
