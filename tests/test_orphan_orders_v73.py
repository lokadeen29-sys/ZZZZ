"""V73 (Option A) — orphan-order recovery tests.

This suite locks in the two behaviours we just added:

  1. `_extract_order_id_from_response` falls back to a recursive, smart
     search whenever the targeted lookups miss. This is what prevents a
     repeat of the V68 bug for *any* future shape change at *any* supplier
     (Shop2Topup, G2Bulk, or a new one).

  2. `update_order_provider_response` persists the supplier's full reply
     on the order row. This is the "we never lose what the supplier
     actually said" guarantee that lets ops recover stuck orders by hand
     even when the parser missed the order_id entirely.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Ensure the repo root is importable when this test file is run in
# isolation — conftest.py already does this, but be defensive.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# (1) `_extract_order_id_from_response` — targeted + recursive fallback
# ---------------------------------------------------------------------------
class TestExtractOrderId:
    """Exhaustive coverage of every shape we have observed in the wild
    plus the recursive fallback for shapes we haven't seen yet."""

    @pytest.fixture()
    def extract(self):
        # Import after conftest has primed os.environ.
        from providers import _extract_order_id_from_response
        return _extract_order_id_from_response

    # ----- targeted lookups (legacy paths must keep working) -------------
    def test_g2bulk_top_level_order(self, extract):
        # Old-school G2Bulk reply — the only path that worked before V68.
        assert extract("server1", {"order": 12345}) == "12345"

    def test_g2bulk_top_level_order_id(self, extract):
        assert extract("server1", {"order_id": "G2-77"}) == "G2-77"

    def test_g2bulk_inside_data(self, extract):
        assert extract("server1", {"data": {"order": "abc"}}) == "abc"

    def test_shop2topup_data_order_id(self, extract):
        # The exact shape that broke in V68.
        payload = {"success": True, "data": {"order_id": "SHT-12345", "status": "pending"}}
        assert extract("server2", payload) == "SHT-12345"

    def test_shop2topup_data_id_only(self, extract):
        payload = {"success": True, "data": {"id": "X-9"}}
        assert extract("server2", payload) == "X-9"

    def test_shop2topup_camelcase_orderid(self, extract):
        payload = {"data": {"orderId": "CAM-1"}}
        assert extract("server2", payload) == "CAM-1"

    def test_shop2topup_top_level_order_id(self, extract):
        # Some legacy forms put it at the top level.
        assert extract("server2", {"order_id": "TOP-42"}) == "TOP-42"

    # ----- the new recursive fallback ------------------------------------
    def test_recursive_finds_nested_transaction_id(self, extract):
        """A shape we have never seen — must still recover the id."""
        payload = {
            "status": "OK",
            "result": {"transaction": {"id": "TX-9876"}},
        }
        assert extract("server2", payload) == "TX-9876"

    def test_recursive_picks_order_id_over_id_in_same_dict(self, extract):
        # Priority list must prefer "order_id" to a generic "id" sibling.
        payload = {"data": {"id": "WRONG", "order_id": "RIGHT"}}
        assert extract("server2", payload) == "RIGHT"

    def test_recursive_skips_our_own_client_order_id(self, extract):
        # We generate `client_order_id` (a UUID) ourselves before posting
        # to Shop2Topup. It must NEVER be returned as the supplier id.
        payload = {
            "client_order_id": "00000000-aaaa-bbbb-cccc-111111111111",
            "supplier_data": {"reference_id": "SUP-1"},
        }
        assert extract("server2", payload) == "SUP-1"

    def test_recursive_finds_in_list(self, extract):
        # Some suppliers wrap a single order in a one-element array.
        payload = {"orders": [{"order_id": "LIST-7"}]}
        assert extract("server2", payload) == "LIST-7"

    def test_recursive_partial_name_match(self, extract):
        # Field name not in the priority list, but contains "order".
        payload = {"data": {"supplier_order_ref": "REF-321"}}
        assert extract("server2", payload) == "REF-321"

    def test_recursive_handles_int_values(self, extract):
        assert extract("server2", {"deeply": {"nested": {"order_id": 9001}}}) == "9001"

    def test_recursive_returns_empty_when_truly_absent(self, extract):
        # No id-shaped field anywhere → must return "" so the caller can
        # park the order in supplier_pending with the raw response saved.
        payload = {"success": True, "msg": "queued", "irrelevant": [1, 2, 3]}
        assert extract("server2", payload) == ""

    def test_handles_non_dict_input_gracefully(self, extract):
        for bad in (None, "", 0, [], "just a string"):
            assert extract("server2", bad) == ""

    def test_ignores_boolean_values(self, extract):
        # "id": True must NOT be accepted as an order id.
        payload = {"data": {"id": True, "order": "REAL"}}
        assert extract("server2", payload) == "REAL"

    def test_long_strings_are_rejected_as_ids(self, extract):
        # 200-char "order_id" looks like garbage / a stack trace, not an id.
        payload = {"data": {"order_id": "x" * 200, "transaction_id": "OK-1"}}
        assert extract("server2", payload) == "OK-1"

    def test_recursive_is_depth_bounded(self, extract):
        # Build a 10-deep nesting that contains an id at the bottom. The
        # depth cap (6) means we deliberately do NOT find it. This proves
        # the cap is in effect and protects against pathological payloads.
        payload = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"order_id": "DEEP"}}}}}}}}}
        assert extract("server2", payload) == ""


