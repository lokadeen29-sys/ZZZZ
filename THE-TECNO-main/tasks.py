"""V48: Background task functions usable by both threading fallback and RQ.

If REDIS_URL is configured we enqueue to RQ (durable, multi-worker).
Otherwise we fall back to the existing in-process Queue + Thread pool
(works in single-process gunicorn but loses jobs on restart).

This module is intentionally framework-light: import-safe even when redis
is unavailable, and contains NO Flask context dependencies so RQ workers
can import it and run process_order() / send_email_task() in isolation.
"""

from __future__ import annotations

import os
import re
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid

log = logging.getLogger("tasks")


# V50.2 MEDIUM (M12): sanitise supplier response messages before they land in
# the DB. Suppliers occasionally echo our own API key back in errors, return
# internal stack traces, or include raw HTML that would break admin views.
_NOTE_MAX_LEN = 200
_SUPPLIER_KEY_RE = re.compile(r"(key|token|apikey|api_key|authorization)\s*[:=]\s*[A-Za-z0-9._\-]+", re.IGNORECASE)
_SUPPLIER_HTML_RE = re.compile(r"<[^>]{1,200}>")
_SUPPLIER_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitise_supplier_note(text) -> str:
    """Strip credentials/HTML/control chars from supplier error text and
    truncate to a safe length so admin screens (and users, via the orders
    API) do not see raw response blobs."""
    if text is None:
        return ""
    s = str(text)
    s = _SUPPLIER_KEY_RE.sub(r"\1=[REDACTED]", s)
    s = _SUPPLIER_HTML_RE.sub(" ", s)
    s = _SUPPLIER_CTRL_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > _NOTE_MAX_LEN:
        s = s[: _NOTE_MAX_LEN - 1] + "…"
    return s

# ---- Redis / RQ optional bootstrap --------------------------------------
_redis_url = os.getenv("REDIS_URL", "").strip()
USE_RQ = False
_queue = None

if _redis_url:
    try:
        from redis import Redis  # type: ignore
        from rq import Queue  # type: ignore

        _conn = Redis.from_url(_redis_url)
        _queue = Queue("tecnogems_orders", connection=_conn)
        USE_RQ = True
        log.info("RQ enabled (REDIS_URL set, queue=tecnogems_orders)")
    except Exception as exc:  # pragma: no cover
        log.warning("RQ disabled — failed to init redis/rq: %s", exc)
        USE_RQ = False
        _queue = None


# ---- Email task ---------------------------------------------------------
def send_email_task(
    to_email: str,
    subject: str,
    body: str,
    smtp_server: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_use_tls: bool,
    mail_from: str,
    html_body: str = None,
    mail_from_name: str = "TecnoGems",
    reply_to: str = "",
):
    """Pure function — safe to enqueue. No app context needed."""
    # Build multipart message (plain + HTML) for better deliverability
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    # V67 DELIVERABILITY: see app.py _send_email_sync for rationale.
    msg["From"] = formataddr((mail_from_name or "TecnoGems", mail_from))
    if smtp_user and smtp_user.lower() != (mail_from or "").lower():
        msg["Sender"] = smtp_user
    msg["To"] = to_email
    msg["Reply-To"] = reply_to or mail_from
    msg["Date"] = formatdate(localtime=True)
    _msgid_domain = mail_from.split("@")[-1] if "@" in (mail_from or "") else "tecnogems.com"
    msg["Message-ID"] = make_msgid(domain=_msgid_domain)
    # V67 DELIVERABILITY headers — DO NOT set Precedence:bulk or
    # List-Unsubscribe (mailto-only) on transactional mail; both push
    # account-verification messages into Spam at Gmail.
    msg["X-Mailer"] = "TecnoGems Transactional Mailer"
    msg["X-Auto-Response-Suppress"] = "All"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Entity-Ref-ID"] = make_msgid(domain=_msgid_domain).strip("<>")
    msg["MIME-Version"] = "1.0"

    part_text = MIMEText(body, "plain", "utf-8")
    msg.attach(part_text)
    if html_body:
        part_html = MIMEText(html_body, "html", "utf-8")
        msg.attach(part_html)

    # V67 DELIVERABILITY: envelope sender MUST equal the authenticated
    # mailbox for SPF alignment with Gmail / Workspace.
    envelope_from = smtp_user if smtp_user and "@" in smtp_user else mail_from

    with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
        server.ehlo()
        if smtp_use_tls:
            server.starttls()
            server.ehlo()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(msg, from_addr=envelope_from, to_addrs=[to_email])


