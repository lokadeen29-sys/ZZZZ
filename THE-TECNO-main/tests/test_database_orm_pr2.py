"""V72 phase 3 / PR #2 — behavioural parity tests for the rewritten
read helpers in ``database.py``.

Scope: only the five functions migrated in PR #2.

  * ``get_user(user_id)``
  * ``get_game(provider, game_key)``
  * ``list_games(provider=None, only_active=True)``
  * ``get_product(product_id)``
  * ``list_products(provider, game_key, only_active=True, group_id=None)``

What we are guarding against
----------------------------

These functions feed the public catalog, the user dashboard and the
admin product editor. A silent shape-drift would either break the
templates outright (KeyError) or — much worse — quietly hide / show
the wrong rows. The tests below assert on:

  1. **Return type & dict shape** — explicit key-set assertions, so
     adding/removing a column on the model surfaces as a test failure
     instead of a route-level surprise.
  2. **None semantics for missing rows** — every ``get_*`` returns
     ``None`` (not ``{}`` or an exception) when the row is absent.
  3. **Filters** — ``list_games(provider="x")`` and
     ``list_games(only_active=...)`` apply correctly; ``get_product``
     hides inactive rows.
  4. **Curated-subset rule for ``list_products``** — when at least one
     active product has a positive ``sort_order``, the curated subset
     is returned; otherwise the full active list. This rule is the
     single most fragile piece of the ORM rewrite.
  5. **Ordering** — ``list_products`` pushes ``sort_order=0`` rows to
     the end and breaks ties on ``sell_price`` then ``id``.
  6. **``display_name`` injection** — every dict from
     ``list_products`` has a translated ``display_name`` field.

If/when this entire suite passes against a Postgres-backed DB without
modification, the migration is structurally sound for these read paths.
"""

from __future__ import annotations

import pytest


