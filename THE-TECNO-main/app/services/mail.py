"""V53 REFACTOR (phase 1): SMTP sender + transactional email helpers.

V69 (this revision): registration + password-reset emails are now sent
**asynchronously** via RQ with retry, instead of blocking the HTTP
request on a 5-30s SMTP round-trip (the V62.1 sync regression).

Behaviour summary:

- Multipart (plain + HTML) MIME with V67 deliverability headers.
- Aligned envelope sender (matches SMTP-AUTH user) for SPF alignment.
- ``send_verification_email`` and ``send_password_reset_email`` now
  enqueue to RQ with a 3-attempt retry policy (30s / 2min / 10min). If
  REDIS_URL is missing or the enqueue itself fails, we fall back to a
  synchronous send so the caller still sees real SMTP errors and the
  email is never silently dropped to an in-process queue that disappears
  on restart. Background failures are surfaced via RQ's
  FailedJobRegistry + Sentry.
- ``send_email_change_confirmation`` stays **synchronous** on purpose:
  the user is sitting on the profile page waiting for confirmation, so
  the few-hundred-ms SMTP latency is acceptable in exchange for an
  immediate, accurate error message if delivery fails.

Email HTML bodies are rendered from Jinja templates in
``templates/email/*.html``. The legacy ``_build_email_html`` helper is
kept for backwards compatibility.

Public symbols (consumed by app.py and routes):

- ``email_verification_is_enabled()``
- ``email_is_configured()``
- ``send_email(to_email, subject, body, html_body=None)`` (async-ish)
- ``send_verification_email(to_email, token)``       (async via RQ + retry)
- ``send_password_reset_email(to_email, token)``     (async via RQ + retry)
- ``send_email_change_confirmation(to_email, token)``(synchronous, intentional)
- ``email_queue`` (in-process Queue used by the worker threads,
  retained for the generic ``send_email`` fallback path)

Plus the lower-level building blocks (also re-exported via app.py):
- ``_send_email_sync``, ``_email_worker``, ``_aligned_envelope_sender``
- ``_build_email_html`` (transitional shim around Jinja base template)

Module-level configuration is read once from env at import time, mirroring
the legacy behaviour. Override via env vars before importing.
"""
from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from queue import Queue

from flask import render_template

from app.utils.settings_cache import get_setting

log = logging.getLogger("tecnogems.mail")

# ---------------------------------------------------------------------------
# Configuration (read once from env)
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("BASE_URL", "https://tecnogems.com").rstrip("/")

MAIL_SERVER = os.getenv("MAIL_SERVER", "")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "1") == "1"
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").replace(" ", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", MAIL_USERNAME or "no-reply@tecnogems.com")
# V67 DELIVERABILITY: friendly From-name + Reply-To.
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "TecnoGems").strip() or "TecnoGems"
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", "").strip()

# Domain used in Message-ID and List-Unsubscribe URLs.
try:
    _BASE_DOMAIN = BASE_URL.split("//", 1)[-1].split("/", 1)[0]
except Exception:
    _BASE_DOMAIN = "tecnogems.com"


def _aligned_envelope_sender():
    """Return the SMTP envelope sender that aligns with the SMTP login.

    Gmail / Google Workspace REWRITE the From: header to the authenticated
    mailbox if it doesn't match. To preserve a clean From: while still
    passing SPF/DKIM alignment, we set the SMTP-level MAIL FROM (envelope)
    to the authenticated user. Most consumer mailbox providers honour this.
    """
    if MAIL_USERNAME and "@" in MAIL_USERNAME:
        return MAIL_USERNAME
    return MAIL_FROM


def email_verification_is_enabled():
    return get_setting("email_verification_enabled", "0") == "1"


def email_is_configured():
    return bool(MAIL_SERVER and MAIL_USERNAME and MAIL_PASSWORD and MAIL_FROM)


# ---------------------------------------------------------------------------
# Async queue + worker threads
# ---------------------------------------------------------------------------
# V42 batch2: async email queue.
email_queue: Queue = Queue()


