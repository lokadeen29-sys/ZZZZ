"""V72 phase 3 / PR #3 — behavioural parity tests for the rewritten
order helpers in ``database.py``.

Scope: the four order-related functions migrated in PR #3.

  * ``list_user_orders(user_id)``
  * ``list_orders(status=None)``
  * ``get_order(order_id)``
  * ``update_order(order_id, status, provider_order_id=None, note=None)``

What we are guarding against
----------------------------

These functions back the user "my orders" page, the admin orders queue,
and the RQ worker's status transitions. The single highest-risk piece
is ``update_order``'s **refund-on-rejection** rule, which has had two
production hotfixes (V47 atomicity, V69.1 transition guard). The tests
below assert on:

  1. **Return type & dict shape** — list / get functions return the
     full order dict with every legacy column.
  2. **Sort order + cap** — ``list_user_orders`` caps at 50 newest first;
     ``list_orders(None)`` caps at 200; ``list_orders(status=X)`` is
     uncapped.
  3. **Filters** — ``list_orders`` honours the ``status`` filter and
     does not leak orders from other statuses.
  4. **None semantics** — ``get_order(missing)`` returns ``None``, and
     accepts string IDs (``"1"`` works just like ``1``).
  5. **``update_order`` core invariants**:
       - Returns ``True`` on success, ``False`` on missing or terminal-
         transition rejection.
       - **Refund only on transition INTO rejected from non-rejected.**
         Crucially: **no refund** on ``rejected → rejected`` (no-op),
         and **no transition** from ``completed → rejected`` (V69.1).
       - The order's ``status``, ``provider_order_id``, ``note`` and
         ``updated_at`` are all updated atomically.
"""

from __future__ import annotations

import time

import pytest


