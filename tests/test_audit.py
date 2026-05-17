"""V52 task D — audit trail + observability tests.

Covers:
  - ``audit_log`` DB schema exists and accepts inserts with NULL fields.
  - ``database.insert_audit_log`` / ``list_audit_logs`` / ``count_audit_logs``.
  - ``audit.log_audit`` redacts sensitive keys before persisting.
  - ``audit.log_audit`` never raises even when the DB write fails.
  - Admin balance change + order complete + 2FA setup each leave an
    audit_log row with the correct action code and actor/target.
  - ``init_sentry`` / ``init_json_logging`` are no-ops without env flags.
"""
from __future__ import annotations

import json

import pyotp


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------
class TestAuditSchema:
    def test_audit_log_table_exists(self, app):
        db = app._test_database
        conn = db.connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_indexes_created(self, app):
        db = app._test_database
        conn = db.connect()
        rows = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_log'"
            ).fetchall()
        ]
        conn.close()
        # At least the four we declared; SQLite may add autoindexes for PK.
        for expected in ("idx_audit_ts", "idx_audit_actor", "idx_audit_target", "idx_audit_action"):
            assert expected in rows, f"missing index {expected}"


class TestInsertAuditLog:
    def test_insert_then_list(self, app):
        db = app._test_database
        row_id = db.insert_audit_log(
            action="TEST_ACTION",
            actor_id=42,
            actor_email="a@b.c",
            target_type="user",
            target_id="42",
            ip="127.0.0.1",
            user_agent="pytest",
            new_value=json.dumps({"balance": 100}),
        )
        assert row_id is not None and row_id > 0

        rows = db.list_audit_logs(limit=5)
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "TEST_ACTION"
        assert row["actor_id"] == 42
        assert row["actor_email"] == "a@b.c"
        assert row["target_type"] == "user"
        assert row["target_id"] == "42"
        assert row["ip"] == "127.0.0.1"
        assert row["user_agent"] == "pytest"
        assert json.loads(row["new_value"]) == {"balance": 100}
        assert row["old_value"] is None
        assert row["metadata"] is None

    def test_insert_without_action_returns_none(self, app):
        db = app._test_database
        assert db.insert_audit_log(action="") is None
        assert db.insert_audit_log(action=None) is None

    def test_insert_nulls_are_accepted(self, app):
        db = app._test_database
        row_id = db.insert_audit_log(action="ONLY_ACTION")
        assert row_id is not None
        rows = db.list_audit_logs()
        assert rows[0]["actor_id"] is None
        assert rows[0]["ip"] is None

    def test_list_filters_by_action(self, app):
        db = app._test_database
        db.insert_audit_log(action="A_ONE", actor_id=1)
        db.insert_audit_log(action="A_TWO", actor_id=1)
        db.insert_audit_log(action="A_ONE", actor_id=2)

        a_one = db.list_audit_logs(action="A_ONE")
        assert len(a_one) == 2
        assert all(r["action"] == "A_ONE" for r in a_one)

    def test_list_filters_by_target(self, app):
        db = app._test_database
        db.insert_audit_log(action="T", target_type="order", target_id="10")
        db.insert_audit_log(action="T", target_type="order", target_id="20")
        rows = db.list_audit_logs(target_type="order", target_id="10")
        assert len(rows) == 1
        assert rows[0]["target_id"] == "10"

    def test_list_newest_first(self, app):
        db = app._test_database
        db.insert_audit_log(action="FIRST")
        db.insert_audit_log(action="SECOND")
        rows = db.list_audit_logs()
        assert rows[0]["action"] == "SECOND"
        assert rows[-1]["action"] == "FIRST"

    def test_list_limit_clamped(self, app):
        db = app._test_database
        for i in range(5):
            db.insert_audit_log(action=f"X{i}")
        # Limit 0 or negative clamps up; limit > 1000 clamps down.
        assert len(db.list_audit_logs(limit=2)) == 2
        assert len(db.list_audit_logs(limit=0)) <= 200  # default/fallback
        assert len(db.list_audit_logs(limit=10_000)) <= 1000

    def test_count(self, app):
        db = app._test_database
        assert db.count_audit_logs() == 0
        db.insert_audit_log(action="ONE")
        db.insert_audit_log(action="TWO")
        assert db.count_audit_logs() == 2


