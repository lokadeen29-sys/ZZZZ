"""V72 phase 3 / PR #4 — behavioural parity tests for the rewritten
deposit helpers in ``database.py``.

Scope: the five deposit-related functions migrated in PR #4.

  * ``create_deposit(user_id, amount, method_id, proof,
                     amount_usd=None, proof_filename=None)``
  * ``list_deposits_for_user(user_id)``
  * ``list_deposits(status=None)``
  * ``get_deposit(deposit_id)``
  * ``update_deposit(deposit_id, status)``

What we are guarding against
----------------------------

These functions back the wallet/deposit page, the admin deposits queue,
and the Approve/Reject buttons. The two highest-risk pieces are:

  * ``create_deposit``'s **V69 dedup window** + **V49-HOTFIX
    server-side amount_usd recomputation**. If we drop dedup, admins
    drown in duplicate review work; if we drop the recomputation, a
    5000-SYP deposit credits $5000 to the wallet.
  * ``update_deposit``'s **idempotent approval** — admins double-click
    Approve/Reject all the time; the row's status filter must catch
    that and return False without crediting the user twice.
"""

from __future__ import annotations

import time

import pytest


# ===========================================================================
# Shared helpers
# ===========================================================================
DEPOSIT_COLUMNS = {
    "id", "deposit_code", "user_id", "amount", "method", "proof",
    "status", "created_at", "currency", "amount_usd", "proof_filename",
}


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


