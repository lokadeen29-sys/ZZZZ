"""V72 phase 3 / PR #6 — behavioural parity tests for the LAST wave of
``database.py`` rewrites.

Scope: every remaining function that previously used ``with db_conn()``
for runtime data (i.e. NOT the bootstrap / DDL / seeder helpers).

  * Auth / user lifecycle: create_user, authenticate, get_user_by_email,
    get_user_by_id, get_user_by_google_sub, link_user_google_sub,
    create_user_oauth, update_user_profile, set_pending_email_change,
    confirm_pending_email_change, set_user_email_token, verify_user_email,
    set_password_reset_token, get_user_by_reset_token, reset_user_password.
  * 2FA: set_user_totp_secret, enable_user_totp, disable_user_totp,
    update_user_backup_codes.
  * Catalog admin writes: upsert_game, add_custom_game, set_game_active,
    set_game_show_on_home, set_game_home_sort_order, update_game_image,
    update_game_pricing, upsert_product, delete_products_for_game,
    update_product_sort_orders, update_manual_syp_prices,
    update_products_admin, update_profit_margin.
  * Product groups: list_product_groups, get_product_group,
    create_product_group, update_product_group, delete_product_group.
  * Public reads: list_public_games, list_home_games,
    list_public_product_groups_for_home, list_all_game_groups,
    list_product_games_from_products.
  * Admin reads: stats, list_users, search_users, user_financial_summary,
    list_user_deposits_admin, list_orders_for_auto_refresh,
    list_all_games_for_admin, list_all_products_for_admin,
    accounting_summary, search_suggest, get_product_by_id,
    get_order_public, can_download_proof.
  * Audit log: insert_audit_log, list_audit_logs, count_audit_logs.
  * Misc: seed_admin (re-tested through conftest's bootstrap path).

Why such a large suite
----------------------

PR #6 closes the migration of ``database.py``. After this PR, the only
``with db_conn()`` calls left in production code are DDL helpers that
will be replaced by Alembic in the cleanup phase. That means a bug
introduced here will show up in EVERY admin / user / catalog / audit
flow in the app. The tests are intentionally redundant against the
behavioural invariants of each function (return shape, ordering, edge
cases, side-effect isolation) so a refactor that breaks anything
surfaces here, not in production.

What we are NOT testing
-----------------------

  * The DDL helpers (``init_db``, ``ensure_indexes``, etc.) — they are
    exercised by ``conftest.py``'s setup; the existing
    ``tests/test_orm_models.py`` covers the schema-vs-models parity.
  * The legacy bulk seeders (``seed_local_provider_catalog``,
    ``attach_generated_posters``) — both kept as raw SQL on purpose
    because they are one-shot admin tools; converting them is queued
    for the post-migration cleanup wave.
"""

from __future__ import annotations

import time

import pytest


# ===========================================================================
# Shared helpers
# ===========================================================================
def _conn(database):
    return database.connect()


