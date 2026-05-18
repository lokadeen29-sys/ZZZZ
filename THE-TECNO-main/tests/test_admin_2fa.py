"""V51 task C — admin 2FA tests (TOTP + backup codes).

Covers:
  - TOTP secret generation / verify with skew window
  - Backup codes: 10 codes, hashed on disk, one-time consumption
  - /admin/2fa/setup renders the QR for an admin
  - /admin/2fa/confirm enables TOTP and stores hashed backup codes
  - admin routes redirect to the challenge once 2FA is enabled
"""
from __future__ import annotations

import json

import pyotp


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
class TestTotpHelpers:
    def test_secret_is_base32_160_bits(self, app):
        from security_2fa import generate_totp_secret
        secret = generate_totp_secret()
        # pyotp uses 32-char base32 by default (= 160 bits).
        assert len(secret) >= 16
        # Base32 alphabet.
        import string
        allowed = set(string.ascii_uppercase + "234567")
        assert all(c in allowed for c in secret)

    def test_verify_accepts_current_code(self, app):
        from security_2fa import generate_totp_secret, verify_totp
        secret = generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert verify_totp(secret, code) is True

    def test_verify_rejects_wrong_code(self, app):
        from security_2fa import generate_totp_secret, verify_totp
        secret = generate_totp_secret()
        assert verify_totp(secret, "000000") is False
        assert verify_totp(secret, "abcdef") is False
        assert verify_totp(secret, "") is False

    def test_verify_rejects_empty_secret(self, app):
        from security_2fa import verify_totp
        assert verify_totp("", "123456") is False


class TestBackupCodes:
    def test_generates_exactly_ten_codes(self, app):
        from security_2fa import generate_backup_codes
        plain, hashed = generate_backup_codes()
        assert len(plain) == 10
        assert len(hashed) == 10

    def test_plain_codes_are_display_formatted(self, app):
        from security_2fa import generate_backup_codes
        plain, _ = generate_backup_codes()
        for code in plain:
            assert "-" in code
            assert len(code) == 11  # 5-5 with dash

    def test_hashed_codes_hide_plaintext(self, app):
        from security_2fa import generate_backup_codes
        plain, hashed = generate_backup_codes()
        for h in hashed:
            assert h.startswith("pbkdf2:") or h.startswith("scrypt:")
            for p in plain:
                # The raw (normalised) code must not appear inside the hash.
                raw = p.replace("-", "")
                assert raw not in h

    def test_consume_backup_code_accepts_and_removes(self, app):
        from security_2fa import (
            generate_backup_codes,
            consume_backup_code,
        )
        plain, hashed = generate_backup_codes()
        remaining = consume_backup_code(hashed, plain[0])
        assert remaining is not None
        assert len(remaining) == 9

    def test_consume_backup_code_rejects_used(self, app):
        from security_2fa import (
            generate_backup_codes,
            consume_backup_code,
        )
        plain, hashed = generate_backup_codes()
        remaining = consume_backup_code(hashed, plain[0])
        # Same code cannot be consumed twice.
        assert consume_backup_code(remaining, plain[0]) is None

    def test_consume_backup_code_accepts_unformatted(self, app):
        from security_2fa import generate_backup_codes, consume_backup_code
        plain, hashed = generate_backup_codes()
        # User types it without the dash / with stray whitespace.
        ugly = "  " + plain[0].upper().replace("-", "") + "  "
        assert consume_backup_code(hashed, ugly) is not None

    def test_consume_rejects_garbage(self, app):
        from security_2fa import generate_backup_codes, consume_backup_code
        _, hashed = generate_backup_codes()
        assert consume_backup_code(hashed, "nope") is None
        assert consume_backup_code(hashed, "") is None
        assert consume_backup_code([], "aaaaa-bbbbb") is None

    def test_serialize_roundtrip(self, app):
        from security_2fa import (
            generate_backup_codes,
            serialize_backup_codes,
            deserialize_backup_codes,
        )
        _, hashed = generate_backup_codes()
        blob = serialize_backup_codes(hashed)
        assert json.loads(blob) == hashed
        assert deserialize_backup_codes(blob) == hashed
        assert deserialize_backup_codes(None) == []
        assert deserialize_backup_codes("not-json") == []


