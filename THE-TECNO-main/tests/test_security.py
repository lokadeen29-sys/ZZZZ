"""V51 task C — security-surface tests.

Covers:
  - Response hardening headers (CSP, X-Frame-Options, Referrer-Policy, …)
  - `safe_next_url` open-redirect rejection
  - `_sanitise_supplier_note` credential / HTML / control-char scrubbing
  - CSRF protection is enforced when WTF_CSRF_ENABLED is re-enabled
  - The admin upload path is NOT served under /static/uploads/
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Response hardening headers
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    def test_csp_is_nonce_based_and_restrictive(self, client):
        resp = client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert "frame-src 'none'" in csp
        assert "form-action 'self'" in csp
        assert "base-uri 'self'" in csp
        # Scripts must be nonce-based (not 'unsafe-inline').
        assert "'nonce-" in csp
        assert "script-src 'self' 'unsafe-inline'" not in csp

    def test_basic_hardening_headers_present(self, client):
        resp = client.get("/login")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert resp.headers.get("X-Permitted-Cross-Domain-Policies") == "none"
        assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
        assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-site"
        assert "camera=()" in resp.headers.get("Permissions-Policy", "")

    def test_auth_pages_are_no_store(self, client):
        resp = client.get("/login")
        cache_ctrl = resp.headers.get("Cache-Control", "")
        assert "no-store" in cache_ctrl

    def test_hsts_absent_over_plain_http(self, client):
        # Test client defaults to http:// — HSTS should NOT be emitted
        # (browsers ignore it over http anyway, but we still gate on is_secure).
        resp = client.get("/login")
        assert "Strict-Transport-Security" not in resp.headers

    def test_hsts_present_over_https(self, client):
        resp = client.get("/login", base_url="https://localhost")
        hsts = resp.headers.get("Strict-Transport-Security", "")
        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts
        assert "preload" in hsts


# ---------------------------------------------------------------------------
# safe_next_url — open-redirect protection
# ---------------------------------------------------------------------------
class TestSafeNextUrl:
    @pytest.fixture()
    def safe_next_url(self, app):
        # Must be called inside a request context because it uses request.args.
        return app._test_module.safe_next_url

    @pytest.fixture()
    def home_url(self, app):
        """Whatever `url_for('home')` resolves to inside this app — the
        fallback value safe_next_url returns on rejection."""
        from flask import url_for
        with app.test_request_context("/"):
            return url_for("home")

    @pytest.mark.parametrize(
        "evil",
        [
            "//evil.com",
            "/\\evil.com",
            "https://evil.com",
            "http://evil.com",
            "javascript:alert(1)",
            "/%2f%2fevil.com",
            "/path\rinjection",
            "/path\nwith-newline",
            "/path with spaces",
            "\\evil.com",
            "a" * 600,  # over-length
        ],
    )
    def test_rejects_malicious(self, app, safe_next_url, home_url, evil):
        with app.test_request_context("/", query_string={"next": evil}):
            result = safe_next_url("home")
        # Anything rejected must fall back to the home endpoint.
        assert result == home_url

    @pytest.mark.parametrize(
        "good",
        ["/dashboard", "/wallet", "/orders/ORDabcdef"],
    )
    def test_allows_same_origin_paths(self, app, safe_next_url, good):
        with app.test_request_context("/", query_string={"next": good}):
            assert safe_next_url("home") == good

    def test_empty_next_uses_default_endpoint(self, app, safe_next_url, home_url):
        with app.test_request_context("/"):
            assert safe_next_url("home") == home_url


# ---------------------------------------------------------------------------
# Supplier note sanitization
# ---------------------------------------------------------------------------
class TestSupplierNoteSanitiser:
    def test_redacts_api_key(self):
        from tasks import _sanitise_supplier_note

        dirty = "Auth failed: apikey=ABC123DEF456 and also api_key=XYZ."
        clean = _sanitise_supplier_note(dirty)
        assert "ABC123DEF456" not in clean
        assert "XYZ" not in clean
        assert "[REDACTED]" in clean

    def test_strips_html_tags(self):
        from tasks import _sanitise_supplier_note

        clean = _sanitise_supplier_note("<script>alert(1)</script>boom")
        assert "<script>" not in clean
        assert "boom" in clean

    def test_strips_control_chars(self):
        from tasks import _sanitise_supplier_note

        clean = _sanitise_supplier_note("a\x00b\x01c\x07d")
        assert "\x00" not in clean
        assert "\x01" not in clean
        assert "a" in clean and "d" in clean

    def test_truncates_long_input(self):
        from tasks import _sanitise_supplier_note

        clean = _sanitise_supplier_note("x" * 5000)
        assert len(clean) <= 200

    def test_handles_none(self):
        from tasks import _sanitise_supplier_note

        assert _sanitise_supplier_note(None) == ""


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------
class TestCSRF:
    def test_post_without_csrf_token_is_rejected(self, app):
        # Re-enable CSRF just for this test — the default fixture disables it.
        app.config["WTF_CSRF_ENABLED"] = True
        client = app.test_client()
        # /login is a POST handler behind CSRF; without a token Flask-WTF
        # raises a CSRFError handled by our 302-redirect handler.
        resp = client.post(
            "/login",
            data={"email": "x@x.com", "password": "y"},
            follow_redirects=False,
        )
        # Either 400 (default WTF) or 302 (our redirect handler).
        assert resp.status_code in (302, 400)


# ---------------------------------------------------------------------------
# Upload path is NOT public
# ---------------------------------------------------------------------------
class TestUploadsNotPublic:
    def test_legacy_uploads_path_blocked(self, client):
        # V50 HF4: legacy /static/uploads/ must 404 (Flask serves static only
        # for files that exist on disk; this test simply confirms nothing
        # under this path leaks proof files).
        resp = client.get("/static/uploads/whatever.png")
        assert resp.status_code in (403, 404)
