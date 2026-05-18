"""V51 task C — authentication flow tests.

Covers:
  - /register happy path + weak-password reject + over-length reject
  - /login with good credentials (password is hashed and cookie is set)
  - /login with bad credentials (no session set)
  - /logout clears the session
  - validate_password_strength module-level logic
"""
from __future__ import annotations


REGISTER_FORM = {
    "name": "Test User",
    "email": "new@test.local",
    "phone": "",
    "password": "StrongPass1!",
    "password_confirm": "StrongPass1!",
}


class TestPasswordStrength:
    def test_requires_min_length(self, app):
        from app import validate_password_strength
        ok, err = validate_password_strength("abc1")
        assert not ok and err

    def test_requires_two_classes(self, app):
        from app import validate_password_strength
        # 8 lowercase chars — only 1 class.
        ok, err = validate_password_strength("abcdefgh")
        assert not ok and err

    def test_accepts_strong(self, app):
        from app import validate_password_strength
        ok, err = validate_password_strength("Password1")
        assert ok and err is None


class TestRegister:
    def test_register_creates_user(self, app, client):
        resp = client.post("/register", data=REGISTER_FORM, follow_redirects=False)
        assert resp.status_code in (302, 303)
        user = app._test_database.get_user_by_email("new@test.local")
        assert user is not None
        assert user["email"] == "new@test.local"
        # Password hashed — NOT stored in plaintext.
        assert user["password_hash"] != "StrongPass1!"
        assert "pbkdf2" in user["password_hash"] or "scrypt" in user["password_hash"]

    def test_register_rejects_weak_password(self, app, client):
        form = dict(REGISTER_FORM, email="weak@test.local",
                    password="aaaaaaaa", password_confirm="aaaaaaaa")
        client.post("/register", data=form, follow_redirects=False)
        assert app._test_database.get_user_by_email("weak@test.local") is None

    def test_register_rejects_mismatched_confirm(self, app, client):
        form = dict(REGISTER_FORM, email="mismatch@test.local",
                    password="StrongPass1!", password_confirm="DifferentPass1!")
        client.post("/register", data=form, follow_redirects=False)
        assert app._test_database.get_user_by_email("mismatch@test.local") is None

    def test_register_rejects_oversized_email(self, app, client):
        form = dict(REGISTER_FORM, email=("a" * 200) + "@test.local")
        resp = client.post("/register", data=form, follow_redirects=False)
        # Over-length must NOT create a user.
        assert resp.status_code == 200  # re-renders the form
        all_users = app._test_database.list_users()
        assert not any(u["email"].startswith("aaaa") for u in all_users)


class TestLogin:
    def test_login_success_sets_session(self, client, make_user):
        make_user(email="login@test.local", password="GoodPass1!")
        resp = client.post(
            "/login",
            data={"email": "login@test.local", "password": "GoodPass1!"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        with client.session_transaction() as sess:
            assert sess.get("user_id")

    def test_login_bad_password_rejected(self, client, make_user):
        make_user(email="login2@test.local", password="GoodPass1!")
        resp = client.post(
            "/login",
            data={"email": "login2@test.local", "password": "WRONG"},
            follow_redirects=False,
        )
        # Re-renders the login form (200) without a session.
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("user_id") is None

    def test_login_oversized_password_rejected(self, client, make_user):
        make_user(email="login3@test.local", password="GoodPass1!")
        resp = client.post(
            "/login",
            data={"email": "login3@test.local", "password": "x" * 500},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("user_id") is None


class TestLogout:
    def test_logout_clears_session(self, client, make_user, login_as):
        user = make_user(email="out@test.local", password="GoodPass1!")
        login_as(user["id"])
        with client.session_transaction() as sess:
            assert sess.get("user_id") == user["id"]

        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code in (302, 303)
        with client.session_transaction() as sess:
            assert sess.get("user_id") is None


class TestLoginRequiredGuard:
    def test_dashboard_redirects_when_not_logged_in(self, client):
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["Location"]

    def test_dashboard_ok_when_logged_in(self, client, make_user, login_as):
        user = make_user(email="dash@test.local")
        login_as(user["id"])
        resp = client.get("/dashboard")
        assert resp.status_code == 200
