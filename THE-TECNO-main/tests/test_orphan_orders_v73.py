"""V73 orphan-recovery — extractor + persistence + schema-migration tests.

Three concerns, three sections:

  1. ``providers._extract_order_id_from_response`` — 19 scenarios
     covering legacy V68 shapes, the new recursive fallback, priority
     ordering, depth/value filters, and the ``client_order_id`` ignore.

  2. ``database.update_order_provider_response`` — 3 scenarios for the
     forensic-write helper (truncation, ``None``, unknown order id).

  3. Alembic migration ``0002_orders_provider_response_raw`` — verifies
     the new column AND the orphan-watch index land on a fresh DB.

The extractor section depends only on ``providers.py``, so it imports
without going through the ``app`` fixture (no DB / no Flask). The
persistence section uses the standard ``app`` fixture (function-scoped,
isolated SQLite). The migration section runs Alembic against a brand
new tmp DB, exactly like ``tests/test_alembic.py``.

If the user's local sandbox is missing Alembic (rare) the migration
test class is auto-skipped; the rest still runs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make repo root importable when this file is run directly via pytest from
# a subdirectory. Mirrors tests/test_alembic.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from providers import _extract_order_id_from_response  # noqa: E402


# ===========================================================================
# Section 1 — extractor (19 scenarios)
# ===========================================================================
#
# We intentionally keep these scenarios as small dict literals (no module-
# level fixtures) so each test is self-contained and you can copy a single
# `assert` line into a REPL to debug a regression.

class TestExtractorLegacyV68Shapes:
    """V68-era shapes — the extractor must remain backward-compatible.

    These are the responses healthy suppliers were already returning
    before the orphan bug; they MUST keep working bit-for-bit so the
    rewrite does not regress already-paid traffic.
    """

    def test_server1_top_level_order(self):
        # G2Bulk happy path: top-level "order" key.
        assert (
            _extract_order_id_from_response("server1", {"order": "ABC123"})
            == "ABC123"
        )

    def test_server1_top_level_order_id(self):
        assert (
            _extract_order_id_from_response("server1", {"order_id": "X-9"})
            == "X-9"
        )

    def test_server1_data_id(self):
        # G2Bulk legacy: id nested under data.
        resp = {"data": {"id": "g2b-42"}}
        assert _extract_order_id_from_response("server1", resp) == "g2b-42"

    def test_server2_data_order_id(self):
        # Shop2Topup happy path documented in V68.
        resp = {"success": True, "data": {"order_id": "S2T-100"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "S2T-100"
        )

    def test_server2_data_id(self):
        # Shop2Topup variant: only `id`.
        resp = {"success": True, "data": {"id": "S2T-101"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "S2T-101"
        )

    def test_server2_data_orderid_camelcase(self):
        resp = {"success": True, "data": {"orderId": "camel-7"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "camel-7"
        )


class TestExtractorRecursiveFallback:
    """The actual V73 fix — recursive search recovers ids that V68 missed."""

    def test_server2_data_transaction_id(self):
        # The order 32 production case: server2 returned transaction_id
        # under data, not order_id, so V68 missed it.
        resp = {"success": True, "data": {"transaction_id": "TXN-32"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "TXN-32"
        )

    def test_data_order_nested(self):
        # Two levels deep: data.order.id (some Shop2Topup variants).
        resp = {"success": True, "data": {"order": {"id": "deep-1"}}}
        assert (
            _extract_order_id_from_response("server2", resp) == "deep-1"
        )

    def test_data_transaction_nested(self):
        # Two levels deep with the transaction_id substring fallback.
        resp = {
            "success": True,
            "data": {"transaction": {"id": "tx-deep-2"}},
        }
        assert (
            _extract_order_id_from_response("server2", resp) == "tx-deep-2"
        )

    def test_data_as_scalar_string(self):
        # ``data`` IS the id (rare but seen on lightweight suppliers).
        resp = {"success": True, "data": "FLAT-77"}
        assert (
            _extract_order_id_from_response("server2", resp) == "FLAT-77"
        )

    def test_data_as_scalar_number(self):
        resp = {"success": True, "data": 90210}
        assert (
            _extract_order_id_from_response("server2", resp) == "90210"
        )


class TestExtractorPriorityAndSubstring:
    """Priority list + substring fallback ordering."""

    def test_order_id_beats_id_when_both_present(self):
        # ``id`` is present at every level, but ``order_id`` should win
        # because it is higher in the priority list.
        resp = {
            "data": {"id": "noisy-id", "order_id": "the-real-one"},
        }
        assert (
            _extract_order_id_from_response("server2", resp)
            == "the-real-one"
        )

    def test_tracking_id_matched_via_priority(self):
        resp = {"data": {"tracking_id": "TRK-1"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "TRK-1"
        )

    def test_reference_id_matched_via_priority(self):
        resp = {"data": {"reference_id": "REF-1"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "REF-1"
        )

    def test_substring_fallback_picks_unknown_supplier_key(self):
        # Custom supplier returns ``external_order_no`` — neither in
        # known V68 paths nor in the priority list. The substring scan
        # over "order" should catch it.
        resp = {"success": True, "data": {"external_order_no": "EXT-99"}}
        assert (
            _extract_order_id_from_response("server2", resp) == "EXT-99"
        )


class TestExtractorFiltersAndIgnores:
    """Depth cap, value-shape filter, and the client_order_id guard."""

    def test_depth_cap_seven_levels_returns_empty(self):
        # Build an 8-deep dict; depth cap is 6. Should NOT find anything.
        node = {"order_id": "TOO-DEEP"}
        for _ in range(8):
            node = {"data": node}
        # The recursive walk will not reach the inner ``order_id``,
        # AND the known-paths fast path can't either (it only inspects
        # the top-level + 1 level under ``data``).
        assert (
            _extract_order_id_from_response("server2", node) == ""
        )

    def test_value_filter_rejects_bool(self):
        # ``True`` would otherwise stringify as the literal "True".
        resp = {"data": {"order_id": True}}
        assert (
            _extract_order_id_from_response("server2", resp) == ""
        )

    def test_value_filter_rejects_empty_and_oversize(self):
        # Empty + 200-char garbage should both be filtered out, leaving
        # the genuine value to win.
        resp = {
            "data": {
                "order_id": "",
                "transaction_id": "X" * 200,  # too long
                "tracking_id": "OK-1",
            }
        }
        assert (
            _extract_order_id_from_response("server2", resp) == "OK-1"
        )

    def test_ignores_client_order_id_when_alone(self):
        # ``client_order_id`` is the UUID we mint locally — even when
        # it's the ONLY id-shaped key in the response we must NOT
        # surface it as the supplier's id.
        resp = {
            "success": True,
            "data": {"client_order_id": "uuid-our-own"},
        }
        assert (
            _extract_order_id_from_response("server2", resp) == ""
        )


class TestExtractorDefensiveInputs:
    """Inputs that real production code can deliver."""

    def test_invalid_response_returns_empty(self):
        # Non-dict (e.g. parser short-circuited and returned a list).
        assert _extract_order_id_from_response("server2", []) == ""  # type: ignore[arg-type]
        assert _extract_order_id_from_response("server2", "noop") == ""  # type: ignore[arg-type]

    def test_empty_dict_returns_empty(self):
        assert _extract_order_id_from_response("server2", {}) == ""


# ===========================================================================
# Section 2 — persistence (3 scenarios)
# ===========================================================================
#
# These tests use the shared ``app`` fixture, which seeds an isolated
# SQLite DB with the legacy schema (init_db) and the V73 column added
# via the ALTER TABLE in ``_init_db_inner``.

class TestUpdateOrderProviderResponse:
    """V73 — ``database.update_order_provider_response``.

    Contract reminders (see docstring on the function itself):
      - never raises;
      - dict / list payloads are JSON-serialised;
      - capped at 4 KiB with a "…" suffix on overflow;
      - missing order id is a silent no-op.
    """

    def _seed_order(self, app):
        """Helper: insert a minimal order row + return its id.

        We use ``database.create_order`` so the V47 atomic balance
        path stays exercised; the user is funded with $1000 and the
        product is a $1 item, which is enough for the tests.
        """
        database = app._test_database

        # User with enough balance.
        ok, _ = database.create_user(
            "T", "owner@v73.local", "", "Pass1234!",
            email_verified=1,
        )
        assert ok
        user = database.get_user_by_email("owner@v73.local")
        database.set_user_balance(user["id"], 1000.0)

        # Game + product so create_order has something to charge for.
        database.upsert_game("server2", "ff", "FF", "🔥", 1)
        database.upsert_product(
            "server2", "ff", "PROD-1", "Diamonds 100",
            base_price=0.50, sell_price=1.00, active=1,
        )
        # ``list_all_products_for_admin`` returns the row with the new
        # internal id we need to feed back to ``create_order``.
        product = database.get_product_by_id(
            database.list_all_products_for_admin("server2", "ff")[0]["id"]
        )
        game = database.get_game("server2", "ff")
        order_id, _code = database.create_order(
            user, product, game, player_id="player-1",
        )
        return order_id

    def test_dict_response_is_json_serialised(self, app):
        database = app._test_database
        order_id = self._seed_order(app)

        payload = {
            "success": True,
            "data": {"order_id": "S2T-200", "status": "pending"},
        }
        # Must not raise.
        database.update_order_provider_response(order_id, payload)

        row = database.get_order(order_id)
        saved = row.get("provider_response_raw")
        assert saved is not None
        # Round-trips back to the same dict.
        parsed = json.loads(saved)
        assert parsed == payload

    def test_oversize_payload_is_truncated_with_ellipsis(self, app):
        database = app._test_database
        order_id = self._seed_order(app)

        # 5 KiB of Xs — well over the 4 KiB cap.
        big = "X" * 5000
        database.update_order_provider_response(order_id, big)

        row = database.get_order(order_id)
        saved = row.get("provider_response_raw")
        assert saved is not None
        assert len(saved) == 4096
        assert saved.endswith("…")
        assert saved.startswith("X" * 100)  # body preserved

    def test_none_is_silent_noop(self, app):
        database = app._test_database
        order_id = self._seed_order(app)

        # Should NOT raise. Should NOT touch the column.
        database.update_order_provider_response(order_id, None)

        row = database.get_order(order_id)
        assert row.get("provider_response_raw") in (None, "")

    def test_unknown_order_id_is_silent_noop(self, app):
        database = app._test_database
        # No setup — just call with a clearly-missing id. Must not raise.
        database.update_order_provider_response(99_999_999, {"k": "v"})


# ===========================================================================
# Section 3 — Alembic migration 0002
# ===========================================================================

# Skip the whole class if alembic isn't installed in this venv (matches
# tests/test_alembic.py's defensive style).
alembic = pytest.importorskip("alembic", reason="alembic not installed")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402

_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def _alembic_cfg(db_path: Path) -> Config:
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    # Force-reload app.db.* so env.py sees the new URL.
    for mod in ("app.db.base", "app.db.session", "app.db", "app.db.models"):
        sys.modules.pop(mod, None)
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return cfg


class TestMigration0002OrdersProviderResponseRaw:
    """Alembic migration ``0002_orders_provider_response_raw``."""

    def test_upgrade_head_adds_provider_response_raw_column(self, tmp_path):
        cfg = _alembic_cfg(tmp_path / "v73.db")
        command.upgrade(cfg, "head")

        engine = create_engine(
            f"sqlite:///{tmp_path / 'v73.db'}", future=True
        )
        cols = {
            c["name"]
            for c in inspect(engine).get_columns("orders")
        }
        assert "provider_response_raw" in cols, (
            "V73 migration 0002 did not add orders.provider_response_raw"
        )

    def test_upgrade_head_creates_idx_orders_orphan(self, tmp_path):
        cfg = _alembic_cfg(tmp_path / "v73.db")
        command.upgrade(cfg, "head")

        engine = create_engine(
            f"sqlite:///{tmp_path / 'v73.db'}", future=True
        )
        index_names = {
            idx["name"]
            for idx in inspect(engine).get_indexes("orders")
        }
        assert "idx_orders_orphan" in index_names, (
            "V73 orphan-watch index missing after migration upgrade"
        )

    def test_downgrade_round_trip_is_clean(self, tmp_path):
        """upgrade head -> downgrade 0001 -> upgrade head again ends in
        the same shape. Catches typos in migration 0002's downgrade()."""
        cfg = _alembic_cfg(tmp_path / "v73.db")

        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0001_baseline")

        engine = create_engine(
            f"sqlite:///{tmp_path / 'v73.db'}", future=True
        )
        cols = {
            c["name"]
            for c in inspect(engine).get_columns("orders")
        }
        assert "provider_response_raw" not in cols, (
            "downgrade did not drop provider_response_raw column"
        )

        # Re-upgrade and re-check.
        command.upgrade(cfg, "head")
        engine = create_engine(
            f"sqlite:///{tmp_path / 'v73.db'}", future=True
        )
        cols = {
            c["name"]
            for c in inspect(engine).get_columns("orders")
        }
        assert "provider_response_raw" in cols