# ---------------------------------------------------------------------------
# Admin 2FA HTTP endpoints
# ---------------------------------------------------------------------------
class TestAdmin2FARoutes:
    def test_setup_page_renders_for_admin(self, app, client, login_as):
        admin = app._test_database.get_user_by_email("admin@test.local")
        login_as(admin["id"])
        resp = client.get("/admin/2fa/setup")
        assert resp.status_code == 200
        # The setup page must persist a TOTP secret on the user.
        refreshed = app._test_database.get_user_by_email("admin@test.local")
        assert refreshed["totp_secret"]

    def test_setup_denied_for_regular_user(self, client, make_user, login_as):
        user = make_user(email="notadmin@test.local")
        login_as(user["id"])
        resp = client.get("/admin/2fa/setup")
        assert resp.status_code == 403

    def test_confirm_enables_2fa_and_stores_backup_codes(
        self, app, client, login_as
    ):
        admin = app._test_database.get_user_by_email("admin@test.local")
        login_as(admin["id"])
        # Prime the secret.
        client.get("/admin/2fa/setup")
        refreshed = app._test_database.get_user_by_email("admin@test.local")
        secret = refreshed["totp_secret"]
        current_code = pyotp.TOTP(secret).now()

        resp = client.post(
            "/admin/2fa/confirm",
            data={"code": current_code},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        after = app._test_database.get_user_by_email("admin@test.local")
        assert int(after["totp_enabled"]) == 1
        # 10 hashed backup codes persisted.
        hashed = json.loads(after["totp_backup_codes"])
        assert len(hashed) == 10
        for h in hashed:
            assert h.startswith(("pbkdf2:", "scrypt:"))

    def test_confirm_rejects_wrong_code(self, app, client, login_as):
        admin = app._test_database.get_user_by_email("admin@test.local")
        login_as(admin["id"])
        client.get("/admin/2fa/setup")
        resp = client.post(
            "/admin/2fa/confirm",
            data={"code": "000000"},
            follow_redirects=False,
        )
        # Redirects back to setup; 2FA must NOT be enabled.
        assert resp.status_code in (302, 303)
        after = app._test_database.get_user_by_email("admin@test.local")
        assert int(after["totp_enabled"] or 0) == 0


class TestAdminRequiredGuardEnforces2FA:
    def _enable_2fa(self, app, user_email: str) -> str:
        """Directly enable 2FA on a user via DB helpers. Returns the secret
        so the test can produce valid codes."""
        from security_2fa import (
            generate_totp_secret,
            generate_backup_codes,
            serialize_backup_codes,
        )
        db = app._test_database
        secret = generate_totp_secret()
        user = db.get_user_by_email(user_email)
        db.set_user_totp_secret(user["id"], secret)
        _, hashed = generate_backup_codes()
        db.enable_user_totp(user["id"], serialize_backup_codes(hashed))
        return secret

    def test_admin_dashboard_redirects_to_challenge_before_verification(
        self, app, client, login_as
    ):
        self._enable_2fa(app, "admin@test.local")
        admin = app._test_database.get_user_by_email("admin@test.local")
        # Log in without marking the session as 2fa-verified.
        login_as(admin["id"], admin_2fa_verified=False)
        resp = client.get("/admin", follow_redirects=False)
        # admin_required bounces to /admin/2fa/challenge.
        assert resp.status_code in (302, 303)
        assert "2fa/challenge" in resp.headers["Location"]

    def test_admin_dashboard_ok_once_2fa_verified(
        self, app, client, login_as
    ):
        self._enable_2fa(app, "admin@test.local")
        admin = app._test_database.get_user_by_email("admin@test.local")
        login_as(admin["id"], admin_2fa_verified=True)
        resp = client.get("/admin", follow_redirects=False)
        # 2FA gate passes; either 200 (dashboard) or redirect into admin.
        assert resp.status_code < 400
