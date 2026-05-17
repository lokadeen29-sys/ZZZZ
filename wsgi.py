"""WSGI entry point for production servers (gunicorn / uwsgi).

V48: initialise the database, indexes, admin user, catalog and posters
eagerly at import time so the very first HTTP request does not pay the
setup cost and any startup error fails fast (instead of being silently
swallowed inside the @before_request lazy-init).
"""
import os
from app import app, init_db, ensure_indexes, seed_admin, seed_local_provider_catalog, attach_generated_posters

# Eager initialisation
init_db()
try:
    ensure_indexes()
except Exception as _exc:
    app.logger.warning("ensure_indexes failed: %s", _exc)

_admin_pw = os.getenv("ADMIN_PASSWORD", "").strip()
_weak = {"", "admin", "admin123456", "password", "123456", "change-this", "<CHANGE-THIS-STRONG-PASSWORD>"}
if os.getenv("FLASK_ENV") == "production" and (_admin_pw in _weak or len(_admin_pw) < 10):
    raise RuntimeError(
        "ADMIN_PASSWORD is too weak or default. Set a strong ADMIN_PASSWORD before booting in production."
    )

seed_admin(os.getenv("ADMIN_EMAIL", "admin@example.com"), _admin_pw or "<CHANGE-THIS-STRONG-PASSWORD>")
seed_local_provider_catalog()
try:
    attach_generated_posters()
except Exception:
    pass

# Mark setup as done so the lazy @before_request guard becomes a no-op.
app._setup_done = True

application = app
