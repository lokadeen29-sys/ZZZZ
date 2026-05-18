"""V53 REFACTOR (phase 5): order-queue dispatch helper.

Owns :func:`enqueue_order_job`, which used to live near the top of
``app.py``. Wraps RQ when ``REDIS_URL`` is configured and falls back to
a synchronous run for dev / tests.

This module is import-cheap: the Redis / RQ connection is lazy. Importing
``app.services.orders`` does **not** open a Redis socket.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("tecnogems.orders")


# ---------------------------------------------------------------------------
# Lazy Redis / RQ wiring
# ---------------------------------------------------------------------------
_redis_url = os.getenv("REDIS_URL", "").strip() or None
_redis_conn = None
_order_queue = None

if _redis_url:
    try:
        from redis import Redis
        from rq import Queue as _RQQueue

        _redis_conn = Redis.from_url(_redis_url)
        _order_queue = _RQQueue("tecnogems_orders", connection=_redis_conn)
        log.info("Using Redis Queue for order processing (worker_rq.py).")
    except Exception as exc:
        log.warning(
            "Failed to initialise Redis Queue (%s); falling back to "
            "synchronous order processing.",
            exc,
        )
        _redis_conn = None
        _order_queue = None
else:
    log.warning(
        "REDIS_URL not set — running orders synchronously (development mode). "
        "Production MUST set REDIS_URL."
    )


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------
def enqueue_order_job(
    order_id: int, product: Any = None, player_id: Any = None
) -> None:
    """Enqueue an order for async processing via RQ.

    Production: uses Redis Queue (validated at boot in ``wsgi.py``).
    Dev/tests without ``REDIS_URL``: runs ``tasks.process_order``
    synchronously so the user-flow still works end-to-end.

    The ``product`` and ``player_id`` parameters are ignored (kept for API
    compatibility with the legacy in-process queue). ``tasks.process_order``
    re-fetches the order from DB by id.
    """
    from tasks import process_order  # lazy — avoid pulling RQ at module load

    if _order_queue is None:
        # Synchronous fallback for dev / tests. Production refuses to
        # boot without Redis (see wsgi.py / app.create_app boot check),
        # so this branch is never taken in production.
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(
                "order_queue is None in production — Redis boot check "
                "failed silently"
            )
        try:
            process_order(order_id)
        except Exception as exc:
            log.exception(
                "Synchronous process_order failed for order=%s: %s",
                order_id,
                exc,
            )
        return
    _order_queue.enqueue(process_order, order_id)


# ---------------------------------------------------------------------------
# Test / introspection helpers
# ---------------------------------------------------------------------------
def has_redis_queue() -> bool:
    """Return True iff the durable Redis-backed queue is wired up."""
    return _order_queue is not None
