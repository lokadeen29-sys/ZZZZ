"""RQ worker entry point.

Run alongside gunicorn:
    rq worker tecnogems_orders --url $REDIS_URL
or:
    python worker_rq.py

V67: this process now ALSO runs a lightweight periodic poller in a daemon
thread that calls tasks.refresh_pending_orders() every 90 seconds. This
fixes the "orders stuck in 'جاري التنفيذ' forever" bug — previously
nothing in the system asked the supplier "is this order done yet?".
The poller only runs when REFRESH_ORDERS_INTERVAL > 0 and is best-effort
(any error is swallowed and retried on the next tick).
"""

import os
import time
import logging
import threading

from redis import Redis
from rq import Queue, Worker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker_rq")

REFRESH_INTERVAL = int(os.getenv("REFRESH_ORDERS_INTERVAL", "90") or 0)
REFRESH_LIMIT = int(os.getenv("REFRESH_ORDERS_LIMIT", "50") or 50)


def _refresh_loop():
    """Daemon loop that periodically polls suppliers for pending orders."""
    # Defer the import so the worker still boots even if the DB or providers
    # module has a transient import-time issue.
    from tasks import refresh_pending_orders

    # Stagger the first run so the worker has time to be marked alive.
    time.sleep(min(20, REFRESH_INTERVAL))
    while True:
        try:
            counters = refresh_pending_orders(limit=REFRESH_LIMIT)
            log.info("[refresh_pending_orders] %s", counters)
        except Exception as exc:
            log.warning("[refresh_pending_orders] error: %s", exc)
        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise SystemExit("REDIS_URL is not set — cannot start RQ worker")
    conn = Redis.from_url(redis_url)
    queues = [Queue("tecnogems_orders", connection=conn)]

    if REFRESH_INTERVAL > 0:
        t = threading.Thread(target=_refresh_loop, daemon=True,
                             name="refresh-pending-orders")
        t.start()
        log.info("Started refresh-pending-orders thread (every %ss)", REFRESH_INTERVAL)
    else:
        log.info("REFRESH_ORDERS_INTERVAL is 0 — periodic poller disabled")

    Worker(queues, connection=conn).work()