# ===========================================================================
# Helpers — directly seed orders bypassing create_order's balance logic.
# We need the freedom to plant orders in any status (e.g. "completed") so
# we can exercise the V69.1 transition guard without going through the full
# checkout flow.
# ===========================================================================
def _seed_order(
    database,
    *,
    user_id,
    status="waiting",
    price=10.0,
    order_code=None,
    provider="server2",
    game_key="pubg",
    game_name="PUBG",
    product_id=1,
    product_name="60 UC",
    player_id="111111",
    note=None,
    provider_order_id=None,
    created_at=None,
    updated_at=None,
):
    """Insert a single row into ``orders`` and return its id."""
    if order_code is None:
        # Unique-ish code; tests never need to match a specific value.
        order_code = f"ORDtest-{int(time.time() * 1000)}-{user_id}-{status}"
    now = int(time.time())
    if created_at is None:
        created_at = now
    if updated_at is None:
        updated_at = now

    conn = database.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO orders (
                order_code, user_id, provider, game_key, game_name,
                product_id, product_name, player_id, price, status,
                provider_order_id, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_code,
                user_id,
                provider,
                game_key,
                game_name,
                product_id,
                product_name,
                player_id,
                price,
                status,
                provider_order_id,
                note,
                created_at,
                updated_at,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _user_balance(database, user_id):
    """Return the user's current balance straight from the DB (bypass cache)."""
    conn = database.connect()
    try:
        row = conn.execute(
            "SELECT balance FROM users WHERE id=?", (user_id,)
        ).fetchone()
        return float(row["balance"]) if row else None
    finally:
        conn.close()


ORDER_COLUMNS = {
    "id", "order_code", "user_id", "provider", "game_key", "game_name",
    "product_id", "product_name", "player_id", "price", "status",
    "provider_order_id", "note", "created_at", "updated_at",
}


# ===========================================================================
# get_order
# ===========================================================================
def test_get_order_returns_full_dict(app, make_user):
    database = app._test_database
    u = make_user()
    oid = _seed_order(database, user_id=u["id"], price=12.5, note="hello")

    o = database.get_order(oid)
    assert o is not None
    assert isinstance(o, dict)
    assert ORDER_COLUMNS.issubset(o.keys()), (
        f"missing legacy keys: {ORDER_COLUMNS - set(o.keys())}"
    )
    assert o["id"] == oid
    assert o["user_id"] == u["id"]
    assert o["price"] == 12.5
    assert o["status"] == "waiting"
    assert o["note"] == "hello"


def test_get_order_returns_none_for_missing(app):
    database = app._test_database
    assert database.get_order(99_999) is None


def test_get_order_accepts_string_id(app, make_user):
    """URL-resolved order ids arrive as strings; legacy SQLite cast them
    implicitly. The ORM rewrite must coerce explicitly."""
    database = app._test_database
    u = make_user()
    oid = _seed_order(database, user_id=u["id"])
    assert database.get_order(str(oid))["id"] == oid


def test_get_order_returns_none_for_invalid_id(app):
    database = app._test_database
    assert database.get_order("not-a-number") is None
    assert database.get_order(None) is None


# ===========================================================================
# list_user_orders
# ===========================================================================
def test_list_user_orders_only_returns_owner_rows(app, make_user):
    database = app._test_database
    alice = make_user(email="alice@test.local")
    bob = make_user(email="bob@test.local")
    _seed_order(database, user_id=alice["id"], order_code="ORDa1")
    _seed_order(database, user_id=bob["id"], order_code="ORDb1")

    rows = database.list_user_orders(alice["id"])
    assert {r["order_code"] for r in rows} == {"ORDa1"}


def test_list_user_orders_sorted_newest_first(app, make_user):
    database = app._test_database
    u = make_user()
    # Insert in increasing order; ids are auto-incremented so the oldest
    # has the smallest id and should appear LAST.
    ids = [
        _seed_order(database, user_id=u["id"], order_code=f"ORDseq-{i}")
        for i in range(5)
    ]

    rows = database.list_user_orders(u["id"])
    assert [r["id"] for r in rows] == list(reversed(ids))


def test_list_user_orders_caps_at_50(app, make_user):
    database = app._test_database
    u = make_user()
    for i in range(60):
        _seed_order(database, user_id=u["id"], order_code=f"ORDcap-{i}")

    rows = database.list_user_orders(u["id"])
    assert len(rows) == 50


def test_list_user_orders_empty_when_no_rows(app, make_user):
    database = app._test_database
    u = make_user()
    assert database.list_user_orders(u["id"]) == []


# ===========================================================================
# list_orders
# ===========================================================================
def test_list_orders_no_filter_returns_all_recent_rows(app, make_user):
    database = app._test_database
    u = make_user()
    _seed_order(database, user_id=u["id"], status="waiting", order_code="ORDw1")
    _seed_order(database, user_id=u["id"], status="completed", order_code="ORDc1")
    _seed_order(database, user_id=u["id"], status="rejected", order_code="ORDr1")

    rows = database.list_orders()
    statuses = {r["status"] for r in rows}
    assert statuses == {"waiting", "completed", "rejected"}
    assert len(rows) == 3


def test_list_orders_status_filter(app, make_user):
    database = app._test_database
    u = make_user()
    _seed_order(database, user_id=u["id"], status="waiting", order_code="ORDw1")
    _seed_order(database, user_id=u["id"], status="waiting", order_code="ORDw2")
    _seed_order(database, user_id=u["id"], status="completed", order_code="ORDc1")

    rows = database.list_orders(status="waiting")
    assert {r["order_code"] for r in rows} == {"ORDw1", "ORDw2"}


def test_list_orders_no_filter_caps_at_200(app, make_user):
    database = app._test_database
    u = make_user()
    for i in range(220):
        _seed_order(database, user_id=u["id"], order_code=f"ORDmany-{i}")

    rows = database.list_orders()
    assert len(rows) == 200


def test_list_orders_with_status_is_uncapped(app, make_user):
    """Admin processing queues need the full set; the legacy SQL
    deliberately omitted LIMIT here."""
    database = app._test_database
    u = make_user()
    for i in range(220):
        _seed_order(
            database, user_id=u["id"], status="waiting", order_code=f"ORDuw-{i}"
        )

    rows = database.list_orders(status="waiting")
    assert len(rows) == 220


def test_list_orders_sorted_newest_first(app, make_user):
    database = app._test_database
    u = make_user()
    ids = [
        _seed_order(database, user_id=u["id"], order_code=f"ORDord-{i}")
        for i in range(4)
    ]
    rows = database.list_orders()
    assert [r["id"] for r in rows] == list(reversed(ids))


# ===========================================================================
# update_order — happy paths
# ===========================================================================
def test_update_order_basic_status_change(app, make_user):
    database = app._test_database
    u = make_user(balance=100.0)
    oid = _seed_order(database, user_id=u["id"], status="waiting", price=15.0)

    ok = database.update_order(
        oid, "processing", provider_order_id="P-1", note="forwarded"
    )
    assert ok is True

    o = database.get_order(oid)
    assert o["status"] == "processing"
    assert o["provider_order_id"] == "P-1"
    assert o["note"] == "forwarded"
    # No refund on a non-reject transition.
    assert _user_balance(database, u["id"]) == 100.0


def test_update_order_completed_does_not_refund(app, make_user):
    database = app._test_database
    u = make_user(balance=50.0)
    oid = _seed_order(database, user_id=u["id"], status="waiting", price=20.0)

    assert database.update_order(oid, "completed") is True
    assert _user_balance(database, u["id"]) == 50.0


def test_update_order_updates_timestamp(app, make_user):
    """`updated_at` should advance on any successful change."""
    database = app._test_database
    u = make_user()
    # Force an old updated_at so the assertion is not flaky on fast systems.
    oid = _seed_order(
        database,
        user_id=u["id"],
        status="waiting",
        updated_at=int(time.time()) - 3600,
    )
    before = database.get_order(oid)["updated_at"]

    ok = database.update_order(oid, "processing")
    assert ok is True
    after = database.get_order(oid)["updated_at"]
    assert after > before


# ===========================================================================
# update_order — refund logic (V69.1 guard)
# ===========================================================================
def test_update_order_refunds_on_first_rejection(app, make_user):
    database = app._test_database
    u = make_user(balance=100.0)
    oid = _seed_order(database, user_id=u["id"], status="waiting", price=25.0)

    ok = database.update_order(oid, "rejected", note="out of stock")
    assert ok is True
    # The order's price was added back to the balance.
    assert _user_balance(database, u["id"]) == 125.0
    assert database.get_order(oid)["status"] == "rejected"


def test_update_order_no_double_refund_on_rejected_to_rejected(app, make_user):
    """rejected → rejected is a no-op; balance must NOT change."""
    database = app._test_database
    u = make_user(balance=100.0)
    # Plant the order already rejected (its price was already refunded
    # earlier in the legacy flow).
    oid = _seed_order(database, user_id=u["id"], status="rejected", price=25.0)

    ok = database.update_order(oid, "rejected", note="re-noted")
    # The transition guard allows no-op; we expect True (note got updated).
    assert ok is True
    # But NO additional refund.
    assert _user_balance(database, u["id"]) == 100.0


def test_update_order_blocks_completed_to_rejected(app, make_user):
    """V69.1: a completed order CANNOT be moved to rejected (would issue
    a refund for product the user already received)."""
    database = app._test_database
    u = make_user(balance=100.0)
    oid = _seed_order(database, user_id=u["id"], status="completed", price=25.0)

    ok = database.update_order(oid, "rejected")
    assert ok is False
    # Status untouched; balance untouched.
    assert database.get_order(oid)["status"] == "completed"
    assert _user_balance(database, u["id"]) == 100.0


def test_update_order_blocks_rejected_to_processing(app, make_user):
    """V69.1: rejected is terminal — no transition out except a no-op."""
    database = app._test_database
    u = make_user(balance=100.0)
    oid = _seed_order(database, user_id=u["id"], status="rejected", price=25.0)

    ok = database.update_order(oid, "processing")
    assert ok is False
    assert database.get_order(oid)["status"] == "rejected"


def test_update_order_returns_false_for_missing_id(app):
    database = app._test_database
    ok = database.update_order(99_999, "completed")
    assert ok is False