# ===========================================================================
# Helpers — build a tiny products / games dataset directly in the per-test
# SQLite file. We deliberately avoid going through the high-level helpers
# (upsert_product / add_custom_game) for the columns they don't expose
# (sort_order, image_url) so the tests stay close to the production schema.
# ===========================================================================
def _seed_game(database, provider, game_key, name, *, emoji="🎮", image_url="", active=1):
    """Insert a row into ``games`` with full control over every column."""
    conn = database.connect()
    try:
        conn.execute(
            """
            INSERT INTO games (provider, game_key, name, emoji, image_url, active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (provider, game_key, name, emoji, image_url, active),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_product(
    database,
    provider,
    game_key,
    provider_product_id,
    name,
    *,
    base_price=1.0,
    sell_price=2.0,
    sort_order=0,
    active=1,
    group_id=None,
):
    """Insert a row into ``products`` with full control over every column."""
    conn = database.connect()
    try:
        conn.execute(
            """
            INSERT INTO products
            (provider, game_key, provider_product_id, name,
             base_price, sell_price, sort_order, active, group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                game_key,
                str(provider_product_id),
                name,
                base_price,
                sell_price,
                sort_order,
                active,
                group_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# Column sets (taken from `database._init_db_inner` + the ALTER TABLE
# upgrades that fire on `init_db()`). We assert subset-inclusion so that
# adding NEW columns later does not break this test.
USER_COLUMNS = {
    "id", "name", "email", "phone", "password_hash", "role", "balance",
    "active", "email_verified", "created_at",
    # added by ALTER on init_db()
    "google_sub", "session_version",
}
GAME_COLUMNS = {
    "id", "provider", "game_key", "name", "emoji", "image_url", "active",
    "pricing_currency", "show_on_home", "home_sort_order",
}
PRODUCT_COLUMNS = {
    "id", "provider", "game_key", "provider_product_id", "name",
    "base_price", "sell_price", "sort_order", "active", "group_id",
    "fixed_syp_price", "pricing_mode", "manual_price_syp",
}


# ===========================================================================
# get_user
# ===========================================================================
def test_get_user_returns_dict_with_legacy_columns(app, make_user):
    database = app._test_database
    u = make_user(email="getuser@test.local", balance=12.5)

    fetched = database.get_user(u["id"])
    assert fetched is not None
    assert isinstance(fetched, dict)
    # Every legacy column we care about must be present.
    assert USER_COLUMNS.issubset(fetched.keys()), (
        f"missing legacy keys: {USER_COLUMNS - set(fetched.keys())}"
    )
    assert fetched["id"] == u["id"]
    assert fetched["email"] == "getuser@test.local"
    assert fetched["balance"] == 12.5
    assert fetched["role"] == "user"
    assert fetched["active"] == 1


def test_get_user_returns_none_when_missing(app):
    database = app._test_database
    assert database.get_user(99_999) is None


def test_get_user_accepts_string_id(app, make_user):
    """Routes occasionally pass a string from `request.args` — the legacy
    SQLite implementation coped via implicit cast; the ORM rewrite must
    do the same."""
    database = app._test_database
    u = make_user(email="strid@test.local")
    fetched = database.get_user(str(u["id"]))
    assert fetched is not None
    assert fetched["id"] == u["id"]


def test_get_user_returns_none_for_invalid_id(app):
    database = app._test_database
    assert database.get_user("not-a-number") is None
    assert database.get_user(None) is None


# ===========================================================================
# get_game
# ===========================================================================
def test_get_game_returns_dict_with_legacy_columns(app):
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG Mobile", emoji="🎯", image_url="/x.png")

    g = database.get_game("server2", "pubg")
    assert g is not None
    assert isinstance(g, dict)
    assert GAME_COLUMNS.issubset(g.keys()), (
        f"missing legacy keys: {GAME_COLUMNS - set(g.keys())}"
    )
    assert g["provider"] == "server2"
    assert g["game_key"] == "pubg"
    assert g["name"] == "PUBG Mobile"
    assert g["emoji"] == "🎯"
    assert g["image_url"] == "/x.png"
    assert g["active"] == 1


def test_get_game_returns_none_when_missing(app):
    database = app._test_database
    assert database.get_game("server2", "no-such-game") is None
    assert database.get_game("nope", "pubg") is None


def test_get_game_returns_inactive_row_unchanged(app):
    """Unlike `get_product`, `get_game` does NOT filter on `active`. The
    admin "edit game" page relies on this (you must be able to load an
    inactive game to re-enable it)."""
    database = app._test_database
    _seed_game(database, "server2", "freezer", "Freezer", active=0)
    g = database.get_game("server2", "freezer")
    assert g is not None
    assert g["active"] == 0


# ===========================================================================
# list_games
# ===========================================================================
def test_list_games_returns_empty_when_no_rows(app):
    database = app._test_database
    assert database.list_games() == []


def test_list_games_default_only_active(app):
    database = app._test_database
    _seed_game(database, "server2", "a-active", "A Active", active=1)
    _seed_game(database, "server2", "b-inactive", "B Inactive", active=0)

    rows = database.list_games()
    keys = {(r["provider"], r["game_key"]) for r in rows}
    assert ("server2", "a-active") in keys
    assert ("server2", "b-inactive") not in keys


def test_list_games_with_only_active_false_includes_inactive(app):
    database = app._test_database
    _seed_game(database, "server2", "a-active", "A Active", active=1)
    _seed_game(database, "server2", "b-inactive", "B Inactive", active=0)

    rows = database.list_games(only_active=False)
    keys = {(r["provider"], r["game_key"]) for r in rows}
    assert ("server2", "a-active") in keys
    assert ("server2", "b-inactive") in keys


def test_list_games_provider_filter(app):
    database = app._test_database
    _seed_game(database, "server1", "g1", "Game One")
    _seed_game(database, "server2", "g2", "Game Two")

    rows = database.list_games(provider="server1")
    assert {r["game_key"] for r in rows} == {"g1"}


def test_list_games_ordered_active_desc_then_name_asc(app):
    """Order contract: active DESC, name ASC, id ASC. Tested by
    constructing a deliberately tangled set."""
    database = app._test_database
    _seed_game(database, "server2", "z", "Zeta", active=1)
    _seed_game(database, "server2", "a", "Alpha", active=1)
    _seed_game(database, "server2", "i", "Inactive Game", active=0)
    _seed_game(database, "server2", "b", "Beta", active=1)

    rows = database.list_games(only_active=False)
    # All actives come before any inactive.
    actives = [r["name"] for r in rows if r["active"] == 1]
    inactives = [r["name"] for r in rows if r["active"] == 0]
    assert actives == ["Alpha", "Beta", "Zeta"]
    assert inactives == ["Inactive Game"]
    # And in the full list, the actives appear before the inactives.
    active_indexes = [i for i, r in enumerate(rows) if r["active"] == 1]
    inactive_indexes = [i for i, r in enumerate(rows) if r["active"] == 0]
    assert max(active_indexes) < min(inactive_indexes)


def test_list_games_dict_keys_match_legacy(app):
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")

    rows = database.list_games()
    assert len(rows) == 1
    assert GAME_COLUMNS.issubset(rows[0].keys()), (
        f"missing legacy keys: {GAME_COLUMNS - set(rows[0].keys())}"
    )


# ===========================================================================
# get_product
# ===========================================================================
def test_get_product_returns_dict_with_legacy_columns(app):
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "ext-1", "60 UC",
                  base_price=0.9, sell_price=1.2, sort_order=10)

    # Resolve product id via list_products (covered separately).
    products = database.list_products("server2", "pubg")
    assert len(products) == 1
    pid = products[0]["id"]

    p = database.get_product(pid)
    assert p is not None
    assert isinstance(p, dict)
    assert PRODUCT_COLUMNS.issubset(p.keys()), (
        f"missing legacy keys: {PRODUCT_COLUMNS - set(p.keys())}"
    )
    assert p["name"] == "60 UC"
    assert p["base_price"] == 0.9
    assert p["sell_price"] == 1.2
    assert p["sort_order"] == 10
    assert p["active"] == 1


def test_get_product_returns_none_when_missing(app):
    database = app._test_database
    assert database.get_product(99_999) is None


def test_get_product_returns_none_for_inactive_row(app):
    """Critical contract — inactive products MUST be hidden from this
    function (the checkout / cart code relies on it as a kill-switch)."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "ext-2", "Inactive pack",
                  active=0, sort_order=5)

    # Look it up directly via raw SQL because list_products would also
    # hide it.
    conn = database.connect()
    try:
        row = conn.execute(
            "SELECT id FROM products WHERE provider='server2' AND game_key='pubg'"
        ).fetchone()
        pid = row["id"]
    finally:
        conn.close()

    assert database.get_product(pid) is None


def test_get_product_returns_none_for_invalid_id(app):
    database = app._test_database
    assert database.get_product("not-a-number") is None
    assert database.get_product(None) is None


# ===========================================================================
# list_products — the tricky one
# ===========================================================================
def test_list_products_empty_when_no_rows(app):
    database = app._test_database
    assert database.list_products("server2", "no-game") == []


def test_list_products_returns_full_set_when_no_curated(app):
    """When NO active product has a positive sort_order, the full active
    list is returned (every legacy import path leaves sort_order=0 by
    default)."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "60 UC",
                  sell_price=1.0, sort_order=0)
    _seed_product(database, "server2", "pubg", "p2", "120 UC",
                  sell_price=2.0, sort_order=0)
    _seed_product(database, "server2", "pubg", "p3", "INACTIVE",
                  sell_price=0.5, sort_order=0, active=0)

    rows = database.list_products("server2", "pubg")
    names = [r["name"] for r in rows]
    assert "INACTIVE" not in names
    assert set(names) == {"60 UC", "120 UC"}