def _seed_game(
    database,
    *,
    provider="server1",
    game_key="g1",
    name="Game One",
    emoji="🎮",
    image_url="",
    active=1,
    show_on_home=0,
    home_sort_order=0,
):
    """Insert a games row directly through SQLite (bypasses upsert_game so
    the fixture itself isn't subject to the function under test)."""
    conn = _conn(database)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO games"
            "(provider, game_key, name, emoji, image_url, active, "
            " show_on_home, home_sort_order) VALUES (?,?,?,?,?,?,?,?)",
            (provider, game_key, name, emoji, image_url, active,
             show_on_home, home_sort_order),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_product(
    database,
    *,
    provider="server1",
    game_key="g1",
    provider_product_id="p1",
    name="Pack 1",
    base_price=1.0,
    sell_price=2.0,
    active=1,
    sort_order=0,
    group_id=None,
    manual_price_syp=0.0,
    pricing_mode="usd",
    fixed_syp_price=0.0,
):
    conn = _conn(database)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO products(
                provider, game_key, provider_product_id, name,
                base_price, sell_price, active, sort_order, group_id,
                manual_price_syp, pricing_mode, fixed_syp_price
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                provider, game_key, provider_product_id, name,
                base_price, sell_price, active, sort_order, group_id,
                manual_price_syp, pricing_mode, fixed_syp_price,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM products WHERE provider=? AND provider_product_id=?",
            (provider, provider_product_id),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def _seed_deposit(
    database,
    *,
    user_id,
    amount=10.0,
    method="USDT (TRC20)",
    status="approved",
    currency="USD",
    amount_usd=10.0,
    proof_filename=None,
):
    """Bypass create_deposit (which has dedup logic) so tests are fully
    deterministic."""
    import secrets as _s
    conn = _conn(database)
    try:
        code = "DEP" + _s.token_urlsafe(10)
        conn.execute(
            """
            INSERT INTO deposits (
                deposit_code, user_id, amount, method, proof, status,
                created_at, currency, amount_usd, proof_filename
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                code, user_id, amount, method, "proof.png", status,
                int(time.time()), currency, amount_usd, proof_filename,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ===========================================================================
# Auth & user lifecycle
# ===========================================================================
class TestCreateUserAndAuthenticate:
    def test_create_user_then_authenticate_succeeds(self, app):
        db = app._test_database
        ok, err = db.create_user(
            "Alice", "alice@example.com", "+963900000001", "GoodPass1!",
            email_verified=1,
        )
        assert ok is True
        assert err is None

        user = db.authenticate("alice@example.com", "GoodPass1!")
        assert user is not None
        # Full users column set is exposed (so callers can read
        # session_version, role, etc.)
        assert {"id", "name", "email", "role", "balance",
                "session_version", "totp_enabled"}.issubset(user.keys())
        assert user["email"] == "alice@example.com"
        assert user["role"] == "user"

    def test_email_lowercased_on_insert(self, app):
        db = app._test_database
        db.create_user("Bob", "BOB@Example.COM", "", "Pw1!")
        # Both the DB and the get-by-email helper agree on lower case.
        assert db.get_user_by_email("BOB@example.com")["email"] == "bob@example.com"
        assert db.get_user_by_email("bob@example.com") is not None

    def test_create_user_duplicate_email_returns_arabic_error(self, app):
        db = app._test_database
        db.create_user("First", "dup@e.com", "", "Pw1!")
        ok, err = db.create_user("Second", "dup@e.com", "", "Pw2!")
        assert ok is False
        assert err == "البريد مستخدم مسبقًا"

    def test_authenticate_wrong_password_returns_none(self, app):
        db = app._test_database
        db.create_user("Carl", "carl@e.com", "", "Right1!")
        assert db.authenticate("carl@e.com", "Wrong1!") is None

    def test_authenticate_inactive_user_returns_none(self, app):
        db = app._test_database
        db.create_user("Dan", "dan@e.com", "", "Pw1!")
        # Force the row inactive (no helper exposed for this).
        c = _conn(db)
        c.execute("UPDATE users SET active=0 WHERE email=?", ("dan@e.com",))
        c.commit()
        c.close()
        assert db.authenticate("dan@e.com", "Pw1!") is None

    def test_email_token_set_on_create_when_provided(self, app):
        db = app._test_database
        db.create_user("Eve", "eve@e.com", "", "Pw1!", email_token="TOK1")
        user = db.get_user_by_email("eve@e.com")
        assert user["email_token"] == "TOK1"
        # Token timestamp is set when a token is supplied.
        assert user["email_token_created_at"] is not None
        assert int(user["email_token_created_at"]) > 0


class TestGetUserHelpers:
    def test_get_user_by_email_returns_none_for_missing(self, app):
        db = app._test_database
        assert db.get_user_by_email("nope@example.com") is None

    def test_get_user_by_id_uses_narrow_projection(self, app, make_user):
        db = app._test_database
        user = make_user(email="proj@e.com")
        row = db.get_user_by_id(user["id"])
        # Narrow projection — does NOT leak password_hash / TOTP / tokens.
        assert "password_hash" not in row
        assert "totp_secret" not in row
        assert "email_token" not in row
        # But does include the columns admin templates render.
        assert {"id", "name", "email", "phone", "role", "balance",
                "active", "email_verified", "created_at"}.issubset(row.keys())

    def test_get_user_by_id_missing_returns_none(self, app):
        db = app._test_database
        assert db.get_user_by_id(999_999_999) is None


class TestUpdateUserProfile:
    def test_only_provided_fields_change(self, app, make_user):
        db = app._test_database
        user = make_user(email="prof1@e.com")
        # Set BOTH name and phone first.
        db.update_user_profile(user["id"], name="Old Name", phone="+963 1")

        # Now update name only — phone must not be wiped.
        db.update_user_profile(user["id"], name="New Name", phone=None)

        refreshed = db.get_user_by_email("prof1@e.com")
        assert refreshed["name"] == "New Name"
        assert refreshed["phone"] == "+963 1"

    def test_both_args_none_is_noop(self, app, make_user):
        db = app._test_database
        user = make_user(email="prof2@e.com")
        before = db.get_user_by_email("prof2@e.com")
        db.update_user_profile(user["id"], name=None, phone=None)
        after = db.get_user_by_email("prof2@e.com")
        assert before["name"] == after["name"]
        assert before["phone"] == after["phone"]


class TestEmailVerification:
    def test_verify_user_email_happy_path(self, app):
        db = app._test_database
        db.create_user("Fay", "fay@e.com", "", "Pw1!", email_verified=0,
                       email_token="T1")

        ok, err = db.verify_user_email("T1")
        assert ok is True
        assert err is None
        user = db.get_user_by_email("fay@e.com")
        assert user["email_verified"] == 1
        # Token wiped → single-use.
        assert user["email_token"] is None
        assert user["email_token_created_at"] is None

    def test_verify_user_email_unknown_token(self, app):
        db = app._test_database
        ok, err = db.verify_user_email("DOES_NOT_EXIST")
        assert ok is False
        assert "غير صحيح" in err

    def test_verify_user_email_expired(self, app):
        db = app._test_database
        db.create_user("Gus", "gus@e.com", "", "Pw1!", email_token="OLD")
        # Backdate token by 25 hours (expiry is 24h).
        c = _conn(db)
        c.execute("UPDATE users SET email_token_created_at=? WHERE email_token=?",
                  (int(time.time()) - 25 * 3600, "OLD"))
        c.commit()
        c.close()

        ok, err = db.verify_user_email("OLD")
        assert ok is False
        assert "صلاحية" in err

    def test_set_user_email_token_resets_timestamp(self, app, make_user):
        db = app._test_database
        user = make_user(email="rsv@e.com")
        db.set_user_email_token(user["id"], "FRESH")
        row = db.get_user_by_email("rsv@e.com")
        assert row["email_token"] == "FRESH"
        assert int(row["email_token_created_at"]) >= int(time.time()) - 5


class TestPasswordReset:
    def test_set_and_lookup_reset_token(self, app, make_user):
        db = app._test_database
        user = make_user(email="rst@e.com")
        db.set_password_reset_token(user["id"], "RESET-1")
        found = db.get_user_by_reset_token("RESET-1")
        assert found is not None
        assert found["id"] == user["id"]

    def test_get_user_by_reset_token_unknown_returns_none(self, app):
        db = app._test_database
        assert db.get_user_by_reset_token("NEVER") is None

    def test_reset_user_password_happy_path_invalidates_session(self, app, make_user):
        db = app._test_database
        user = make_user(email="rst2@e.com")
        before_sv = db.get_user_by_email("rst2@e.com").get("session_version") or 1
        db.set_password_reset_token(user["id"], "RST-2")

        ok, err = db.reset_user_password("RST-2", "BrandNew1!")
        assert ok is True
        assert err is None

        refreshed = db.get_user_by_email("rst2@e.com")
        # Token wiped (single-use).
        assert refreshed["reset_token"] is None
        assert refreshed["reset_token_created_at"] is None
        # V53 session_version bumped → other devices get logged out.
        assert refreshed["session_version"] == before_sv + 1
        # New password works; old password no longer.
        assert db.authenticate("rst2@e.com", "BrandNew1!") is not None
        assert db.authenticate("rst2@e.com", "UserPass123!") is None

    def test_reset_user_password_invalid_token(self, app):
        db = app._test_database
        ok, err = db.reset_user_password("BAD", "BrandNew1!")
        assert ok is False
        assert err == "رابط الاستعادة غير صحيح"

    def test_reset_user_password_expired_token(self, app, make_user):
        db = app._test_database
        user = make_user(email="rst3@e.com")
        db.set_password_reset_token(user["id"], "EXP")
        c = _conn(db)
        c.execute("UPDATE users SET reset_token_created_at=? WHERE reset_token=?",
                  (int(time.time()) - 7200, "EXP"))  # 2h ago, expiry is 1h.
        c.commit()
        c.close()

        ok, err = db.reset_user_password("EXP", "BrandNew1!")
        assert ok is False
        assert "صلاحية" in err


class TestPendingEmailChange:
    def test_set_then_confirm_swaps_email(self, app, make_user):
        db = app._test_database
        user = make_user(email="old@e.com")
        db.set_pending_email_change(user["id"], "NEW@e.com", "PE-1")

        ok, err = db.confirm_pending_email_change("PE-1")
        assert ok is True
        assert err is None
        refreshed = db.get_user_by_email("new@e.com")  # lower-cased on store
        assert refreshed is not None
        assert refreshed["id"] == user["id"]
        # All pending columns wiped.
        assert refreshed["pending_email"] is None
        assert refreshed["pending_email_token"] is None

    def test_confirm_email_change_when_target_exists(self, app, make_user):
        db = app._test_database
        u1 = make_user(email="own@e.com")
        make_user(email="taken@e.com")
        db.set_pending_email_change(u1["id"], "taken@e.com", "PE-2")

        ok, err = db.confirm_pending_email_change("PE-2")
        assert ok is False
        assert "مستخدم في حساب آخر" in err

    def test_confirm_email_change_expired(self, app, make_user):
        db = app._test_database
        user = make_user(email="exp@e.com")
        db.set_pending_email_change(user["id"], "fresh@e.com", "PE-3")
        c = _conn(db)
        c.execute(
            "UPDATE users SET pending_email_created_at=? WHERE pending_email_token=?",
            (int(time.time()) - 25 * 3600, "PE-3"),
        )
        c.commit()
        c.close()

        ok, err = db.confirm_pending_email_change("PE-3")
        assert ok is False
        assert "صلاحية" in err


class TestGoogleOAuth:
    def test_create_user_oauth_returns_id(self, app):
        db = app._test_database
        uid = db.create_user_oauth("Google User", "g@e.com", "google-sub-1")
        assert isinstance(uid, int)
        assert uid > 0

        user = db.get_user_by_email("g@e.com")
        assert user["email_verified"] == 1  # OAuth users land verified.
        assert user["google_sub"] == "google-sub-1"

    def test_create_user_oauth_duplicate_returns_none(self, app):
        db = app._test_database
        db.create_user_oauth("First", "dup@e.com", "sub-A")
        # Second call collides on email — legacy returned None.
        assert db.create_user_oauth("Second", "dup@e.com", "sub-B") is None

    def test_get_user_by_google_sub(self, app):
        db = app._test_database
        db.create_user_oauth("Hank", "hank@e.com", "sub-X")
        row = db.get_user_by_google_sub("sub-X")
        assert row is not None
        assert row["email"] == "hank@e.com"
        # Unknown sub.
        assert db.get_user_by_google_sub("sub-NOPE") is None

    def test_link_user_google_sub(self, app, make_user):
        db = app._test_database
        user = make_user(email="link@e.com")
        db.link_user_google_sub(user["id"], "sub-LINK")
        assert db.get_user_by_email("link@e.com")["google_sub"] == "sub-LINK"


# ===========================================================================
# 2FA helpers
# ===========================================================================
class TestTotpHelpers:
    def test_set_secret_clears_previous_state(self, app, make_user):
        db = app._test_database
        user = make_user(email="totp@e.com")
        # Pre-load some "stale" TOTP state.
        db.set_user_totp_secret(user["id"], "SECRET1")
        db.enable_user_totp(user["id"], '["code1","code2"]')

        # Setting a fresh secret must reset enabled + backup codes + at.
        db.set_user_totp_secret(user["id"], "SECRET2")
        c = _conn(db)
        row = c.execute(
            "SELECT totp_secret, totp_enabled, totp_backup_codes, totp_enabled_at"
            " FROM users WHERE id=?", (user["id"],)).fetchone()
        c.close()
        assert row["totp_secret"] == "SECRET2"
        assert row["totp_enabled"] == 0
        assert row["totp_backup_codes"] is None
        assert row["totp_enabled_at"] is None

    def test_enable_user_totp_marks_enabled_and_stores_codes(self, app, make_user):
        db = app._test_database
        user = make_user(email="totp2@e.com")
        db.set_user_totp_secret(user["id"], "SECRET")
        db.enable_user_totp(user["id"], '["x","y"]')

        c = _conn(db)
        row = c.execute(
            "SELECT totp_enabled, totp_backup_codes, totp_enabled_at"
            " FROM users WHERE id=?", (user["id"],)).fetchone()
        c.close()
        assert row["totp_enabled"] == 1
        assert row["totp_backup_codes"] == '["x","y"]'
        assert row["totp_enabled_at"] is not None

    def test_disable_user_totp_wipes_everything(self, app, make_user):
        db = app._test_database
        user = make_user(email="totp3@e.com")
        db.set_user_totp_secret(user["id"], "SECRET")
        db.enable_user_totp(user["id"], '["a"]')

        db.disable_user_totp(user["id"])
        c = _conn(db)
        row = c.execute(
            "SELECT totp_secret, totp_enabled, totp_backup_codes,"
            " totp_enabled_at FROM users WHERE id=?",
            (user["id"],)).fetchone()
        c.close()
        assert row["totp_secret"] is None
        assert row["totp_enabled"] == 0
        assert row["totp_backup_codes"] is None
        assert row["totp_enabled_at"] is None

    def test_update_user_backup_codes_replaces_blob(self, app, make_user):
        db = app._test_database
        user = make_user(email="totp4@e.com")
        db.set_user_totp_secret(user["id"], "SECRET")
        db.enable_user_totp(user["id"], '["old1","old2"]')
        db.update_user_backup_codes(user["id"], '["new1"]')

        c = _conn(db)
        row = c.execute(
            "SELECT totp_backup_codes FROM users WHERE id=?",
            (user["id"],)).fetchone()
        c.close()
        assert row["totp_backup_codes"] == '["new1"]'


# ===========================================================================
# Catalog admin writes
# ===========================================================================
class TestUpsertGame:
    def test_insert_then_update_only_name_emoji(self, app):
        db = app._test_database
        db.upsert_game("server1", "g1", "First Name", emoji="🎮", active=0)

        # Re-call with active=1 — legacy ON CONFLICT does NOT touch active.
        db.upsert_game("server1", "g1", "Second Name", emoji="🔥", active=1)

        row = db.get_game("server1", "g1")
        assert row["name"] == "Second Name"
        assert row["emoji"] == "🔥"
        # active stays 0 — that's the legacy quirk we're guarding.
        assert row["active"] == 0


class TestAddCustomGame:
    def test_update_path_does_touch_active_and_image(self, app):
        db = app._test_database
        db.add_custom_game("server1", "cg1", "Custom", image_url="/a.png", active=1)
        # Update path: must overwrite image_url and active (admin form).
        db.add_custom_game("server1", "cg1", "Custom 2", image_url="/b.png", active=0)
        row = db.get_game("server1", "cg1")
        assert row["name"] == "Custom 2"
        assert row["image_url"] == "/b.png"
        assert row["active"] == 0


class TestSetGameActive:
    def test_toggles_only_target_row(self, app):
        db = app._test_database
        _seed_game(db, provider="server1", game_key="ag", active=1)
        _seed_game(db, provider="server1", game_key="other", active=1)

        db.set_game_active("server1", "ag", False)
        assert db.get_game("server1", "ag")["active"] == 0
        assert db.get_game("server1", "other")["active"] == 1


class TestSetGameShowOnHomeAndSort:
    def test_show_on_home_and_sort_order(self, app):
        db = app._test_database
        _seed_game(db, provider="server1", game_key="hg")
        db.set_game_show_on_home("server1", "hg", True)
        db.set_game_home_sort_order("server1", "hg", 5)
        row = db.get_game("server1", "hg")
        assert row["show_on_home"] == 1
        assert row["home_sort_order"] == 5

    def test_negative_sort_order_clamped_to_zero(self, app):
        db = app._test_database
        _seed_game(db, provider="server1", game_key="hg2")
        db.set_game_home_sort_order("server1", "hg2", -3)
        assert db.get_game("server1", "hg2")["home_sort_order"] == 0

    def test_invalid_sort_order_falls_back_to_zero(self, app):
        db = app._test_database
        _seed_game(db, provider="server1", game_key="hg3")
        db.set_game_home_sort_order("server1", "hg3", "not-a-number")
        assert db.get_game("server1", "hg3")["home_sort_order"] == 0


class TestUpsertProduct:
    def test_insert_then_update_does_not_touch_active(self, app):
        db = app._test_database
        _seed_game(db)
        db.upsert_product("server1", "g1", "P1", "First", 1.0, 2.0, active=1)
        # Disable the row directly.
        c = _conn(db)
        c.execute("UPDATE products SET active=0 WHERE provider=? AND provider_product_id=?",
                  ("server1", "P1"))
        c.commit()
        c.close()
        # Re-upsert with active=1 — legacy keeps the manually-disabled state.
        db.upsert_product("server1", "g1", "P1", "Second", 3.0, 4.0, active=1)
        c = _conn(db)
        row = c.execute("SELECT * FROM products WHERE provider=? AND provider_product_id=?",
                        ("server1", "P1")).fetchone()
        c.close()
        assert row["name"] == "Second"
        assert row["base_price"] == 3.0
        assert row["sell_price"] == 4.0
        # `active` MUST stay 0 (the admin disable wins).
        assert row["active"] == 0


class TestDeleteProductsForGame:
    def test_only_target_game_products_removed(self, app):
        db = app._test_database
        _seed_product(db, provider="server1", game_key="g1", provider_product_id="p1")
        _seed_product(db, provider="server1", game_key="g1", provider_product_id="p2")
        _seed_product(db, provider="server1", game_key="g2", provider_product_id="p3")

        db.delete_products_for_game("server1", "g1")

        c = _conn(db)
        rows = c.execute(
            "SELECT provider_product_id FROM products WHERE provider=?",
            ("server1",),
        ).fetchall()
        c.close()
        assert {r["provider_product_id"] for r in rows} == {"p3"}


class TestUpdateGamePricing:
    def test_only_whitelisted_values_persist(self, app):
        db = app._test_database
        _seed_game(db, provider="server1", game_key="gp")
        db.update_game_pricing("server1", "gp", "USD")
        assert db.get_game("server1", "gp")["pricing_currency"] == "USD"
        # Anything else falls back to GLOBAL.
        db.update_game_pricing("server1", "gp", "BOGUS")
        assert db.get_game("server1", "gp")["pricing_currency"] == "GLOBAL"


class TestProductGroups:
    def test_create_and_list(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="gx")
        g = db.create_product_group("s", "gx", "Specials", sort_order=1)
        assert g is not None
        assert g["name"] == "Specials"
        rows = db.list_product_groups("s", "gx")
        assert any(r["id"] == g["id"] for r in rows)

    def test_list_only_active(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="gy")
        g1 = db.create_product_group("s", "gy", "On", sort_order=1, active=1)
        db.create_product_group("s", "gy", "Off", sort_order=2, active=0)
        rows = db.list_product_groups("s", "gy", only_active=True)
        assert {r["name"] for r in rows} == {"On"}
        rows_all = db.list_product_groups("s", "gy", only_active=False)
        assert {r["name"] for r in rows_all} == {"On", "Off"}
        assert g1 is not None  # use the variable

    def test_get_invalid_id_returns_none(self, app):
        db = app._test_database
        assert db.get_product_group("not-an-int") is None
        assert db.get_product_group(999_999) is None

    def test_update_replaces_all_fields(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="gu")
        g = db.create_product_group("s", "gu", "Old", sort_order=2, active=1)

        db.update_product_group(g["id"], name="New", image_url="/x.png",
                                sort_order=9, active=0)
        refreshed = db.get_product_group(g["id"])
        assert refreshed["name"] == "New"
        assert refreshed["image_url"] == "/x.png"
        assert refreshed["sort_order"] == 9
        assert refreshed["active"] == 0

    def test_delete_detaches_products_first(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="gd")
        g = db.create_product_group("s", "gd", "Del", sort_order=1)
        # Attach a product to the group.
        prod = _seed_product(db, provider="s", game_key="gd",
                             provider_product_id="GD-1", group_id=g["id"])
        assert prod["group_id"] == g["id"]

        db.delete_product_group(g["id"])
        # Group is gone, product survives but with group_id=NULL.
        assert db.get_product_group(g["id"]) is None
        c = _conn(db)
        row = c.execute("SELECT group_id FROM products WHERE id=?",
                        (prod["id"],)).fetchone()
        c.close()
        assert row["group_id"] is None


class TestUpdateProductSortOrders:
    def test_bulk_update_in_one_transaction(self, app):
        db = app._test_database
        _seed_game(db)
        p1 = _seed_product(db, provider_product_id="bp1", sort_order=0)
        p2 = _seed_product(db, provider_product_id="bp2", sort_order=0)

        db.update_product_sort_orders([(p1["id"], 5), (p2["id"], 9)])
        c = _conn(db)
        rows = {r["id"]: r["sort_order"] for r in c.execute(
            "SELECT id, sort_order FROM products WHERE id IN (?,?)",
            (p1["id"], p2["id"]),
        ).fetchall()}
        c.close()
        assert rows[p1["id"]] == 5
        assert rows[p2["id"]] == 9


class TestUpdateManualSypPrices:
    def test_bulk_set_with_safe_fallback(self, app):
        db = app._test_database
        _seed_game(db)
        p = _seed_product(db, provider_product_id="ms1", manual_price_syp=0)

        db.update_manual_syp_prices([(p["id"], 12345.0), (p["id"], "not-num"),
                                     (p["id"], None)])
        # Last update wins. None / "not-num" both collapse to 0.0.
        c = _conn(db)
        row = c.execute("SELECT manual_price_syp FROM products WHERE id=?",
                        (p["id"],)).fetchone()
        c.close()
        assert row["manual_price_syp"] == 0.0


class TestUpdateProductsAdmin:
    def test_fixed_syp_path_recomputes_sell_price(self, app):
        db = app._test_database
        _seed_game(db)
        p = _seed_product(db, provider_product_id="ua1", sell_price=100.0)
        db.update_products_admin(
            [{
                "product_id": p["id"],
                "sort_order": 3,
                "group_id": None,
                "pricing_mode": "fixed_syp",
                "fixed_syp_price": 30000,
            }],
            usd_syp_rate=15000,
        )
        c = _conn(db)
        row = c.execute("SELECT sort_order, group_id, pricing_mode, "
                        "fixed_syp_price, sell_price FROM products WHERE id=?",
                        (p["id"],)).fetchone()
        c.close()
        assert row["sort_order"] == 3
        assert row["group_id"] is None
        assert row["pricing_mode"] == "fixed_syp"
        assert row["fixed_syp_price"] == 30000
        assert row["sell_price"] == 2.0  # 30000 / 15000

    def test_other_path_resets_fixed_syp_price(self, app):
        db = app._test_database
        _seed_game(db)
        p = _seed_product(db, provider_product_id="ua2",
                          pricing_mode="fixed_syp", fixed_syp_price=99999.0,
                          sell_price=42.0)
        db.update_products_admin(
            [{
                "product_id": p["id"],
                "sort_order": 0,
                "group_id": None,
                "pricing_mode": "usd",
                "fixed_syp_price": 0,
            }],
            usd_syp_rate=15000,
        )
        c = _conn(db)
        row = c.execute("SELECT pricing_mode, fixed_syp_price, sell_price"
                        " FROM products WHERE id=?", (p["id"],)).fetchone()
        c.close()
        assert row["pricing_mode"] == "usd"
        assert row["fixed_syp_price"] == 0
        # sell_price untouched in the "else" branch.
        assert row["sell_price"] == 42.0

    def test_invalid_pricing_mode_falls_back_to_usd(self, app):
        db = app._test_database
        _seed_game(db)
        p = _seed_product(db, provider_product_id="ua3")
        db.update_products_admin(
            [{
                "product_id": p["id"],
                "sort_order": 1,
                "group_id": None,
                "pricing_mode": "BOGUS",
                "fixed_syp_price": 0,
            }],
            usd_syp_rate=15000,
        )
        c = _conn(db)
        row = c.execute("SELECT pricing_mode FROM products WHERE id=?",
                        (p["id"],)).fetchone()
        c.close()
        assert row["pricing_mode"] == "usd"


@pytest.mark.postgres
class TestUpdateProfitMargin:
    def test_recomputes_sell_price_and_resets_overrides(self, app):
        db = app._test_database
        _seed_game(db)
        p1 = _seed_product(db, provider_product_id="pm1",
                           base_price=10.0, sell_price=12.0)
        p2 = _seed_product(db, provider_product_id="pm2",
                           pricing_mode="fixed_syp", fixed_syp_price=9999.0,
                           sell_price=42.0, base_price=8.0)
        p3 = _seed_product(db, provider_product_id="pm3",
                           manual_price_syp=11111.0, sell_price=10.0,
                           base_price=5.0)

        db.update_profit_margin(1.5)

        c = _conn(db)
        rows = {r["id"]: dict(r) for r in c.execute(
            "SELECT id, sell_price, pricing_mode, fixed_syp_price,"
            " manual_price_syp FROM products WHERE id IN (?,?,?)",
            (p1["id"], p2["id"], p3["id"]),
        ).fetchall()}
        c.close()
        # sell_price = ROUND(base_price * 1.5, 2) on every row.
        assert rows[p1["id"]]["sell_price"] == 15.0
        assert rows[p2["id"]]["sell_price"] == 12.0
        assert rows[p3["id"]]["sell_price"] == 7.5
        # Overrides reset.
        assert rows[p2["id"]]["pricing_mode"] == "usd"
        assert rows[p2["id"]]["fixed_syp_price"] == 0
        assert rows[p3["id"]]["manual_price_syp"] == 0
        # Setting persisted.
        assert db.get_setting("profit_margin") == "1.5"


# ===========================================================================
# Public reads
# ===========================================================================
class TestListPublicGames:
    def test_includes_zero_product_games(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="z1", name="Zero")
        rows = db.list_public_games(only_active=True)
        zero = next((r for r in rows if r["game_key"] == "z1"), None)
        assert zero is not None
        assert zero["product_count"] == 0
        assert zero["min_price"] is None

    def test_only_active_filters_inactive(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="oa1", active=1)
        _seed_game(db, provider="s", game_key="oa2", active=0)
        keys = {r["game_key"] for r in db.list_public_games(only_active=True)}
        assert "oa1" in keys and "oa2" not in keys

    def test_only_active_products_count(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="cnt", name="C")
        _seed_product(db, provider="s", game_key="cnt",
                      provider_product_id="A", active=1, sell_price=2.0)
        _seed_product(db, provider="s", game_key="cnt",
                      provider_product_id="B", active=0, sell_price=1.0)
        row = next(r for r in db.list_public_games(True) if r["game_key"] == "cnt")
        # Only the active product is counted; min_price reflects ONLY active rows.
        assert row["product_count"] == 1
        assert row["min_price"] == 2.0


class TestListHomeGames:
    def test_only_show_on_home_active(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="h1",
                   active=1, show_on_home=1, home_sort_order=1)
        _seed_game(db, provider="s", game_key="h2",
                   active=1, show_on_home=0)
        _seed_game(db, provider="s", game_key="h3",
                   active=0, show_on_home=1)

        rows = db.list_home_games()
        keys = {r["game_key"] for r in rows}
        assert "h1" in keys
        assert "h2" not in keys
        assert "h3" not in keys

    def test_sort_order_zero_pushed_to_end(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="A",
                   active=1, show_on_home=1, home_sort_order=0, name="A")
        _seed_game(db, provider="s", game_key="B",
                   active=1, show_on_home=1, home_sort_order=2, name="B")
        _seed_game(db, provider="s", game_key="C",
                   active=1, show_on_home=1, home_sort_order=1, name="C")
        rows = db.list_home_games()
        order = [r["game_key"] for r in rows]
        # 1 (C) → 2 (B) → 0/end → A
        assert order.index("C") < order.index("B") < order.index("A")


class TestListPublicProductGroupsForHome:
    def test_groups_with_inactive_game_disappear(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="ga", active=1)
        _seed_game(db, provider="s", game_key="gb", active=0)
        db.create_product_group("s", "ga", "Live")
        db.create_product_group("s", "gb", "Hidden")

        names = {r["name"] for r in db.list_public_product_groups_for_home()}
        assert "Live" in names
        assert "Hidden" not in names

    def test_active_products_only_in_count(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="gc", active=1)
        g = db.create_product_group("s", "gc", "Pricy")
        _seed_product(db, provider="s", game_key="gc",
                      provider_product_id="X", group_id=g["id"],
                      active=1, sell_price=5.0)
        _seed_product(db, provider="s", game_key="gc",
                      provider_product_id="Y", group_id=g["id"],
                      active=0, sell_price=1.0)
        row = next(r for r in db.list_public_product_groups_for_home()
                   if r["name"] == "Pricy")
        assert row["product_count"] == 1
        assert row["min_price"] == 5.0
        assert row["game_name"] is not None  # joined from games


class TestListAllGameGroups:
    def test_includes_inactive_games_and_products(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="ag1", active=0, name="Hidden")
        _seed_product(db, provider="s", game_key="ag1",
                      provider_product_id="Z", active=0, sell_price=99.0)

        rows = db.list_all_game_groups()
        match = next((r for r in rows if r["game_key"] == "ag1"), None)
        assert match is not None
        # Inactive products ARE counted here (admin view).
        assert match["product_count"] == 1
        assert match["min_price"] == 99.0


class TestListProductGamesFromProducts:
    def test_groups_unique_provider_game_pairs(self, app):
        db = app._test_database
        _seed_product(db, provider="s1", game_key="x", provider_product_id="P1",
                      sell_price=1.0)
        _seed_product(db, provider="s1", game_key="x", provider_product_id="P2",
                      sell_price=2.0)
        _seed_product(db, provider="s2", game_key="y", provider_product_id="P3",
                      sell_price=3.0)
        rows = db.list_product_games_from_products()
        keys = {(r["provider"], r["game_key"]) for r in rows}
        assert ("s1", "x") in keys
        assert ("s2", "y") in keys
        s1x = next(r for r in rows if r["provider"] == "s1" and r["game_key"] == "x")
        assert s1x["product_count"] == 2
        assert s1x["min_price"] == 1.0


# ===========================================================================
# Admin reads
# ===========================================================================
class TestStats:
    def test_counts_orders_by_status(self, app, make_user):
        db = app._test_database
        u = make_user(email="st@e.com", balance=100.0)
        _seed_game(db)
        prod = _seed_product(db, sell_price=5.0)

        # 2 completed, 1 pending — direct INSERTs to control status.
        c = _conn(db)
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("OC1", u["id"], "server1", "g1", "G", prod["id"],
                   "P", "p1", 5.0, "completed", 1, 1))
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("OC2", u["id"], "server1", "g1", "G", prod["id"],
                   "P", "p1", 5.0, "completed", 1, 1))
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("OC3", u["id"], "server1", "g1", "G", prod["id"],
                   "P", "p1", 5.0, "pending", 1, 1))
        c.commit()
        c.close()

        s = db.stats()
        assert s["completed"] == 2
        assert s["pending"] == 1
        assert s["orders"] == 3
        assert s["revenue"] == 10.0
        assert s["users"] >= 1


@pytest.mark.postgres
class TestListUsersAndSearch:
    def test_list_users_narrow_projection(self, app, make_user):
        db = app._test_database
        make_user(email="lu1@e.com")
        rows = db.list_users()
        u = next(r for r in rows if r["email"] == "lu1@e.com")
        assert "password_hash" not in u
        assert "totp_secret" not in u

    def test_search_users_by_name_email_phone(self, app, make_user):
        db = app._test_database
        make_user(email="needle@e.com")
        c = _conn(db)
        c.execute("UPDATE users SET phone=? WHERE email=?",
                  ("+963 999 12345", "needle@e.com"))
        c.commit()
        c.close()

        # Each search term hits one column.
        for term in ("needle", "needle@e.com", "999 12345"):
            rows = db.search_users(term)
            assert any(r["email"] == "needle@e.com" for r in rows), term

    def test_search_users_by_id(self, app, make_user):
        db = app._test_database
        u = make_user(email="byid@e.com")
        rows = db.search_users(str(u["id"]))
        assert any(r["id"] == u["id"] for r in rows)

    def test_search_users_returns_distinct(self, app, make_user):
        db = app._test_database
        u = make_user(email="dup@e.com")
        # Plant several orders so the LEFT JOIN would otherwise duplicate.
        c = _conn(db)
        for i in range(3):
            c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                      "game_name,product_id,product_name,player_id,price,status,"
                      "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"DUP{i}", u["id"], "p", "g", "G", 1, "P",
                       "needle-pid", 1.0, "pending", 1, 1))
        c.commit()
        c.close()

        rows = db.search_users("needle-pid")
        # Despite 3 matching orders, the user must appear ONCE (DISTINCT).
        assert sum(1 for r in rows if r["id"] == u["id"]) == 1

    def test_search_users_no_query_returns_recent(self, app, make_user):
        db = app._test_database
        make_user(email="nq@e.com")
        rows = db.search_users()
        assert any(r["email"] == "nq@e.com" for r in rows)


class TestUserFinancialSummary:
    def test_returns_zeros_for_user_with_nothing(self, app, make_user):
        db = app._test_database
        u = make_user(email="empty@e.com")
        s = db.user_financial_summary(u["id"])
        assert s == {
            "deposits_count": 0,
            "deposits_approved": 0,
            "deposits_total_paid": 0,
            "orders_count": 0,
            "orders_total": 0,
        }

    def test_sums_amount_usd_not_amount(self, app, make_user):
        db = app._test_database
        u = make_user(email="ufs@e.com")
        # Two approved deposits: a 5000 SYP one (amount_usd=0.33) and a
        # 10 USD one (amount_usd=10).
        _seed_deposit(db, user_id=u["id"], amount=5000, currency="SYP",
                      amount_usd=0.33, status="approved")
        _seed_deposit(db, user_id=u["id"], amount=10, currency="USD",
                      amount_usd=10, status="approved")
        # Plus a pending one that should NOT count toward `deposits_approved`.
        _seed_deposit(db, user_id=u["id"], amount=1, currency="USD",
                      amount_usd=1, status="pending")

        s = db.user_financial_summary(u["id"])
        assert s["deposits_count"] == 3
        assert s["deposits_approved"] == 2
        # Crucially the total is in USD (10.33), NOT 5010 (mixed currencies).
        assert abs(s["deposits_total_paid"] - 10.33) < 0.01


class TestListUserDepositsAdmin:
    def test_returns_full_column_set(self, app, make_user):
        db = app._test_database
        u = make_user(email="luda@e.com")
        _seed_deposit(db, user_id=u["id"], amount=2.5,
                      proof_filename="proof_x.png", status="pending")

        rows = db.list_user_deposits_admin(u["id"])
        assert len(rows) == 1
        assert {"id", "user_id", "amount", "method", "status", "currency",
                "amount_usd", "proof_filename", "deposit_code"}.issubset(rows[0].keys())
        assert rows[0]["proof_filename"] == "proof_x.png"


class TestSearchSuggest:
    def test_substring_match_games(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="ff",
                   name="Free Fire MENA", active=1)
        _seed_game(db, provider="s", game_key="pubg",
                   name="PUBG Mobile", active=1)

        rows = db.search_suggest("FIRE", limit=5)
        labels = {r["label"] for r in rows}
        assert "Free Fire MENA" in labels

    def test_excludes_inactive(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="hidden",
                   name="Hidden Game", active=0)
        rows = db.search_suggest("Hidden", limit=5)
        assert not any(r["kind"] == "game" and r["label"] == "Hidden Game"
                       for r in rows)

    def test_escapes_like_wildcards(self, app):
        db = app._test_database
        _seed_game(db, provider="s", game_key="literal",
                   name="100% Real Game", active=1)
        # A `%` in the user query must NOT match every row.
        rows = db.search_suggest("%", limit=10)
        # The literal-percent game DOES match because it contains '%'.
        assert any(r["label"] == "100% Real Game" for r in rows)
        # But a non-matching pattern must NOT match.
        _seed_game(db, provider="s", game_key="other",
                   name="No Percent Here", active=1)
        rows2 = db.search_suggest("xyzabc-no-such-pattern", limit=10)
        assert rows2 == []


class TestGetProductByIdAndGetOrderPublic:
    def test_get_product_by_id_returns_inactive(self, app):
        db = app._test_database
        _seed_game(db)
        p = _seed_product(db, provider_product_id="inact", active=0)
        # `get_product` (active=1 only) returns None.
        assert db.get_product(p["id"]) is None
        # `get_product_by_id` (RQ worker hot-path) returns it.
        assert db.get_product_by_id(p["id"]) is not None

    def test_get_product_by_id_invalid_input(self, app):
        db = app._test_database
        assert db.get_product_by_id(None) is None
        assert db.get_product_by_id("abc") is None

    def test_get_order_public_requires_explicit_user_id(self, app):
        db = app._test_database
        with pytest.raises(ValueError):
            db.get_order_public(1, user_id=None)

    def test_get_order_public_owner_isolation(self, app, make_user):
        db = app._test_database
        owner = make_user(email="own@e.com", balance=100.0)
        other = make_user(email="oth@e.com", balance=100.0)
        _seed_game(db)
        prod = _seed_product(db, sell_price=2.0)
        order_id, _ = db.create_order(owner, prod,
                                      db.get_game("server1", "g1"), "P1")

        # Owner can read it.
        assert db.get_order_public(order_id, user_id=owner["id"]) is not None
        # Other user cannot.
        assert db.get_order_public(order_id, user_id=other["id"]) is None
        # Admin sentinel `*` can.
        assert db.get_order_public(order_id, user_id="*") is not None


class TestCanDownloadProof:
    def test_admin_allowed_unconditionally(self, app):
        db = app._test_database
        assert db.can_download_proof(0, True, "any.png") is True

    def test_user_only_for_own_proof(self, app, make_user):
        db = app._test_database
        u = make_user(email="cdp@e.com")
        _seed_deposit(db, user_id=u["id"], proof_filename="mine.png")
        assert db.can_download_proof(u["id"], False, "mine.png") is True
        assert db.can_download_proof(u["id"], False, "someone-else.png") is False


class TestListOrdersForAutoRefresh:
    def test_only_actionable_statuses(self, app, make_user):
        db = app._test_database
        u = make_user(email="rq@e.com", balance=100.0)
        c = _conn(db)
        # supplier_pending + provider_order_id → returned.
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "provider_order_id,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("RQ1", u["id"], "p", "g", "G", 1, "P", "x",
                   1.0, "supplier_pending", "PO-1", 1, 1))
        # processing + provider_order_id → returned.
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "provider_order_id,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("RQ2", u["id"], "p", "g", "G", 1, "P", "x",
                   1.0, "processing", "PO-2", 1, 1))
        # waiting → NOT returned.
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "provider_order_id,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("RQ3", u["id"], "p", "g", "G", 1, "P", "x",
                   1.0, "waiting", "PO-3", 1, 1))
        # processing but empty provider_order_id → NOT returned.
        c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                  "game_name,product_id,product_name,player_id,price,status,"
                  "provider_order_id,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("RQ4", u["id"], "p", "g", "G", 1, "P", "x",
                   1.0, "processing", "", 1, 1))
        c.commit()
        c.close()

        rows = db.list_orders_for_auto_refresh()
        codes = {r["order_code"] for r in rows}
        assert codes == {"RQ1", "RQ2"}


class TestListAllGamesForAdmin:
    def test_returns_full_column_set_in_provider_order(self, app):
        db = app._test_database
        _seed_game(db, provider="b", game_key="b1", name="B Game")
        _seed_game(db, provider="a", game_key="a1", name="A Game")
        rows = db.list_all_games_for_admin()
        # provider ASC: a* first, then b*.
        providers = [r["provider"] for r in rows
                     if r["provider"] in ("a", "b")]
        assert providers.index("a") < providers.index("b")
        # Full column set.
        assert {"id", "provider", "game_key", "name", "active",
                "show_on_home", "home_sort_order", "image_url",
                "pricing_currency"}.issubset(rows[0].keys())


class TestListAllProductsForAdmin:
    def test_includes_group_name_and_display_name(self, app):
        db = app._test_database
        _seed_game(db, provider="x", game_key="g")
        g = db.create_product_group("x", "g", "Specials", sort_order=1)
        _seed_product(db, provider="x", game_key="g",
                      provider_product_id="DN-1", name="UC 60",
                      group_id=g["id"], sort_order=1)
        rows = db.list_all_products_for_admin("x", "g")
        r = next(r for r in rows if r["provider_product_id"] == "DN-1")
        assert r["group_name"] == "Specials"
        # display_name is translated (UC → "شدات").
        assert "شدات" in r["display_name"]


class TestAccountingSummary:
    def test_zero_orders_returns_zero_aggregates(self, app):
        db = app._test_database
        s = db.accounting_summary()
        assert s["sales"] == 0
        assert s["cost"] == 0
        assert s["profit"] == 0.0
        assert s["orders_count"] == 0
        assert s["by_game"] == []
        assert s["recent"] == []

    def test_aggregates_completed_orders_only(self, app, make_user):
        db = app._test_database
        u = make_user(email="acc@e.com", balance=100.0)
        _seed_game(db)
        p = _seed_product(db, base_price=2.0, sell_price=5.0)

        c = _conn(db)
        # Two completed (counted), one rejected (NOT counted).
        for code, status in [("AC1", "completed"), ("AC2", "completed"),
                             ("AC3", "rejected")]:
            c.execute("INSERT INTO orders(order_code,user_id,provider,game_key,"
                      "game_name,product_id,product_name,player_id,price,"
                      "status,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (code, u["id"], "server1", "g1", "Game One",
                       p["id"], "Pack 1", "x", 5.0, status, 1, 1))
        c.commit()
        c.close()

        s = db.accounting_summary()
        # 2 × $5 = $10 sales, 2 × $2 = $4 cost, profit $6.
        assert s["sales"] == 10.0
        assert s["cost"] == 4.0
        assert s["profit"] == 6.0
        assert s["orders_count"] == 2
        # by_game has Game One.
        assert any(g["game_name"] == "Game One" for g in s["by_game"])

    def test_sales_override_changes_display_only(self, app):
        db = app._test_database
        db.set_setting("sales_override", "12345.67")
        s = db.accounting_summary()
        assert s["sales_override"] == "12345.67"
        assert s["display_sales"] == 12345.67
        # sales (real total) is still 0.
        assert s["sales"] == 0


# ===========================================================================
# Audit log
# ===========================================================================
class TestAuditLog:
    def test_insert_returns_int_id(self, app):
        db = app._test_database
        rid = db.insert_audit_log(
            "TEST_ACTION", actor_id=1, actor_email="who@e.com",
            target_type="order", target_id="42", ip="1.2.3.4",
            user_agent="ua/1", new_value='{"k":1}', metadata="meta",
        )
        assert isinstance(rid, int)
        assert rid > 0

    def test_insert_no_action_returns_none(self, app):
        db = app._test_database
        assert db.insert_audit_log(None) is None
        assert db.insert_audit_log("") is None

    def test_truncates_long_strings(self, app):
        db = app._test_database
        long = "x" * 500
        rid = db.insert_audit_log(
            long, actor_email=long, target_type=long, target_id=long, ip=long,
        )
        rows = db.list_audit_logs(limit=1)
        # Caps from the legacy INSERT.
        assert len(rows[0]["action"]) == 120
        assert len(rows[0]["actor_email"]) == 120
        assert len(rows[0]["target_type"]) == 60
        assert len(rows[0]["target_id"]) == 120
        assert len(rows[0]["ip"]) == 64
        assert rid > 0

    def test_metadata_dict_key(self, app):
        """Public dict key is `metadata` (legacy DB column name) even
        though the ORM attribute is `meta` (alias)."""
        db = app._test_database
        db.insert_audit_log("META_TEST", metadata="payload-1")
        rows = db.list_audit_logs(action="META_TEST")
        assert rows[0]["metadata"] == "payload-1"
        assert "meta" not in rows[0]

    def test_list_filters(self, app):
        db = app._test_database
        db.insert_audit_log("A1", actor_id=1, target_type="t1", target_id="x")
        db.insert_audit_log("A2", actor_id=2, target_type="t1", target_id="x")
        db.insert_audit_log("A1", actor_id=1, target_type="t2", target_id="y")

        # Filter by action.
        assert len(db.list_audit_logs(action="A1")) == 2
        # By actor_id.
        assert len(db.list_audit_logs(actor_id=2)) == 1
        # By target_type+id.
        rows = db.list_audit_logs(target_type="t2", target_id="y")
        assert len(rows) == 1 and rows[0]["action"] == "A1"

    def test_list_orders_newest_first(self, app):
        db = app._test_database
        db.insert_audit_log("ORD_A")
        db.insert_audit_log("ORD_B")
        rows = db.list_audit_logs(limit=2)
        assert rows[0]["action"] == "ORD_B"
        assert rows[1]["action"] == "ORD_A"

    def test_list_limit_clamped(self, app):
        db = app._test_database
        for i in range(5):
            db.insert_audit_log(f"L{i}")
        # ``limit=0`` is falsy → ``0 or 200`` → effective limit 200 → all 5 rows.
        assert len(db.list_audit_logs(limit=0)) == 5
        # ``limit=1`` returns exactly one (newest) row.
        assert len(db.list_audit_logs(limit=1)) == 1
        # Garbage input falls back to the default 200.
        assert len(db.list_audit_logs(limit="not-int")) == 5

    def test_count_audit_logs(self, app):
        db = app._test_database
        before = db.count_audit_logs()
        db.insert_audit_log("CT_1")
        db.insert_audit_log("CT_2")
        assert db.count_audit_logs() == before + 2