def _send_email_sync(to_email, subject, body, html_body=None):
    if not email_is_configured():
        log.warning("Email skipped (SMTP not configured): to=%s subject=%s", to_email, subject)
        return
    # Build multipart message (plain + HTML) to improve deliverability
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    # V67 DELIVERABILITY: From, Sender, Reply-To
    msg["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM))
    if MAIL_USERNAME and MAIL_USERNAME.lower() != MAIL_FROM.lower():
        msg["Sender"] = MAIL_USERNAME
    msg["To"] = to_email
    msg["Reply-To"] = MAIL_REPLY_TO or MAIL_FROM
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(
        domain=MAIL_FROM.split("@")[-1] if "@" in MAIL_FROM else _BASE_DOMAIN
    )
    # V67 DELIVERABILITY headers for transactional mail.
    # IMPORTANT: do NOT add "Precedence: bulk" or a mailto-only
    # List-Unsubscribe — those are signals for newsletters and push
    # account-verification mail straight into Spam at Gmail.
    msg["X-Mailer"] = "TecnoGems Transactional Mailer"
    msg["X-Auto-Response-Suppress"] = "All"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Entity-Ref-ID"] = make_msgid(domain=_BASE_DOMAIN).strip("<>")
    msg["MIME-Version"] = "1.0"

    # Attach plain text first (fallback), then HTML if provided.
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=30) as server:
            server.ehlo()
            if MAIL_USE_TLS:
                server.starttls()
                server.ehlo()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            # V67 DELIVERABILITY: pass an explicit envelope sender that is
            # always the authenticated mailbox.
            server.send_message(
                msg,
                from_addr=_aligned_envelope_sender(),
                to_addrs=[to_email],
            )
        log.info("Email sent successfully to=%s subject=%s", to_email, subject)
    except smtplib.SMTPAuthenticationError as exc:
        log.error(
            "Email AUTH FAILED to=%s subject=%s: %s. "
            "تأكد من استخدام Gmail App Password (16 حرف بدون مسافات) في "
            "MAIL_PASSWORD وليس كلمة مرور الحساب العادية.",
            to_email, subject, exc,
        )
        raise
    except smtplib.SMTPException as exc:
        log.error("Email SMTP error to=%s subject=%s: %s", to_email, subject, exc)
        raise
    except Exception as exc:
        log.error("Email send failed to=%s subject=%s: %s", to_email, subject, exc)
        raise


def _email_worker():
    while True:
        item = email_queue.get()
        try:
            if item is None:
                continue
            _send_email_sync(*item)
        except Exception as exc:
            log.error("email_worker error: %s", exc)
        finally:
            email_queue.task_done()


# Spin up 2 worker threads for parallel SMTP sends.
# (Same count as the legacy app.py implementation.)
def _start_email_workers(count: int = 2) -> None:
    for i in range(count):
        threading.Thread(
            target=_email_worker,
            daemon=True,
            name=f"email-worker-{i}",
        ).start()


_start_email_workers()


def send_email(to_email, subject, body, html_body=None):
    """Non-blocking: enqueue email and return immediately.

    V45: prefer durable RQ queue when REDIS_URL is configured; fall back
    to the in-process thread queue otherwise (backwards compatible).
    """
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")
    try:
        from tasks import USE_RQ, enqueue_email
        if USE_RQ:
            enqueue_email(
                to_email, subject, body,
                MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD,
                MAIL_USE_TLS, MAIL_FROM,
                html_body=html_body,
                mail_from_name=MAIL_FROM_NAME,
                reply_to=MAIL_REPLY_TO or MAIL_FROM,
            )
            return
    except Exception as exc:
        log.warning("RQ enqueue failed, falling back to thread queue: %s", exc)
    email_queue.put((to_email, subject, body, html_body))


# ---------------------------------------------------------------------------
# Async transactional dispatch (RQ with retry, sync fallback)
# ---------------------------------------------------------------------------
# V69: registration / password-reset must not block the HTTP request on
# SMTP. We enqueue to RQ with a retry policy and let the worker handle
# delivery. The fallback path is *synchronous* on purpose — see the
# module docstring for why we don't fall back to the in-process queue.
def _enqueue_transactional(to_email, subject, body, html_body):
    """Enqueue a transactional email to RQ with retry; sync fallback.

    Returns True if successfully enqueued for asynchronous delivery,
    False if we had to fall back to (and complete) a synchronous send.

    Any exception raised here is a real failure the caller should surface
    to the user — it means both the async path AND the sync fallback
    failed.
    """
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")

    try:
        from tasks import USE_RQ, _queue, send_email_task
        if USE_RQ and _queue is not None:
            from rq import Retry
            _queue.enqueue(
                send_email_task,
                args=(
                    to_email, subject, body,
                    MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD,
                    MAIL_USE_TLS, MAIL_FROM,
                ),
                kwargs={
                    "html_body": html_body,
                    "mail_from_name": MAIL_FROM_NAME,
                    "reply_to": MAIL_REPLY_TO or MAIL_FROM,
                },
                # 3 retries spaced 30s / 2min / 10min — enough to ride
                # through transient Gmail blips without spamming.
                retry=Retry(max=3, interval=[30, 120, 600]),
                job_timeout=60,
                # Keep failures around for a week so admins (and Sentry
                # alerts pointing here) can inspect the traceback.
                failure_ttl=86400 * 7,
                result_ttl=3600,
            )
            log.info("Email enqueued to RQ to=%s subject=%s", to_email, subject)
            return True
    except Exception as exc:
        # Don't raise here — fall through to the sync send so the email
        # still has a chance, AND so the caller still sees real SMTP
        # errors if SMTP itself is the problem.
        log.warning(
            "RQ enqueue failed for transactional email (to=%s, subject=%s); "
            "falling back to synchronous send: %s",
            to_email, subject, exc,
        )

    # Sync fallback — no Redis, or RQ enqueue raised.
    _send_email_sync(to_email, subject, body, html_body=html_body)
    return False