def test_list_products_curated_subset_when_any_positive_sort(app):
    """Once the admin curates one product (sort_order > 0), the full
    list collapses to ONLY the curated ones — sort_order=0 rows hide."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "Curated 1",
                  sell_price=1.0, sort_order=1)
    _seed_product(database, "server2", "pubg", "p2", "Curated 2",
                  sell_price=2.0, sort_order=2)
    _seed_product(database, "server2", "pubg", "p3", "Noise",
                  sell_price=3.0, sort_order=0)

    rows = database.list_products("server2", "pubg")
    names = [r["name"] for r in rows]
    assert names == ["Curated 1", "Curated 2"]
    assert "Noise" not in names


def test_list_products_only_active_false_disables_curated_filter(app):
    """`only_active=False` is the admin path — see EVERY product even if
    inactive or non-curated. This is what powers the admin product
    editor."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "Curated",
                  sell_price=1.0, sort_order=1)
    _seed_product(database, "server2", "pubg", "p2", "Noise",
                  sell_price=2.0, sort_order=0)
    _seed_product(database, "server2", "pubg", "p3", "Inactive",
                  sell_price=3.0, sort_order=0, active=0)

    rows = database.list_products("server2", "pubg", only_active=False)
    names = {r["name"] for r in rows}
    assert names == {"Curated", "Noise", "Inactive"}