# ---- Order processing task (RQ-callable) -------------------------------
def process_order(order_id: int):
    """Process a single order: read from DB, send to supplier, update status.

    This is the RQ-callable entry point. It re-fetches the order and product
    from the database (cannot trust serialized objects across workers) so the
    correct provider_product_id (from the products table) is used when
    talking to the supplier.

    Notes:
    - Uses get_product_by_id (NOT order["product_id"]) to obtain the
      supplier's external product id (provider_product_id).
    - Mirrors the auto-refund / manual_pending logic of the legacy
      in-process worker.
    """
    from database import get_order, get_product_by_id, update_order, get_setting
    from providers import create_provider_order, normalize_supplier_create_status

    try:
        order = get_order(order_id)
        if not order:
            log.error("process_order: order %s not found", order_id)
            return

        product = get_product_by_id(order["product_id"])
        if not product:
            log.error("process_order: product %s not found for order %s", order["product_id"], order_id)
            update_order(order_id, "manual_pending", None, "Product not found in DB")
            return

        # Manual mode short-circuit
        if get_setting("manual_orders", "0") == "1":
            update_order(order_id, "manual_pending", None, "Manual mode is enabled")
            return

        update_order(order_id, "processing", None, "Sending order to supplier")

        # V67.1: log every supplier call so we can see WHY orders fall to
        # manual_pending. Previous version swallowed the response into an
        # opaque note that admins could not interpret.
        log.info(
            "process_order %s: provider=%s product_id=%s player_id=%s",
            order_id, product.get("provider"),
            product.get("provider_product_id"), order.get("player_id"),
        )
        res = create_provider_order(
            product["provider"],
            product["provider_product_id"],   # ← critical: external supplier id
            order["player_id"],
        )
        log.info("process_order %s: supplier response keys=%s",
                 order_id, list(res.keys()) if isinstance(res, dict) else type(res).__name__)

        auto_refund = get_setting("auto_refund_on_failure", "0") == "1"

        if not isinstance(res, dict):
            note = f"Invalid response from supplier ({type(res).__name__})"
            status = "rejected" if auto_refund else "manual_pending"
            update_order(order_id, status, None, f"{note}{' (auto-refund)' if auto_refund else ''}")
            log.error("Order %s: invalid supplier response: %r", order_id, res)
            return

        # V67 BUGFIX: previously we treated *any* response without an
        # explicit "error" key as `completed`. That was wrong — most
        # suppliers (G2Bulk especially) accept the order and return only
        # an order id, with the actual fulfilment still pending. We were
        # lying to the user that the order was completed.
        # Use the canonical normaliser instead so:
        #   - status="completed"     → completed
        #   - status="pending"/empty → supplier_pending (poller will follow up)
        #   - error/success=false    → manual_pending or rejected (auto-refund)
        norm = normalize_supplier_create_status(product["provider"], res)
        provider_order_id = norm.get("provider_order_id") or ""

        if not norm.get("ok"):
            reason = _sanitise_supplier_note(norm.get("error") or "Supplier error")
            status = "rejected" if auto_refund else "manual_pending"
            note = f"Supplier error{' (auto-refund)' if auto_refund else ''}: {reason}"
            update_order(order_id, status, str(provider_order_id) or None, note)
            log.warning("Order %s rejected by supplier: %s", order_id, reason)
            return

        target_status = norm.get("status") or "supplier_pending"
        # Only true completion ends the cycle; anything else stays pending
        # for the periodic poller to pick up.
        if target_status == "completed":
            update_order(order_id, "completed", str(provider_order_id), "Accepted by supplier")
            log.info("Order %s completed (provider id=%s)", order_id, provider_order_id)
        else:
            update_order(order_id, "supplier_pending", str(provider_order_id),
                         "Awaiting supplier fulfilment")
            log.info("Order %s queued at supplier (provider id=%s)", order_id, provider_order_id)

    except Exception as exc:
        log.exception("process_order error on order %s: %s", order_id, exc)
        try:
            from database import update_order as _update
            # V50.2 MEDIUM (M12): sanitise exception text — tracebacks can
            # leak filesystem paths or library internals to the admin UI.
            _update(order_id, "manual_pending", None,
                    f"Worker error: {_sanitise_supplier_note(exc)}")
        except Exception:
            pass


