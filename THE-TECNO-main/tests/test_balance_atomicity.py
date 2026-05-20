"""V74 — atomicity guarantees for balance credits in
``database.update_deposit`` (approve path) and ``database.update_order``
(reject/refund path).

Background
----------

Both functions used to follow a Python read-then-write pattern:

    user = s.get(User, dep.user_id)
    if user is not None:
        user.balance = (user.balance or 0) + amount

That is *not* atomic: two threads (or two admin sessions) acting on the
same user could both load ``balance=0`` before either flushed, then each
write ``balance=amount``, dropping one of the deltas. Postgres exposes
this loss because writers don't serialize the way SQLite WAL does on a
single file.

The fix is to do the increment at SQL evaluation time:

    s.execute(
        update(User)
        .where(User.id == ...)
        .values(balance=func.coalesce(User.balance, 0) + amount)
    )

The same atomic pattern is already used (correctly) by
``database.change_balance``.

What this module covers
-----------------------

  * **Serial parity** — N back-to-back approvals/rejections accumulate
    exactly. Would have passed against the old code too; included as a
    sanity net to catch sign / rounding regressions.
  * **Concurrent safety** — many threads acting on the *same* user
    must produce ``balance == sum(amounts)``. Under the previous
    Python read-then-write, this test fails most of the time on any
    platform that isn't the single-writer SQLite WAL we ran prod on
    before the Postgres migration.

SQLite + threads
----------------

The pytest harness uses an isolated SQLite file. Concurrent writers on
the same file without ``busy_timeout`` raise ``database is locked``
immediately. We register a SQLAlchemy connect hook on the engine so
every fresh connection sets ``busy_timeout=15000`` and ``journal_mode
=WAL`` — matching what the legacy ``database.connect()`` would have
done on a real-world SQLite deployment, and what Postgres would do
implicitly via row-level locks. The fix being validated does not
depend on these pragmas — they only stop the SQLite driver from
crying "locked" before the race we want to observe even has a chance
to play out.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import event


# ===========================================================================
# Shared helpers
# ===========================================================================
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
    """Insert / replace a row in ``payment_methods`` so create_deposit
    accepts it. Tests that don't go through create_deposit don't strictly
    need this, but the deposits.method column is a free-text foreign key
    in practice so we set it for completeness."""
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


def _seed_pending_deposit(
    database,
    *,
    user_id,
    amount=10.0,
    method="USDT",
    currency="USD",
    amount_usd=None,
    code_suffix="",
):
    """Insert one ``deposits`` row in status='pending' and return its id.

    Bypasses ``create_deposit`` so we don't trip the V69 60-second
    same-amount dedup window when seeding many rows for one user.
    """
    if amount_usd is None:
        amount_usd = amount if currency == "USD" else 0
    deposit_code = (
        f"DEPtest-{int(time.time() * 1_000_000)}-{user_id}-{code_suffix}"
    )
    created_at = int(time.time())
    conn = database.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO deposits (
                deposit_code, user_id, amount, method, proof, status,
                created_at, currency, amount_usd, proof_filename
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                deposit_code, user_id, amount, method, None,
                created_at, currency, amount_usd, None,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _seed_waiting_order(
    database,
    *,
    user_id,
    price=10.0,
    code_suffix="",
):
    """Insert one ``orders`` row in status='waiting' so update_order's
    V69.1 transition guard allows the rejected transition."""
    order_code = (
        f"ORDtest-{int(time.time() * 1_000_000)}-{user_id}-{code_suffix}"
    )
    now = int(time.time())
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
                order_code, user_id, "server2", "pubg", "PUBG",
                1, "60 UC", "111111", price, "waiting",
                None, None, now, now,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SQLite concurrency setup — make multiple writers tolerant of contention
# without papering over the actual race we're testing.
# ---------------------------------------------------------------------------
@pytest.fixture
def configure_sqlite_for_threading(app):
    """Register a connect hook on the SQLAlchemy engine so each fresh
    connection enables WAL + a 15s busy_timeout. Without this, concurrent
    SQLAlchemy writers immediately raise ``database is locked`` and the
    test fails for the wrong reason."""
    from app.db import base as _db_base

    eng = _db_base.engine

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - hook
        try:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()
        except Exception:
            pass

    yield

    try:
        event.remove(eng, "connect", _set_sqlite_pragmas)
    except Exception:
        pass


# ===========================================================================
# Serial parity — would have passed against the old code too. Catches
# sign / rounding regressions in the rewrite.
# ===========================================================================
def test_update_deposit_serial_credits_sum_of_amounts(app, make_user):
    """Approving N pending deposits in sequence credits the user's
    balance with the SUM of their amount_usd values."""
    database = app._test_database
    u = make_user(balance=0.0)
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    amounts = [3.0, 7.5, 1.25]
    deposit_ids = [
        _seed_pending_deposit(
            database, user_id=u["id"], amount=a,
            currency="USD", amount_usd=a, code_suffix=f"s{i}",
        )
        for i, a in enumerate(amounts)
    ]

    for did in deposit_ids:
        assert database.update_deposit(did, "approved") is True

    assert _user_balance(database, u["id"]) == pytest.approx(sum(amounts))


def test_update_order_serial_refunds_sum_of_prices(app, make_user):
    """Rejecting N waiting orders in sequence refunds the SUM of their
    prices to the user's balance."""
    database = app._test_database
    u = make_user(balance=0.0)

    prices = [5.0, 12.25, 0.75]
    order_ids = [
        _seed_waiting_order(
            database, user_id=u["id"], price=p, code_suffix=f"s{i}",
        )
        for i, p in enumerate(prices)
    ]

    for oid in order_ids:
        assert database.update_order(oid, "rejected") is True

    assert _user_balance(database, u["id"]) == pytest.approx(sum(prices))