def test_list_products_ordering_pushes_zero_sort_to_end(app):
    """Order: rows with sort_order=0 are treated as 999999 and end up
    last; ties broken by sell_price ASC then id ASC."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    # Curated entries — 5, 10 → expected first 5, then 10.
    _seed_product(database, "server2", "pubg", "p10", "Curated 10",
                  sell_price=99.0, sort_order=10)
    _seed_product(database, "server2", "pubg", "p5", "Curated 5",
                  sell_price=99.0, sort_order=5)
    # Two non-curated rows; will only show when only_active=False (since
    # the curated subset will hide them in the default mode). For this
    # test we use only_active=False to exercise ordering across both
    # buckets.
    _seed_product(database, "server2", "pubg", "p_zero_a", "Zero A",
                  sell_price=2.0, sort_order=0)
    _seed_product(database, "server2", "pubg", "p_zero_b", "Zero B",
                  sell_price=1.0, sort_order=0)

    rows = database.list_products("server2", "pubg", only_active=False)
    names = [r["name"] for r in rows]
    # Curated first, then sort=0 rows ordered by sell_price ASC.
    assert names == ["Curated 5", "Curated 10", "Zero B", "Zero A"]


def test_list_products_fallback_when_only_active_returns_empty(app):
    """When `only_active=True` produces an empty set AND no group_id
    filter is set, the function falls through to a no-filter query.
    This is the safety net for admins who just imported products with
    `active=0` so a /game/<key> page never renders empty."""
    database = app._test_database
    _seed_game(database, "server2", "newgame", "Fresh Game")
    # Every product is inactive -> first query would be empty.
    _seed_product(database, "server2", "newgame", "p1", "First",
                  sell_price=1.0, active=0)
    _seed_product(database, "server2", "newgame", "p2", "Second",
                  sell_price=2.0, active=0)

    rows = database.list_products("server2", "newgame")
    names = {r["name"] for r in rows}
    assert names == {"First", "Second"}


def test_list_products_fallback_does_not_trigger_when_group_id_set(app):
    """If the caller asked for a specific group, an empty result is
    expected and meaningful — don't fall back."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "Standalone",
                  sell_price=1.0, active=0)  # no group, inactive

    rows = database.list_products("server2", "pubg", group_id=42)
    assert rows == []


def test_list_products_group_id_filter(app):
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "In Group 1",
                  sell_price=1.0, sort_order=1, group_id=1)
    _seed_product(database, "server2", "pubg", "p2", "In Group 2",
                  sell_price=2.0, sort_order=2, group_id=2)
    _seed_product(database, "server2", "pubg", "p3", "Ungrouped",
                  sell_price=3.0, sort_order=3, group_id=None)

    rows = database.list_products("server2", "pubg", group_id=1)
    assert [r["name"] for r in rows] == ["In Group 1"]
    rows2 = database.list_products("server2", "pubg", group_id=2)
    assert [r["name"] for r in rows2] == ["In Group 2"]


def test_list_products_attaches_display_name(app):
    """Every dict gets a `display_name` injected via translate_product_name.
    Templates read this key to show the Arabic version next to the
    raw provider-supplied name."""
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "PUBG Mobile UC 60",
                  sell_price=1.0, sort_order=1)

    rows = database.list_products("server2", "pubg")
    assert len(rows) == 1
    assert "display_name" in rows[0]
    # Spot-check a known replacement: "PUBG Mobile UC" → "شدات ببجي".
    assert "شدات ببجي" in rows[0]["display_name"]


def test_list_products_dict_keys_match_legacy(app):
    database = app._test_database
    _seed_game(database, "server2", "pubg", "PUBG")
    _seed_product(database, "server2", "pubg", "p1", "60 UC",
                  sell_price=1.0, sort_order=1)
    rows = database.list_products("server2", "pubg")
    assert len(rows) == 1
    expected = PRODUCT_COLUMNS | {"display_name"}
    assert expected.issubset(rows[0].keys()), (
        f"missing keys: {expected - set(rows[0].keys())}"
    )
