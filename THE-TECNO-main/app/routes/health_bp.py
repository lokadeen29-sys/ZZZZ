"""Health-check endpoint for uptime monitoring (Uptime Robot, Heroku, etc.).

GET /health → JSON payload reporting component status:
    {
        "status": "healthy" | "degraded",
        "db": true | false,
        "redis": true | false | null,
        "timestamp": <unix epoch>
    }

HTTP 200 when all required components are up; 503 otherwise.
Redis is only checked when REDIS_URL is configured — if absent, its
field is ``null`` and does not affect the overall status.
"""
from __future__ import annotations

import logging
import os
import time

from flask import Blueprint, jsonify

bp = Blueprint("health", __name__)

log = logging.getLogger("tecnogems.health")


@bp.route("/health")
def health():
    """Lightweight probe — no auth, no CSRF, no rate-limit."""
    db_ok = _check_db()
    redis_ok = _check_redis()

    # Redis is optional in dev; only fail the check if it's configured but down.
    all_ok = db_ok and (redis_ok is not False)
    status_code = 200 if all_ok else 503

    return (
        jsonify(
            {
                "status": "healthy" if all_ok else "degraded",
                "db": db_ok,
                "redis": redis_ok,
                "timestamp": int(time.time()),
            }
        ),
        status_code,
    )


# ---------------------------------------------------------------------------
# Component probes
# ---------------------------------------------------------------------------
def _check_db() -> bool:
    """SELECT 1 against SQLite to confirm the file is readable."""
    try:
        from database import db_conn

        with db_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception as exc:
        log.warning("Health check: DB unreachable — %s", exc)
        return False


def _check_redis() -> bool | None:
    """Ping Redis if REDIS_URL is set; return None when Redis is not configured."""
    redis_url = os.getenv("REDIS_URL", "").strip() or None
    if not redis_url:
        return None  # not configured — skip
    try:
        import redis as _redis_lib

        r = _redis_lib.from_url(redis_url, socket_connect_timeout=2)
        r.ping()
        return True
    except Exception as exc:
        log.warning("Health check: Redis unreachable — %s", exc)
        return False
