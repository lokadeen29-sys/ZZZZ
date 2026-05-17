"""V53 REFACTOR (phase 1): Blueprint registration.

Phase 1 only extracts the auth Blueprint. Future phases will register
admin_bp, wallet_bp, games_bp, api_bp here — without further edits to app.py.

Import is deferred into `register_blueprints()` on purpose: auth_bp.py does
`from app import ...helpers...` at module top, so it can only be imported
*after* all helpers in app.py have been defined. app.py therefore calls
`register_blueprints(app)` at the very end of its module body.
"""
from __future__ import annotations


def register_blueprints(app, deps=None) -> None:
    """Register every phase-1 Blueprint on the given Flask app.

    `deps` is accepted (but unused today) for backward compatibility with
    the previous no-op signature.
    """
    from .auth_bp import bp as auth_bp

    app.register_blueprint(auth_bp)