def test_update_deposit_starts_from_existing_balance(app, make_user):
    """Approval is additive: starting balance is preserved, deltas
    sum on top of it."""
    database = app._test_database
    u = make_user(balance=100.0)
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    did = _seed_pending_deposit(
        database, user_id=u["id"], amount=25.0,
        currency="USD", amount_usd=25.0, code_suffix="base",
    )
    assert database.update_deposit(did, "approved") is True
    assert _user_balance(database, u["id"]) == pytest.approx(125.0)


def test_update_deposit_idempotency_guard_unchanged(app, make_user):
    """Re-approving an already-approved deposit must not double-credit.
    This is the legacy ``if dep.status != 'pending': return False``
    guard, kept intact by the V74 rewrite."""
    database = app._test_database
    u = make_user(balance=0.0)
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    did = _seed_pending_deposit(
        database, user_id=u["id"], amount=10.0,
        currency="USD", amount_usd=10.0, code_suffix="idem",
    )
    assert database.update_deposit(did, "approved") is True
    assert _user_balance(database, u["id"]) == pytest.approx(10.0)

    # Second call: the deposit is now in status='approved', not 'pending'
    # → must short-circuit and return False without touching the balance.
    assert database.update_deposit(did, "approved") is False
    assert _user_balance(database, u["id"]) == pytest.approx(10.0)


def test_update_order_v691_terminal_guard_unchanged(app, make_user):
    """Re-rejecting an already-rejected order must not double-refund.
    This is the V69.1 transition guard, preserved by the V74 rewrite."""
    database = app._test_database
    u = make_user(balance=0.0)

    oid = _seed_waiting_order(
        database, user_id=u["id"], price=20.0, code_suffix="term",
    )
    assert database.update_order(oid, "rejected") is True
    assert _user_balance(database, u["id"]) == pytest.approx(20.0)

    # Already in a terminal state and we're trying to keep it there
    # via a different transition: the guard must short-circuit.
    # rejected → completed is blocked entirely (terminal).
    assert database.update_order(oid, "completed") is False
    assert _user_balance(database, u["id"]) == pytest.approx(20.0)


# ===========================================================================
# Concurrent — N threads + barrier; final balance MUST equal sum(amounts)
# ===========================================================================
def _run_concurrent(workers_n, fn):
    """Spawn ``workers_n`` daemon threads, all blocked on a barrier so
    they release simultaneously, then join. Returns ``(results, errors)``
    parallel lists."""
    barrier = threading.Barrier(workers_n)
    results = [None] * workers_n
    errors = [None] * workers_n

    def runner(idx):
        try:
            barrier.wait(timeout=10)
            results[idx] = fn(idx)
        except Exception as exc:  # surfaced via assertions below
            errors[idx] = exc

    threads = [
        threading.Thread(target=runner, args=(i,), daemon=True)
        for i in range(workers_n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    return results, errors


def test_update_deposit_concurrent_no_lost_updates(
    app, make_user, configure_sqlite_for_threading
):
    """V74 race fix: 10 threads concurrently approve their own pending
    deposit for the SAME user. The final balance must equal the sum of
    all deposit amounts. Under the previous Python read-then-write
    pattern, two threads that both read balance=0 before either flushed
    would each write balance=amount, dropping one or more deltas — the
    classic lost-update.
    """
    database = app._test_database
    u = make_user(balance=0.0)
    _seed_payment_method(database, mid="usdt", name="USDT", currency="USD")

    n_threads = 10
    per_amount = 1.0
    deposit_ids = [
        _seed_pending_deposit(
            database, user_id=u["id"], amount=per_amount,
            currency="USD", amount_usd=per_amount, code_suffix=f"c{i}",
        )
        for i in range(n_threads)
    ]

    def approve(idx):
        return database.update_deposit(deposit_ids[idx], "approved")

    results, errors = _run_concurrent(n_threads, approve)

    assert errors == [None] * n_threads, f"thread errors: {errors!r}"
    assert results == [True] * n_threads, f"unexpected results: {results!r}"
    final = _user_balance(database, u["id"])
    expected = per_amount * n_threads
    assert final == pytest.approx(expected), (
        f"lost update detected: balance={final!r} expected={expected!r}; "
        "credits did not sum atomically"
    )


def test_update_order_concurrent_refund_no_lost_updates(
    app, make_user, configure_sqlite_for_threading
):
    """Same race shape as the deposit test, but on the refund path of
    ``update_order``: 10 threads each reject their own waiting order
    for the SAME user. Final balance must equal sum(prices).
    """
    database = app._test_database
    u = make_user(balance=0.0)

    n_threads = 10
    per_price = 1.0
    order_ids = [
        _seed_waiting_order(
            database, user_id=u["id"], price=per_price, code_suffix=f"c{i}",
        )
        for i in range(n_threads)
    ]

    def reject(idx):
        return database.update_order(order_ids[idx], "rejected")

    results, errors = _run_concurrent(n_threads, reject)

    assert errors == [None] * n_threads, f"thread errors: {errors!r}"
    assert results == [True] * n_threads, f"unexpected results: {results!r}"
    final = _user_balance(database, u["id"])
    expected = per_price * n_threads
    assert final == pytest.approx(expected), (
        f"lost update detected: balance={final!r} expected={expected!r}; "
        "refunds did not sum atomically"
    )
