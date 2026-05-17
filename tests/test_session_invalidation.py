"""
V53 security — session invalidation on password change.

Scenario: User logs in → gets session with sess_v=1 → password is reset
via token → session_version in DB becomes 2 → old session (sess_v=1) is
rejected by current_user() on the next request.
"""
from __future__ import annotations

import secrets
import time


def test_password_reset_invalidates_old_session(app, client, make_user):
    """After password reset, a session created before the reset must be rejected."""
    database = app._test_database

    # 1. Create a user and log in (simulates a normal session).
    user = make_user(email="victim@test.local", password="OldPass123!")
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["sess_v"] = int(user.get("session_version") or 1)

    # 2. Verify the session works before the password change.
    with app.test_request_context():
        with client.session_transaction() as sess:
            assert sess.get("user_id") == user["id"]

    # 3. Simulate a password reset: set a reset token, then call reset_user_password.
    token = secrets.token_urlsafe(32)
    database.set_password_reset_token(user["id"], token)
    ok, err = database.reset_user_password(token, "NewPass456!")
    assert ok is True, f"reset_user_password failed: {err}"

    # 4. Confirm session_version was incremented in DB.
    updated_user = database.get_user(user["id"])
    assert int(updated_user["session_version"]) == 2

    # 5. The old session (sess_v=1) must now be invalidated.
    #    Hit any authenticated endpoint — current_user() should clear session.
    resp = client.get("/orders", follow_redirects=False)
    # Should redirect to login (302) because session is invalidated.
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")

    # 6. Session should be cleared.
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_new_login_after_password_reset_works(app, client, make_user):
    """After password reset, logging in again with the new password must work
    and set the correct sess_v matching the new session_version."""
    database = app._test_database

    user = make_user(email="user2@test.local", password="OldPass123!")
    token = secrets.token_urlsafe(32)
    database.set_password_reset_token(user["id"], token)
    database.reset_user_password(token, "NewPass456!")

    # Login with the new password via the /login route.
    resp = client.post("/login", data={
        "email": "user2@test.local",
        "password": "NewPass456!",
    }, follow_redirects=False)
    # Successful login redirects (302).
    assert resp.status_code in (302, 303)

    # Session should contain the updated sess_v.
    with client.session_transaction() as sess:
        assert sess.get("user_id") is not None
        assert int(sess.get("sess_v", 0)) == 2
