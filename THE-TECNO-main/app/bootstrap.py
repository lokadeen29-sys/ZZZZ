"""V53 REFACTOR (phase 5): first-request initialisation hook.

The legacy ``app.py`` had a giant ``@app.before_request`` named
``setup_once`` that lazily ran ``init_db()``, ``ensure_indexes()``,
``seed_admin()``, ``seed_local_provider_catalog()`` and
``attach_generated_posters()`` on the very first HTTP request.

That logic is preserved here verbatim. ``wsgi.py`` calls the same
helpers eagerly at boot to short-circuit the lazy path; setting
``app._setup_done = True`` after the eager run makes ``setup_once``
a no-op for the rest of the process.

This module intentionally does NOT touch the request/response cycle —
it only registers the hook. Behaviour is bit-identical to the legacy
implementation.
"""
from __future__ import annotations

import logging
import os
import threading

from flask import Flask

log = logging.getLogger("tecnogems.bootstrap")

# Module-level lock so two concurrent first requests do not both run the
# setup body. Re-entrant inside a single request thanks to the `_setup_done`
# guard on the app instance.
_setup_lock = threading.Lock()


def register_setup_once(app: Flask) -> None:
    """Attach the lazy first-request initialiser to *app*.

    The hook order here matters: this must run *after* the language-cookie
    reset (so the very first request still sees a sensible locale) but
    *before* the ``_api_origin_guard`` (so DB tables exist by the time an
    API request lands). :func:`app.routes.register_blueprints` enforces
    that ordering by calling this function and the API origin-guard
    registration in the right sequence.
    """

    @app.before_request
    def setup_once():
        if getattr(app, "_setup_done", False):
            return None
        with _setup_lock:
            if getattr(app, "_setup_done", False):
                return None
            # V47: warn / block weak admin password in production.
            _admin_pw = os.getenv("ADMIN_PASSWORD", "admin123456")
            _weak_passwords = {
                "admin123456",
                "admin",
                "password",
                "123456",
                "change-this",
                "<CHANGE-THIS-STRONG-PASSWORD>",
                "",
            }
            if _admin_pw in _weak_passwords or len(_admin_pw) < 10:
                if os.getenv("FLASK_ENV") == "production":
                    raise RuntimeError(
                        "ADMIN_PASSWORD is too weak or still the default "
                        "value. Set a strong ADMIN_PASSWORD in .env before "
                        "running in production."
                    )
                log.warning(
                    "ADMIN_PASSWORD is weak or default. "
                    "Change it before going to production."
                )

            # Defer the heavy DB calls so circular imports (database -> app
            # extensions) cannot deadlock at module load.
            from database import (
                init_db,
                ensure_indexes,
                seed_admin,
                seed_local_provider_catalog,
                attach_generated_posters,
            )

            init_db()
            try:
                ensure_indexes()
            except Exception as exc:
                log.warning("ensure_indexes failed: %s", exc)
            seed_admin(os.getenv("ADMIN_EMAIL", "admin@example.com"), _admin_pw)
            seed_local_provider_catalog()
            try:
                attach_generated_posters()
            except Exception:
                pass
            app._setup_done = True
        return None
