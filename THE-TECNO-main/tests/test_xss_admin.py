"""
V53 security — Stored XSS sanitization tests.

Verifies that admin input routes strip dangerous HTML/JS from text fields
before persisting to the database.
"""
from __future__ import annotations

import secrets


def test_payment_method_strips_script_tag(app, client, make_user, login_as):
    """Admin payment method edit must strip <script> and event handlers."""
    database = app._test_database

    # Create an admin and log in
    admin = make_user(email="xss-admin@test.local", password="Admin123!", role="admin")
    login_as(admin["id"], admin_2fa_verified=True)

    # Pick a payment method that was seeded by init_db
    methods = database.list_payment_methods()
    assert methods, "No payment methods seeded"
    method_id = methods[0]["id"]

    # POST with XSS payloads
    resp = client.post(f"/admin/payment-method/{method_id}", data={
        "name": "<script>alert(1)</script>Test Method",
        "emoji": "💳",
        "address": "ADDR<img src=x onerror=alert(1)>",
        "instructions": 'Click <a href="javascript:alert(1)">here</a> for help',
        "active": "1",
        "currency": "USD",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    # Verify DB content is sanitized
    m = database.get_payment_method(method_id)
    assert "<script>" not in m["name"]
    assert "Test Method" in m["name"]  # text content preserved

    assert "onerror" not in m["address"]
    assert "<img" not in m["address"]
    assert "ADDR" in m["address"]

    assert "javascript:" not in m["instructions"]


def test_payment_method_rich_text_allows_safe_html(app, client, make_user, login_as):
    """Instructions field should allow safe tags like <b>, <br>, <a href=https>."""
    database = app._test_database

    admin = make_user(email="xss-admin2@test.local", password="Admin123!", role="admin")
    login_as(admin["id"], admin_2fa_verified=True)

    methods = database.list_payment_methods()
    method_id = methods[0]["id"]

    resp = client.post(f"/admin/payment-method/{method_id}", data={
        "name": "Safe Method",
        "emoji": "💳",
        "address": "123 Main St",
        "instructions": '<b>Important:</b> Send to <a href="https://example.com">link</a>',
        "active": "1",
        "currency": "USD",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    m = database.get_payment_method(method_id)
    assert "<b>" in m["instructions"]
    assert "https://example.com" in m["instructions"]


def test_admin_add_game_strips_xss(app, client, make_user, login_as):
    """Game name and image_url must be stripped of HTML."""
    admin = make_user(email="xss-admin3@test.local", password="Admin123!", role="admin")
    login_as(admin["id"], admin_2fa_verified=True)

    resp = client.post("/admin/games/add", data={
        "provider": "server1",
        "game_key": "xss_test_game",
        "name": '<img src=x onerror=alert("xss")>Cool Game',
        "emoji": "🎮",
        "image_url": '"><script>alert(1)</script>',
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    database = app._test_database
    game = database.get_game("server1", "xss_test_game")
    assert game is not None
    assert "<img" not in game["name"]
    assert "onerror" not in game["name"]
    assert "Cool Game" in game["name"]
    assert "<script>" not in game["image_url"]


def test_admin_settings_strips_xss(app, client, make_user, login_as):
    """Settings free-text fields must be sanitized."""
    database = app._test_database

    admin = make_user(email="xss-admin4@test.local", password="Admin123!", role="admin")
    login_as(admin["id"], admin_2fa_verified=True)

    resp = client.post("/admin/settings", data={
        "support_contact": '<script>steal()</script>@support',
        "whatsapp_number": "123456",
        "telegram_username": "bot",
        "usd_syp_rate": "15000",
        "pricing_mode": "usd",
        "profit_margin": "1.20",
        "site_theme": "theme-aurora",
        "nav_mode": "menu",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    val = database.get_setting("support_contact")
    assert "<script>" not in val
    assert "@support" in val


def test_clean_plain_text_unit():
    """Unit test for the sanitizer helper itself."""
    from sanitize import clean_plain_text, clean_rich_text

    # Plain text: all HTML stripped
    assert clean_plain_text('<script>alert(1)</script>Hello') == "alert(1)Hello"
    assert clean_plain_text('<img src=x onerror=alert(1)>Text') == "Text"
    assert clean_plain_text("Normal text") == "Normal text"
    assert clean_plain_text("") == ""

    # Length cap
    assert len(clean_plain_text("A" * 1000, max_len=50)) <= 50

    # Rich text: safe tags kept, dangerous ones stripped
    assert "<b>" in clean_rich_text("<b>Bold</b>")
    assert "<script>" not in clean_rich_text("<script>x</script><b>ok</b>")
    assert "javascript:" not in clean_rich_text('<a href="javascript:alert(1)">x</a>')
    assert "https://ok.com" in clean_rich_text('<a href="https://ok.com">link</a>')