def _seed_payment_method(database, *, mid="usdt", name="USDT",
                         currency="USD", active=1):
    """Insert a payment_methods row used by create_deposit.

    The default fixture seeds the standard set; tests that need a SYP-
    denominated method create one explicitly.
    """
    conn = database.connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO payment_methods
                (id, name, emoji, address, instructions, active, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (mid, name, "💳", "addr", "ins", active, currency),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_deposit(
    database,
    *,
    user_id,
    amount=10.0,
    method="USDT",
    status="pending",
    currency="USD",
    amount_usd=None,
    deposit_code=None,
    created_at=None,
    proof=None,
    proof_filename=None,
):
    """Direct DB insert into ``deposits`` — bypasses create_deposit so
    tests can plant arbitrary status / currency / created_at."""
    if deposit_code is None:
        deposit_code = f"DEPtest-{int(time.time() * 1000)}-{user_id}-{status}"
    if created_at is None:
        created_at = int(time.time())
    if amount_usd is None:
        amount_usd = amount if currency == "USD" else 0

    conn = database.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO deposits (
                deposit_code, user_id, amount, method, proof, status,
                created_at, currency, amount_usd, proof_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deposit_code,
                user_id,
                amount,
                method,
                proof,
                status,
                created_at,
                currency,
                amount_usd,
                proof_filename,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ===========================================================================
# create_deposit
# ===========================================================================
def test_create_deposit_unknown_method_returns_none(app, make_user):
    database = app._test_database
    u = make_user()
    assert database.create_deposit(u["id"], 10, "no_such_method", None) is None


def test_create_deposit_inserts_row_and_returns_tuple(app, make_user):
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    result = database.create_deposit(u["id"], 25.5, "usdt", None)
    assert result is not None
    dep_id, code = result
    assert isinstance(dep_id, int)
    assert isinstance(code, str)
    assert code.startswith("DEP") and len(code) > 5

    dep = database.get_deposit(dep_id)
    assert dep is not None
    assert DEPOSIT_COLUMNS.issubset(dep.keys()), (
        f"missing legacy keys: {DEPOSIT_COLUMNS - set(dep.keys())}"
    )
    assert dep["user_id"] == u["id"]
    assert dep["amount"] == 25.5
    assert dep["method"] == "USDT"
    assert dep["status"] == "pending"
    assert dep["currency"] == "USD"
    # USD method => amount_usd == amount.
    assert dep["amount_usd"] == 25.5


def test_create_deposit_recomputes_amount_usd_for_syp_method(app, make_user):
    """V49-HOTFIX: a 5000-SYP deposit must store amount_usd as the
    converted value, not as 5000 USD. The caller's amount_usd kwarg
    is intentionally ignored."""
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="syriatel", name="Syriatel", currency="SYP")
    database.set_setting("usd_syp_rate", "10000")

    # Caller maliciously passes amount_usd=5000 — server must override.
    result = database.create_deposit(
        u["id"], 5000, "syriatel", None, amount_usd=5000
    )
    assert result is not None
    dep_id, _ = result
    dep = database.get_deposit(dep_id)
    assert dep["currency"] == "SYP"
    assert dep["amount"] == 5000
    # 5000 / 10000 = 0.5 USD
    assert dep["amount_usd"] == pytest.approx(0.5)


def test_create_deposit_dedupes_within_60s_window(app, make_user):
    """V69: same-user, same-method, same-amount, pending, within 60s
    returns the existing row unchanged."""
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    a_id, a_code = database.create_deposit(u["id"], 10, "usdt", None)
    b_id, b_code = database.create_deposit(u["id"], 10, "usdt", None)

    assert (a_id, a_code) == (b_id, b_code)
    assert len(database.list_deposits_for_user(u["id"])) == 1


def test_create_deposit_dedup_respects_amount_tolerance(app, make_user):
    """ABS(amount - ?) < 0.005 — different amounts must NOT dedup."""
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    a_id, _ = database.create_deposit(u["id"], 10.0, "usdt", None)
    b_id, _ = database.create_deposit(u["id"], 10.5, "usdt", None)
    assert a_id != b_id


def test_create_deposit_dedup_skips_after_window(app, make_user):
    """A pending deposit older than 60s must NOT be reused."""
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    # Plant an "old" pending deposit (90s ago).
    old_id = _seed_deposit(
        database,
        user_id=u["id"],
        amount=10,
        method="USDT",
        currency="USD",
        amount_usd=10,
        created_at=int(time.time()) - 90,
    )
    new_id, _ = database.create_deposit(u["id"], 10, "usdt", None)
    assert new_id != old_id


def test_create_deposit_dedup_only_matches_pending(app, make_user):
    """An approved or rejected deposit in the window must NOT be reused."""
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    # Plant a recent NON-pending deposit.
    _seed_deposit(
        database,
        user_id=u["id"],
        amount=10,
        method="USDT",
        currency="USD",
        amount_usd=10,
        status="approved",
    )
    new_id, _ = database.create_deposit(u["id"], 10, "usdt", None)
    new = database.get_deposit(new_id)
    assert new["status"] == "pending"


def test_create_deposit_zero_amount_skips_dedup(app, make_user):
    """The legacy code only ran the dedup query when amount > 0."""
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    a_id, _ = database.create_deposit(u["id"], 0, "usdt", None)
    b_id, _ = database.create_deposit(u["id"], 0, "usdt", None)
    # Two zero-amount deposits are kept separate (no spurious dedup).
    assert a_id != b_id


def test_create_deposit_persists_proof_and_filename(app, make_user):
    database = app._test_database
    u = make_user()
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    dep_id, _ = database.create_deposit(
        u["id"], 5, "usdt", "txid-abc", proof_filename="proof.png"
    )
    dep = database.get_deposit(dep_id)
    assert dep["proof"] == "txid-abc"
    assert dep["proof_filename"] == "proof.png"


# ===========================================================================
# list_deposits_for_user
# ===========================================================================
def test_list_deposits_for_user_isolates_owner(app, make_user):
    database = app._test_database
    alice = make_user(email="alice@test.local")
    bob = make_user(email="bob@test.local")
    _seed_deposit(database, user_id=alice["id"], deposit_code="DEPa1")
    _seed_deposit(database, user_id=bob["id"], deposit_code="DEPb1")

    rows = database.list_deposits_for_user(alice["id"])
    assert {r["deposit_code"] for r in rows} == {"DEPa1"}


def test_list_deposits_for_user_sorted_newest_first(app, make_user):
    database = app._test_database
    u = make_user()
    ids = [
        _seed_deposit(database, user_id=u["id"], deposit_code=f"DEPseq-{i}")
        for i in range(5)
    ]
    rows = database.list_deposits_for_user(u["id"])
    assert [r["id"] for r in rows] == list(reversed(ids))


def test_list_deposits_for_user_caps_at_200(app, make_user):
    database = app._test_database
    u = make_user()
    for i in range(220):
        _seed_deposit(database, user_id=u["id"], deposit_code=f"DEPcap-{i}")

    rows = database.list_deposits_for_user(u["id"])
    assert len(rows) == 200


def test_list_deposits_for_user_empty(app, make_user):
    database = app._test_database
    u = make_user()
    assert database.list_deposits_for_user(u["id"]) == []


def test_list_deposits_for_user_returns_full_dict_shape(app, make_user):
    database = app._test_database
    u = make_user()
    _seed_deposit(database, user_id=u["id"])
    rows = database.list_deposits_for_user(u["id"])
    assert len(rows) == 1
    assert DEPOSIT_COLUMNS.issubset(rows[0].keys())


# ===========================================================================
# list_deposits (admin queue, with JOIN)
# ===========================================================================
def test_list_deposits_no_filter_returns_all_with_user_columns(app, make_user):
    database = app._test_database
    alice = make_user(email="alice@test.local")
    bob = make_user(email="bob@test.local")
    _seed_deposit(database, user_id=alice["id"], deposit_code="DEPa")
    _seed_deposit(database, user_id=bob["id"], deposit_code="DEPb")

    rows = database.list_deposits()
    assert len(rows) == 2
    by_code = {r["deposit_code"]: r for r in rows}
    # Joined columns the legacy SQL produced.
    assert by_code["DEPa"]["user_email"] == "alice@test.local"
    assert by_code["DEPb"]["user_email"] == "bob@test.local"
    # And the user_name field is present (default fixture name).
    assert "user_name" in rows[0]


def test_list_deposits_status_filter(app, make_user):
    database = app._test_database
    u = make_user()
    _seed_deposit(database, user_id=u["id"], status="pending", deposit_code="DEPp1")
    _seed_deposit(database, user_id=u["id"], status="approved", deposit_code="DEPa1")
    _seed_deposit(database, user_id=u["id"], status="rejected", deposit_code="DEPr1")

    rows = database.list_deposits(status="pending")
    assert {r["deposit_code"] for r in rows} == {"DEPp1"}


def test_list_deposits_no_filter_caps_at_200(app, make_user):
    database = app._test_database
    u = make_user()
    for i in range(210):
        _seed_deposit(database, user_id=u["id"], deposit_code=f"DEPmany-{i}")
    rows = database.list_deposits()
    assert len(rows) == 200


def test_list_deposits_with_status_is_uncapped(app, make_user):
    """Admin processing queue needs the full set; legacy SQL omits LIMIT."""
    database = app._test_database
    u = make_user()
    for i in range(210):
        _seed_deposit(
            database,
            user_id=u["id"],
            status="pending",
            deposit_code=f"DEPup-{i}",
        )
    rows = database.list_deposits(status="pending")
    assert len(rows) == 210


def test_list_deposits_sorted_newest_first(app, make_user):
    database = app._test_database
    u = make_user()
    ids = [
        _seed_deposit(database, user_id=u["id"], deposit_code=f"DEPord-{i}")
        for i in range(4)
    ]
    rows = database.list_deposits()
    assert [r["id"] for r in rows] == list(reversed(ids))


# ===========================================================================
# get_deposit
# ===========================================================================
def test_get_deposit_returns_full_dict(app, make_user):
    database = app._test_database
    u = make_user()
    did = _seed_deposit(database, user_id=u["id"], amount=12.5)

    d = database.get_deposit(did)
    assert d is not None
    assert isinstance(d, dict)
    assert DEPOSIT_COLUMNS.issubset(d.keys())
    assert d["id"] == did
    assert d["user_id"] == u["id"]
    assert d["amount"] == 12.5
    assert d["status"] == "pending"


def test_get_deposit_missing(app):
    database = app._test_database
    assert database.get_deposit(99_999) is None


def test_get_deposit_accepts_string_id(app, make_user):
    database = app._test_database
    u = make_user()
    did = _seed_deposit(database, user_id=u["id"])
    assert database.get_deposit(str(did))["id"] == did


def test_get_deposit_invalid_id(app):
    database = app._test_database
    assert database.get_deposit("not-a-number") is None
    assert database.get_deposit(None) is None


# ===========================================================================
# update_deposit
# ===========================================================================
def test_update_deposit_approve_credits_amount_usd(app, make_user):
    """V49-HOTFIX: balance is credited with the precomputed amount_usd,
    not re-converted from amount today."""
    database = app._test_database
    u = make_user(balance=0.0)
    did = _seed_deposit(
        database,
        user_id=u["id"],
        amount=5000,
        currency="SYP",
        amount_usd=0.5,  # Locked in at submission time.
    )
    # Move the live rate after submission — must NOT affect the credit.
    database.set_setting("usd_syp_rate", "1")

    ok = database.update_deposit(did, "approved")
    assert ok is True
    assert database.get_deposit(did)["status"] == "approved"
    # Credited with the locked-in 0.5 USD, NOT 5000 (the new rate).
    assert _user_balance(database, u["id"]) == pytest.approx(0.5)


def test_update_deposit_approve_falls_back_to_amount_when_no_usd(app, make_user):
    """Legacy deposits with amount_usd missing/0 fall back to converting
    the paid amount via _amount_to_usd."""
    database = app._test_database
    u = make_user(balance=0.0)
    database.set_setting("usd_syp_rate", "10000")
    did = _seed_deposit(
        database,
        user_id=u["id"],
        amount=20000,  # 20k SYP
        currency="SYP",
        amount_usd=0,  # legacy/missing
    )
    ok = database.update_deposit(did, "approved")
    assert ok is True
    # 20000 / 10000 = 2 USD.
    assert _user_balance(database, u["id"]) == pytest.approx(2.0)


def test_update_deposit_approve_usd_method(app, make_user):
    """USD methods credit amount_usd (== amount) directly."""
    database = app._test_database
    u = make_user(balance=10.0)
    did = _seed_deposit(
        database, user_id=u["id"], amount=15, currency="USD", amount_usd=15
    )
    ok = database.update_deposit(did, "approved")
    assert ok is True
    assert _user_balance(database, u["id"]) == pytest.approx(25.0)


def test_update_deposit_reject_does_not_credit(app, make_user):
    database = app._test_database
    u = make_user(balance=10.0)
    did = _seed_deposit(
        database, user_id=u["id"], amount=15, currency="USD", amount_usd=15
    )
    ok = database.update_deposit(did, "rejected")
    assert ok is True
    assert database.get_deposit(did)["status"] == "rejected"
    assert _user_balance(database, u["id"]) == 10.0


def test_update_deposit_idempotent_double_approve(app, make_user):
    """Admin double-clicks Approve — second call returns False, balance
    must NOT be credited twice (the legacy idempotency guard)."""
    database = app._test_database
    u = make_user(balance=0.0)
    did = _seed_deposit(
        database, user_id=u["id"], amount=10, currency="USD", amount_usd=10
    )
    assert database.update_deposit(did, "approved") is True
    assert database.update_deposit(did, "approved") is False
    assert _user_balance(database, u["id"]) == pytest.approx(10.0)


def test_update_deposit_blocks_change_after_terminal(app, make_user):
    """Approved/rejected are terminal — flipping must return False and
    leave both status and balance untouched."""
    database = app._test_database
    u = make_user(balance=10.0)
    did = _seed_deposit(
        database,
        user_id=u["id"],
        amount=15,
        currency="USD",
        amount_usd=15,
        status="approved",
    )
    ok = database.update_deposit(did, "rejected")
    assert ok is False
    assert database.get_deposit(did)["status"] == "approved"
    assert _user_balance(database, u["id"]) == 10.0


def test_update_deposit_missing_returns_false(app):
    database = app._test_database
    assert database.update_deposit(99_999, "approved") is False