# ---------------------------------------------------------------------------
# HTML rendering (Jinja templates)
# ---------------------------------------------------------------------------
def _build_email_html(title, greeting, message, button_text, button_url, footer_note):
    """Render the transactional email layout via Jinja.

    Kept under the legacy name so any caller (or external tool) that still
    imports ``_build_email_html`` keeps working. New code should call the
    template-specific senders (``send_verification_email``, ...) directly.
    """
    return render_template(
        "email/base.html",
        title=title,
        greeting=greeting,
        message=message,
        button_text=button_text,
        button_url=button_url,
        footer_note=footer_note,
        base_url=BASE_URL,
    )


# ---------------------------------------------------------------------------
# Specific senders
# ---------------------------------------------------------------------------
def send_verification_email(to_email, token):
    link = f"{BASE_URL}/verify-email/{token}"
    # V67 DELIVERABILITY: rich plain-text body close to the HTML content.
    body = f"""مرحبًا بك في TecnoGems

شكرًا لإنشاء حسابك. لتفعيل بريدك الإلكتروني والبدء في استخدام المنصة،
افتح الرابط التالي خلال 24 ساعة:

{link}

إذا لم تطلب إنشاء حساب على TecnoGems يمكنك تجاهل هذه الرسالة بأمان،
ولن يتم إنشاء أي حساب باستخدام بريدك.

— فريق TecnoGems
{BASE_URL}

ملاحظة: هذه رسالة تلقائية لتأكيد البريد الإلكتروني، يرجى عدم الرد عليها.
للدعم تواصل معنا عبر صفحة الدعم على الموقع.
"""
    html_body = render_template(
        "email/verify.html",
        link=link,
        base_url=BASE_URL,
    )
    # V69: async via RQ with retry. Don't block the HTTP response on a
    # 5-30s SMTP round-trip; if the SMTP server is slow or transiently
    # down, RQ will retry in the background. Falls back to synchronous
    # send only when RQ itself is unavailable.
    _enqueue_transactional(
        to_email, "TecnoGems - تفعيل حسابك", body, html_body
    )


def send_password_reset_email(to_email, token):
    link = f"{BASE_URL}/reset-password/{token}"
    body = f"""مرحبًا

تلقينا طلبًا لإعادة تعيين كلمة المرور الخاصة بحسابك على TecnoGems.

لإنشاء كلمة مرور جديدة، افتح الرابط التالي:
{link}

صلاحية الرابط ساعة واحدة فقط من تاريخ إرسال هذه الرسالة.
إذا لم تطلب استعادة كلمة المرور يمكنك تجاهل هذه الرسالة، حسابك آمن
ولن يتم إجراء أي تغيير.

— فريق TecnoGems
{BASE_URL}

ملاحظة: هذه رسالة تلقائية، يرجى عدم الرد عليها.
للدعم تواصل معنا عبر صفحة الدعم على الموقع.
"""
    html_body = render_template(
        "email/reset_password.html",
        link=link,
        base_url=BASE_URL,
    )
    # V69: async via RQ with retry (same rationale as verification).
    _enqueue_transactional(
        to_email, "TecnoGems - استعادة كلمة المرور", body, html_body
    )


def send_email_change_confirmation(to_email, token):
    link = f"{BASE_URL}/confirm-email-change/{token}"
    body = f"""مرحبًا

تلقينا طلبًا لتغيير البريد الإلكتروني المرتبط بحسابك على TecnoGems.

لتأكيد التغيير، افتح الرابط التالي:
{link}

إذا لم تطلب تغيير البريد يمكنك تجاهل هذه الرسالة، ولن يتم إجراء أي
تعديل على حسابك.

— فريق TecnoGems
{BASE_URL}

ملاحظة: هذه رسالة تلقائية، يرجى عدم الرد عليها.
"""
    html_body = render_template(
        "email/change_email.html",
        link=link,
        base_url=BASE_URL,
    )
    # V69: intentionally synchronous. Unlike registration / forgot-password
    # (which fire a long-running form submit and benefit from a snappy
    # response), the email-change flow happens inside the profile page
    # while the user is actively watching for confirmation; the few
    # hundred ms SMTP latency is worth getting an immediate, accurate
    # error message if delivery fails.
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")
    _send_email_sync(to_email, "TecnoGems - تأكيد تغيير البريد", body, html_body=html_body)


__all__ = [
    "BASE_URL",
    "MAIL_SERVER",
    "MAIL_PORT",
    "MAIL_USE_TLS",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_FROM",
    "MAIL_FROM_NAME",
    "MAIL_REPLY_TO",
    "_aligned_envelope_sender",
    "email_verification_is_enabled",
    "email_is_configured",
    "email_queue",
    "_send_email_sync",
    "_email_worker",
    "send_email",
    "_enqueue_transactional",
    "_build_email_html",
    "send_verification_email",
    "send_password_reset_email",
    "send_email_change_confirmation",
]