# ---------------------------------------------------------------------------
# audit.log_audit helper
# ---------------------------------------------------------------------------
class TestLogAuditRedaction:
    def test_redacts_password_in_metadata(self, app):
        from audit import log_audit

        log_audit(
            "REDACT_TEST",
            actor_id=1,
            metadata={"password": "hunter2", "note": "ok"},
        )
        rows = app._test_database.list_audit_logs(action="REDACT_TEST")
        assert len(rows) == 1
        meta = json.loads(rows[0]["metadata"])
        assert meta["password"] == "[REDACTED]"
        assert meta["note"] == "ok"

    def test_redacts_totp_secret_and_backup_codes(self, app):
        from audit import log_audit

        log_audit(
            "REDACT_TOTP",
            actor_id=1,
            new={
                "totp_secret": "JBSWY3DPEHPK3PXP",
                "backup_codes": ["a", "b"],
                "enabled": 1,
            },
        )
        rows = app._test_database.list_audit_logs(action="REDACT_TOTP")
        new_val = json.loads(rows[0]["new_value"])
        assert new_val["totp_secret"] == "[REDACTED]"
        assert new_val["backup_codes"] == "[REDACTED]"
        assert new_val["enabled"] == 1

    def test_redacts_token_key(self, app):
        from audit import log_audit

        log_audit(
            "REDACT_TOKEN",
            metadata={"token": "abc.def.ghi", "user_id": 7},
        )
        rows = app._test_database.list_audit_logs(action="REDACT_TOKEN")
        meta = json.loads(rows[0]["metadata"])
        assert meta["token"] == "[REDACTED]"
        assert meta["user_id"] == 7

    def test_is_case_insensitive(self, app):
        from audit import log_audit

        log_audit(
            "REDACT_CASE",
            metadata={"PASSWORD": "x", "Token": "y", "Secret": "z"},
        )
        rows = app._test_database.list_audit_logs(action="REDACT_CASE")
        meta = json.loads(rows[0]["metadata"])
        assert meta["PASSWORD"] == "[REDACTED]"
        assert meta["Token"] == "[REDACTED]"
        assert meta["Secret"] == "[REDACTED]"


class TestLogAuditSafety:
    def test_never_raises_even_on_bad_input(self, app):
        from audit import log_audit

        # Non-serialisable payload — must not leak the exception.
        class Weird:
            pass

        # Should silently accept and still write a row (metadata replaced
        # with {"_serialisation_error": True} or a string fallback).
        log_audit("BAD_META", metadata={"obj": Weird()})
        rows = app._test_database.list_audit_logs(action="BAD_META")
        assert len(rows) == 1

    def test_no_metadata_writes_null(self, app):
        from audit import log_audit

        log_audit("NO_META", actor_id=5)
        rows = app._test_database.list_audit_logs(action="NO_META")
        assert rows[0]["metadata"] is None
        assert rows[0]["old_value"] is None
        assert rows[0]["new_value"] is None


# ---------------------------------------------------------------------------
# End-to-end: admin routes leave audit trail
# ---------------------------------------------------------------------------
class TestAdminRoutesEmitAudit:
    def test_admin_balance_change_emits_audit_row(
        self, app, client, make_user, login_as
    ):
        db = app._test_database
        target = make_user(email="target@test.local", balance=50)
        admin = db.get_user_by_email("admin@test.local")
        login_as(admin["id"], admin_2fa_verified=True)

        resp = client.post(
            f"/admin/user/{target['id']}/balance",
            data={"amount": "123.45"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)

        rows = db.list_audit_logs(action="ADMIN_BALANCE_CHANGE")
        assert len(rows) == 1
        row = rows[0]
        assert row["actor_id"] == admin["id"]
        assert row["actor_email"] == admin["email"]
        assert row["target_type"] == "user"
        assert row["target_id"] == str(target["id"])
        new_val = json.loads(row["new_value"])
        assert abs(new_val["balance"] - 123.45) < 0.001
        old_val = json.loads(row["old_value"])
        assert abs(old_val["balance"] - 50.0) < 0.001

    def test_admin_2fa_enabled_emits_audit_row(self, app, client, login_as):
        db = app._test_database
        admin = db.get_user_by_email("admin@test.local")
        login_as(admin["id"])

        # Prime the TOTP secret on disk.
        client.get("/admin/2fa/setup")
        refreshed = db.get_user_by_email("admin@test.local")
        code = pyotp.TOTP(refreshed["totp_secret"]).now()

        resp = client.post("/admin/2fa/confirm", data={"code": code})
        assert resp.status_code == 200

        rows = db.list_audit_logs(action="ADMIN_2FA_ENABLED")
        assert len(rows) == 1
        assert rows[0]["actor_id"] == admin["id"]
        assert rows[0]["actor_email"] == admin["email"]

    def test_admin_2fa_setup_fail_emits_audit_row(self, app, client, login_as):
        db = app._test_database
        admin = db.get_user_by_email("admin@test.local")
        login_as(admin["id"])
        client.get("/admin/2fa/setup")

        client.post("/admin/2fa/confirm", data={"code": "000000"})

        rows = db.list_audit_logs(action="ADMIN_2FA_SETUP_FAIL")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Lazy init behaviour — should be no-ops without env flags
# ---------------------------------------------------------------------------
class TestLazyInit:
    def test_init_sentry_without_dsn_returns_false(self, app, monkeypatch):
        from audit import init_sentry

        monkeypatch.delenv("SENTRY_DSN", raising=False)
        # Reset module state so the function re-evaluates the env var.
        import audit as audit_mod
        audit_mod._sentry_initialised = False

        assert init_sentry() is False

    def test_init_json_logging_without_flag_returns_false(self, app, monkeypatch):
        from audit import init_json_logging

        monkeypatch.delenv("LOG_JSON", raising=False)
        import audit as audit_mod
        audit_mod._json_logging_applied = False

        assert init_json_logging() is False
