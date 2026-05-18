"""V72 phase 3 / PR #1 — behavioural parity tests for the rewritten
``database.py`` helpers.

Scope: only the seven functions migrated in PR #1.

  * ``set_setting`` / ``get_setting``
  * ``wishlist_list`` / ``wishlist_has`` / ``wishlist_toggle``
  * ``list_payment_methods`` / ``get_payment_method`` /
    ``update_payment_method``

What we are guarding against
----------------------------

The ORM rewrite must be a perfect drop-in. Every failure mode below
would silently break a real route or template:

  1. **Return shape drift** — callers expect plain ``dict``s with the
     exact column names. We assert on the keys explicitly.
  2. **Default merging** — ``update_payment_method(..., name=None)``
     must keep the existing name. A naive ORM rewrite that overwrites
     unconditionally would corrupt admin-edited rows.
  3. **Bool semantics** — ``wishlist_toggle`` returns ``True`` on insert
     and ``False`` on delete; not just truthy/falsy.
  4. **Missing-row handling** — ``get_payment_method("does-not-exist")``
     must return ``None`` (not raise, not return an empty dict).
  5. **Default values** — ``get_setting("missing", "fallback")`` must
     return ``"fallback"`` exactly.

These tests use the existing ``app`` fixture from ``conftest.py`` so
they run against the seeded SQLite schema (default payment methods +
default settings rows are present).

If/when this entire suite passes against a Postgres-backed DB without
modification, the migration is structurally sound.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# settings: get_setting / set_setting
# ---------------------------------------------------------------------------
def test_get_setting_returns_seeded_value(app):
    """`init_db()` seeds `usd_syp_rate=15000`. Sanity-check the read path."""
    database = app._test_database
    assert database.get_setting("usd_syp_rate") == "15000"


def test_get_setting_default_when_missing(app):
    database = app._test_database
    assert database.get_setting("does_not_exist", "fallback") == "fallback"
    # Default is `None` if not provided, NOT a sentinel.
    assert database.get_setting("does_not_exist") is None


def test_set_setting_inserts_when_absent(app):
    database = app._test_database
    database.set_setting("brand_new_key", "v1")
    assert database.get_setting("brand_new_key") == "v1"


def test_set_setting_overwrites_existing(app):
    database = app._test_database
    database.set_setting("usd_syp_rate", "16000")
    assert database.get_setting("usd_syp_rate") == "16000"


def test_set_setting_coerces_to_string(app):
    """Legacy contract: any non-string value is `str()`-ified before
    storage. Templates always read settings as strings."""
    database = app._test_database
    database.set_setting("a_number", 42)
    database.set_setting("a_float", 3.14)
    database.set_setting("a_bool", True)
    assert database.get_setting("a_number") == "42"
    assert database.get_setting("a_float") == "3.14"
    assert database.get_setting("a_bool") == "True"


# ---------------------------------------------------------------------------
# payment_methods: list / get / update
# ---------------------------------------------------------------------------
PAYMENT_METHOD_KEYS = {
    "id", "name", "emoji", "address", "instructions", "active", "currency",
}


def test_list_payment_methods_returns_default_seeds(app):
    """`init_db()` seeds 6 default methods; they must all come back."""
    database = app._test_database
    methods = database.list_payment_methods()
    assert isinstance(methods, list)
    assert len(methods) == 6
    ids = {m["id"] for m in methods}
    assert ids == {"usdt", "binance", "sham_syr", "sham_usd", "syriatel", "center"}


def test_list_payment_methods_dict_keys_match_legacy(app):
    """Every row dict has exactly the columns templates iterate over."""
    database = app._test_database
    methods = database.list_payment_methods()
    for m in methods:
        # `>=` rather than `==` to forgive future column additions; what
        # we care about is that nothing existing went missing.
        assert PAYMENT_METHOD_KEYS.issubset(m.keys()), (
            f"Method {m.get('id')!r} missing keys: "
            f"{PAYMENT_METHOD_KEYS - set(m.keys())}"
        )


def test_list_payment_methods_only_active_filter(app):
    """`only_active=True` must hide rows where `active=0`."""
    database = app._test_database
    # Deactivate one method and verify it disappears.
    database.update_payment_method("center", active=False)
    active_ids = {m["id"] for m in database.list_payment_methods(only_active=True)}
    assert "center" not in active_ids
    all_ids = {m["id"] for m in database.list_payment_methods()}
    assert "center" in all_ids  # still listed when filter is off


def test_list_payment_methods_ordered_by_name(app):
    database = app._test_database
    methods = database.list_payment_methods()
    names = [m["name"] for m in methods]
    assert names == sorted(names)


def test_get_payment_method_returns_dict(app):
    database = app._test_database
    m = database.get_payment_method("usdt")
    assert m is not None
    assert m["id"] == "usdt"
    assert m["currency"] == "USD"
    assert PAYMENT_METHOD_KEYS.issubset(m.keys())


def test_get_payment_method_missing_returns_none(app):
    database = app._test_database
    assert database.get_payment_method("nope") is None


def test_update_payment_method_partial_update(app):
    """Passing `name=...` updates name only; other fields are preserved."""
    database = app._test_database
    before = database.get_payment_method("usdt")
    ok = database.update_payment_method("usdt", name="USDT (Tron)")
    assert ok is True
    after = database.get_payment_method("usdt")
    assert after["name"] == "USDT (Tron)"
    # Unchanged fields keep their old values.
    assert after["emoji"] == before["emoji"]
    assert after["address"] == before["address"]
    assert after["instructions"] == before["instructions"]
    assert after["active"] == before["active"]
    assert after["currency"] == before["currency"]


def test_update_payment_method_active_flag_coerced(app):
    """The legacy contract is `1 if active else 0` — we must replicate it
    so DB rows stay INTEGER (admin templates compare to 1/0)."""
    database = app._test_database
    database.update_payment_method("usdt", active=False)
    assert database.get_payment_method("usdt")["active"] == 0
    database.update_payment_method("usdt", active=True)
    assert database.get_payment_method("usdt")["active"] == 1
    # Truthy non-bool still flips to 1.
    database.update_payment_method("usdt", active="yes")
    assert database.get_payment_method("usdt")["active"] == 1


def test_update_payment_method_missing_returns_false(app):
    database = app._test_database
    assert database.update_payment_method("nope", name="ignored") is False


# ---------------------------------------------------------------------------
# wishlist_*
# ---------------------------------------------------------------------------
@pytest.fixture()
def user(app, make_user):
    return make_user(email="wishuser@test.local")


def test_wishlist_starts_empty(app, user):
    database = app._test_database
    assert database.wishlist_list(user["id"]) == []
    assert database.wishlist_has(user["id"], "server2", "pubg") is False


def test_wishlist_toggle_add_then_remove(app, user):
    database = app._test_database
    added = database.wishlist_toggle(user["id"], "server2", "pubg")
    assert added is True
    assert database.wishlist_has(user["id"], "server2", "pubg") is True

    removed = database.wishlist_toggle(user["id"], "server2", "pubg")
    assert removed is False
    assert database.wishlist_has(user["id"], "server2", "pubg") is False


def test_wishlist_list_returns_legacy_dict_shape(app, user):
    """The dict shape (provider, game_key, created_at, name, image_url)
    must match what the now-removed wishlist template expected."""
    database = app._test_database
    database.wishlist_toggle(user["id"], "server2", "pubg")
    rows = database.wishlist_list(user["id"])
    assert len(rows) == 1
    row = rows[0]
    expected_keys = {"provider", "game_key", "created_at", "name", "image_url"}
    assert set(row.keys()) == expected_keys
    assert row["provider"] == "server2"
    assert row["game_key"] == "pubg"
    # `name` and `image_url` come from the LEFT JOIN with `games` — the
    # game does not exist in this test, so both are None. The legacy
    # implementation also returned None for these (sqlite LEFT JOIN miss).
    assert row["name"] is None
    assert row["image_url"] is None


def test_wishlist_list_orders_newest_first(app, user, monkeypatch):
    """Items must come back sorted by `created_at` DESC."""
    import time as _t
    database = app._test_database

    # Force three distinct timestamps by stubbing time.time().
    timestamps = iter([1000, 2000, 3000])
    monkeypatch.setattr(_t, "time", lambda: next(timestamps))

    database.wishlist_toggle(user["id"], "server2", "pubg")
    database.wishlist_toggle(user["id"], "server1", "ff")
    database.wishlist_toggle(user["id"], "server2", "ml")

    rows = database.wishlist_list(user["id"])
    assert [r["game_key"] for r in rows] == ["ml", "ff", "pubg"]


def test_wishlist_unique_per_user_game_pair(app, user):
    """Toggling does NOT raise on duplicates — it just removes the row.
    This guards against an ORM rewrite that did a blind INSERT."""
    database = app._test_database
    database.wishlist_toggle(user["id"], "s", "g")
    # Round-trip 5 more times. Should always end up empty or with one row.
    for _ in range(5):
        database.wishlist_toggle(user["id"], "s", "g")
    assert database.wishlist_has(user["id"], "s", "g") in (True, False)
    # Whatever the parity, there is at most one row.
    rows = database.wishlist_list(user["id"])
    assert len(rows) <= 1
