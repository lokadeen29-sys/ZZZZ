"""V53.1 — قراءة IP الحقيقي للعميل خلف Cloudflare / Heroku / أي reverse-proxy.

المشكلة
--------
``Flask-Limiter`` و ``log_audit(ip=...)`` كانا يقرأان ``request.remote_addr``
مباشرةً. خلف Cloudflare/Heroku هذه القيمة هي IP الـproxy، ليس العميل، مما يؤدي إلى:

1. **Rate-limit ينهار** — كل الطلبات تأتي من نفس الـbucket فيُحسب limit واحد للجميع.
2. **Audit logs بلا قيمة جنائية** — كل الصفوف تحمل نفس IP الـproxy.
3. **Origin guards لا تعرف من أين جاء الطلب فعلياً.**

الحل
-----
- ``apply_proxy_fix(app)`` يلفّ ``app.wsgi_app`` بـ ``ProxyFix`` ليحلّ
  ``X-Forwarded-For`` تلقائياً عندما يكون التطبيق خلف عدد معروف من الـhops
  (1 لـHeroku وحده، 2 لـCloudflare + Heroku).
- ``get_real_ip()`` يفضّل ``CF-Connecting-IP`` أولاً (Cloudflare يضعه من الـTCP
  socket مباشرةً ولا يمكن للعميل تزويره عند المرور عبر CF)، ثم يقع على
  ``request.remote_addr`` المُصحَّح بـProxyFix.
- كل قيمة تُتحقَّق بـ``ipaddress.ip_address()`` لمنع تلويث المفاتيح/السجلّات
  برؤوس مشوّهة.

الإعدادات (env vars)
---------------------
- ``TRUST_PROXY_HOPS`` — عدد الـreverse-proxies الموثوقة. الافتراضي:
    * ``1`` في الإنتاج (Heroku)
    * ``0`` في التطوير (يُعطّل ProxyFix كلياً ⇒ لا تأثير على الاختبارات).
- ``TRUST_CF_CONNECTING_IP`` — ``1`` (افتراضي) لقبول رأس Cloudflare،
  ``0`` لتجاهله. يجب أن يكون ``0`` إذا الـorigin يقبل طلبات مباشرة من
  الإنترنت (تجاوز Cloudflare ممكن ⇒ المهاجم قد يزيّف الرأس).

الأمان
-------
- ``ProxyFix(x_for=N)`` يقرأ آخر N إدخالات من ``X-Forwarded-For``. الإدخالات
  الباقية يضعها العميل ⇒ موثوق فقط الأخير منها (proxy الأقرب للـorigin).
- ``CF-Connecting-IP`` آمن **فقط** إذا الـorigin غير قابل للوصول إلا عبر
  Cloudflare (cloudflared tunnel أو firewall على CF IP ranges).
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Optional

from flask import Flask, has_request_context, request
from werkzeug.middleware.proxy_fix import ProxyFix

log = logging.getLogger("tecnogems.request_ip")

# قيمة fallback عند فشل كل المصادر — IP غير صالح للوصول لكنه يبقى string
# صالح كي لا تنفجر طبقات أعلى تتوقع str.
_FALLBACK_IP = "0.0.0.0"


def _env_int(name: str, default: int) -> int:
    """قراءة env var كعدد صحيح مع fallback آمن."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("Invalid integer for %s=%r, falling back to %d", name, raw, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    """قراءة env var كبوولين. القيم الصادقة: 1/true/yes/on (case-insensitive)."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _trust_hops_default() -> int:
    """العدد الافتراضي للـhops حسب البيئة."""
    if (os.getenv("FLASK_ENV") or "").strip().lower() == "production":
        return 1  # Heroku واحد افتراضياً
    return 0  # في الـdev نتركه كما هو حتى لا تتعطّل اختبارات test client


def apply_proxy_fix(app: Flask) -> None:
    """يلفّ ``app.wsgi_app`` بـ ``ProxyFix`` عند الحاجة.

    يُستدعى مرةً واحدة فور إنشاء كائن Flask. آمن للاستدعاء حتى لو لم تكن
    هناك proxies (يصبح no-op).
    """
    hops = _env_int("TRUST_PROXY_HOPS", _trust_hops_default())
    if hops < 1:
        log.info("ProxyFix disabled (TRUST_PROXY_HOPS=%d)", hops)
        return

    app.wsgi_app = ProxyFix(  # type: ignore[assignment]
        app.wsgi_app,
        x_for=hops,
        x_proto=hops,
        x_host=hops,
        x_port=hops,
        x_prefix=hops,
    )
    log.info("ProxyFix enabled with %d hop(s)", hops)


def _is_valid_ip(value: Optional[str]) -> bool:
    """``True`` إذا ``value`` يمثّل IPv4/IPv6 صالح."""
    if not value:
        return False
    try:
        ipaddress.ip_address(value.strip())
        return True
    except (ValueError, TypeError):
        return False


def get_real_ip() -> str:
    """إرجاع IP العميل الحقيقي بأفضل ترتيب موثوق متاح.

    الترتيب:
        1. ``CF-Connecting-IP`` (إذا ``TRUST_CF_CONNECTING_IP=1``)
        2. ``request.remote_addr`` (مُصحَّح تلقائياً بـ``ProxyFix``)
        3. ``"0.0.0.0"`` كـfallback آمن.

    يتم التحقّق من صلاحية كل قيمة قبل قبولها لمنع رؤوس مشوّهة من
    تلويث Limiter keys أو audit logs.
    """
    if not has_request_context():
        return _FALLBACK_IP

    if _env_bool("TRUST_CF_CONNECTING_IP", True):
        cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
        if _is_valid_ip(cf_ip):
            return cf_ip

    remote = (request.remote_addr or "").strip()
    if _is_valid_ip(remote):
        return remote

    return _FALLBACK_IP