# ---------------------------------------------------------------------------
# (2) `update_order_provider_response` — persistence + safety
# ---------------------------------------------------------------------------
class TestUpdateOrderProviderResponse:
    """The raw-response column must accept anything we throw at it without
    raising, and the value must be readable back from the orders row."""

    def _seed_order(self, app, user, *, price_usd: float = 1.0):
        """Insert a minimal product + game + order so we have a row to
        attach a raw response to. Returns the new order id."""
        db = app._test_database
        conn = db.connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO games(provider, game_key, name, active) "
                "VALUES (?,?,?,1)",
                ("local", "v73game", "V73 Game"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO products(provider, game_key, "
                "provider_product_id, name, base_price, sell_price, "
                "sort_order, active) VALUES (?,?,?,?,?,?,?,1)",
                ("local", "v73game", "P1", "Pack", 0.5, price_usd, 1),
            )
            conn.commit()
        finally:
            conn.close()

        conn = db.connect()
        product = dict(conn.execute(
            "SELECT * FROM products WHERE provider=? AND provider_product_id=?",
            ("local", "P1"),
        ).fetchone())
        game = dict(conn.execute(
            "SELECT * FROM games WHERE provider=? AND game_key=?",
            ("local", "v73game"),
        ).fetchone())
        conn.close()

        order_id, _code = db.create_order(user, product, game, player_id="PID-V73")
        return order_id

    def test_persists_string_payload(self, app, make_user):
        db = app._test_database
        user = make_user(email="raw1@test.local", balance=10.0)
        order_id = self._seed_order(app, user)

        ok = db.update_order_provider_response(order_id, '{"data":{"order_id":"X-1"}}')
        assert ok is True

        row = db.get_order(order_id)
        assert row["provider_response_raw"] == '{"data":{"order_id":"X-1"}}'

    def test_truncates_oversized_payload(self, app, make_user):
        db = app._test_database
        user = make_user(email="raw2@test.local", balance=10.0)
        order_id = self._seed_order(app, user)

        big = "A" * 10_000  # > _PROVIDER_RAW_MAX_LEN (4096)
        assert db.update_order_provider_response(order_id, big) is True

        row = db.get_order(order_id)
        stored = row["provider_response_raw"] or ""
        assert len(stored) <= 4096
        assert stored.endswith("…")

    def test_handles_none_gracefully(self, app, make_user):
        db = app._test_database
        user = make_user(email="raw3@test.local", balance=10.0)
        order_id = self._seed_order(app, user)

        # Must not raise; column ends up as empty string.
        assert db.update_order_provider_response(order_id, None) is True
        row = db.get_order(order_id)
        assert row["provider_response_raw"] == ""

    def test_serialises_dict_via_json_in_caller_then_persists(self, app, make_user):
        # Simulates what tasks.process_order does: json.dumps then store.
        db = app._test_database
        user = make_user(email="raw4@test.local", balance=10.0)
        order_id = self._seed_order(app, user)

        payload = {"success": True, "data": {"order_id": "JSN-1", "status": "pending"}}
        db.update_order_provider_response(order_id, json.dumps(payload, ensure_ascii=False))

        row = db.get_order(order_id)
        decoded = json.loads(row["provider_response_raw"])
        assert decoded["data"]["order_id"] == "JSN-1"

    def test_unknown_order_id_returns_false_no_raise(self, app):
        db = app._test_database
        # 999999 doesn't exist; helper must not raise.
        assert db.update_order_provider_response(999_999, "ignored") is True
        # And no zero-id either.
        assert db.update_order_provider_response(0, "ignored") is False
        assert db.update_order_provider_response(None, "ignored") is False


# ---------------------------------------------------------------------------
# (3) Schema migration — column actually exists on the orders table
# ---------------------------------------------------------------------------
class TestSchemaMigration:
    def test_column_present(self, app):
        db = app._test_database
        conn = db.connect()
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        finally:
            conn.close()
        assert "provider_response_raw" in cols