# ---- Public enqueue helpers --------------------------------------------
def enqueue_email(*args, **kwargs):
    if USE_RQ and _queue is not None:
        return _queue.enqueue(send_email_task, *args, **kwargs)
    return None  # caller falls back to thread queue


# V67: Periodic supplier-status poller.
# `process_order` parks any non-completed order in `supplier_pending` (or
# `processing`) with the provider's order id. This sweep walks those rows,
# asks the supplier for the latest status, and promotes them to
# `completed` / `rejected` (with auto-refund when enabled). Without this,
# orders that the supplier accepts but does not fulfil instantly stay
# "جاري التنفيذ" forever — the exact bug reported by the user.
def refresh_pending_orders(limit: int = 50) -> dict:
    """Refresh status for pending orders that have a provider_order_id.

    Returns a dict with counts of orders inspected / changed / errored,
    safe to log or expose to admins.
    """
    from database import (
        list_orders_for_auto_refresh,
        get_product_by_id,
        update_order,
        get_setting,
    )
    from providers import get_provider_order_status, normalize_supplier_status

    counters = {"checked": 0, "completed": 0, "rejected": 0, "errors": 0, "still_pending": 0}
    auto_refund = get_setting("auto_refund_on_failure", "0") == "1"

    try:
        rows = list_orders_for_auto_refresh()
    except Exception as exc:
        log.exception("refresh_pending_orders: failed to list orders: %s", exc)
        return counters

    for o in (rows or [])[: max(1, int(limit))]:
        counters["checked"] += 1
        order_id = o.get("id")
        provider_order_id = o.get("provider_order_id")
        # `provider` is on orders directly; fall back to product lookup if missing.
        provider = o.get("provider")
        if not provider:
            try:
                product = get_product_by_id(o.get("product_id"))
                if product:
                    provider = product.get("provider")
            except Exception:
                provider = None
        if not provider or not provider_order_id:
            continue
        try:
            res = get_provider_order_status(provider, provider_order_id)
            norm = normalize_supplier_status(provider, res)
            new_status = (norm.get("status") or "").strip()
            note = _sanitise_supplier_note(norm.get("note") or "")
            if new_status == "completed":
                update_order(order_id, "completed", str(provider_order_id),
                             note or "Supplier completed")
                counters["completed"] += 1
            elif new_status == "manual_pending":
                # Supplier reported failed/cancelled/refunded/partial.
                if auto_refund:
                    update_order(order_id, "rejected", str(provider_order_id),
                                 f"{note} (auto-refund)" if note else "Auto-refund")
                    counters["rejected"] += 1
                else:
                    update_order(order_id, "manual_pending", str(provider_order_id), note)
                    counters["still_pending"] += 1
            else:
                # Still supplier_pending — leave as-is, just refresh the note.
                if note and note != (o.get("note") or ""):
                    update_order(order_id, o.get("status") or "supplier_pending",
                                 str(provider_order_id), note)
                counters["still_pending"] += 1
        except Exception as exc:
            counters["errors"] += 1
            log.warning("refresh_pending_orders: order %s failed: %s", order_id, exc)
            continue

    log.info("refresh_pending_orders done: %s", counters)
    return counters


# Note: app.py uses enqueue_order_job() which dispatches between local
# Queue.put and rq.enqueue(process_order). We deliberately do NOT export
# a separate tasks.enqueue_order here to avoid two parallel queue paths.
