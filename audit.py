"""
V52 (task D) — observability helpers: audit trail + Sentry + JSON logging.

This module is opt-in by environment:

  * ``SENTRY_DSN``               — when set, installs the Flask integration.
  * ``SENTRY_TRACES_SAMPLE_RATE``— float in [0, 1], default 0.0 (no tracing).
  * ``SENTRY_ENVIRONMENT``       — tag for error grouping (default: FLASK_ENV).
  * ``SENTRY_RELEASE``           — optional release tag.
  * ``LOG_JSON``                 — "1" to emit one JSON object per log line.

All functions degrade gracefully: if a dependency is missing, we log a
warning and keep the legacy behaviour. Nothing in this module is allowed to
raise — observability must never break the main request.

Audit flow:

    app.py → log_audit(action=..., ...)
        → database.insert_audit_log()      (permanent record)
        → logger.warning("AUDIT ...")      (ops feed, legacy grep contract)
        → sentry_sdk.add_breadcrumb(...)   (context on next error, if loaded)

Sensitive keys (password, token, secret, otp, code, backup_code) are
redacted before they ever reach the DB, log, or Sentry.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields whose value we must never persist in logs / the audit table / Sentry.
# Match is case-insensitive on the key name.
_SENSITIVE_KEYS = frozenset({
    "password",
    "password_hash",
    "secret",
    "totp_secret",
    "totp_backup_codes",
    "backup_code",
    "backup_codes",
    "code",
    "otp",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "set-cookie",
    "csrf_token",
})

_REDACTED = "[REDACTED]"

# Max length for a single serialised metadata blob before we truncate. The DB
# column is TEXT (unbounded in SQLite), but we still cap to avoid log-bomb
# abuse and keep the audit trail queryable.
_MAX_METADATA_LEN = 4096

log = logging.getLogger("tecnogems.audit")


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------

_sentry_initialised = False


def init_sentry() -> bool:
    """Initialise Sentry SDK with the Flask integration.

    Returns True on success, False if skipped (no DSN or SDK missing).
    Safe to call multiple times; the second call is a no-op.
    """
    global _sentry_initialised
    if _sentry_initialised:
        return True

    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as exc:  # pragma: no cover — dependency missing
        log.warning("sentry-sdk not installed but SENTRY_DSN is set: %s", exc)
        return False

    try:
        traces_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or 0.0)
    except ValueError:
        traces_rate = 0.0

    environment = (
        os.getenv("SENTRY_ENVIRONMENT")
        or os.getenv("FLASK_ENV")
        or "production"
    )
    release = os.getenv("SENTRY_RELEASE") or None

    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                FlaskIntegration(),
                # Breadcrumbs for anything ≥ INFO, events for ERROR+.
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            traces_sample_rate=max(0.0, min(1.0, traces_rate)),
            environment=environment,
            release=release,
            send_default_pii=False,
            before_send=_scrub_event,
        )
        _sentry_initialised = True
        log.info(
            "Sentry initialised (env=%s traces_rate=%.2f)",
            environment, traces_rate,
        )
        return True
    except Exception as exc:  # pragma: no cover — SDK internal failure
        log.warning("Sentry init failed: %s", exc)
        return False


def _scrub_event(event, hint):  # pragma: no cover — defensive
    """Redact any sensitive keys from Sentry events before they are sent.

    Sentry's default scrubbing is good but not exhaustive; we belt-and-
    brace on our own key list so a future refactor cannot accidentally
    leak a TOTP secret via an exception context.
    """
    try:
        _deep_redact(event)
    except Exception:
        pass
    return event


def _deep_redact(obj: Any) -> None:
    """Walk a nested dict/list and redact sensitive keys in place."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                obj[k] = _REDACTED
            else:
                _deep_redact(obj[k])
    elif isinstance(obj, list):
        for item in obj:
            _deep_redact(item)


# ---------------------------------------------------------------------------
# JSON logging
# ---------------------------------------------------------------------------

_json_logging_applied = False


def init_json_logging(force: Optional[bool] = None) -> bool:
    """Reconfigure the root logger to emit JSON lines.

    Activated when ``LOG_JSON=1`` or when ``force=True``. Returns True if
    JSON handlers were installed. On failure (library missing, invalid env),
    keeps the existing handlers and returns False.
    """
    global _json_logging_applied
    if _json_logging_applied:
        return True

    if force is None:
        force = os.getenv("LOG_JSON", "").strip() in {"1", "true", "yes", "on"}
    if not force:
        return False

    try:
        from pythonjsonlogger import jsonlogger
    except Exception as exc:
        log.warning("python-json-logger not installed; LOG_JSON ignored: %s", exc)
        return False

    fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level"},
    )
    root = logging.getLogger()
    # Replace existing handlers so we don't double-emit (one JSON + one text).
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)
    root.setLevel(logging.INFO)

    _json_logging_applied = True
    log.info("JSON logging enabled")
    return True


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _redact_metadata(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of ``data`` with sensitive keys redacted."""
    if not data:
        return {}
    clean: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
            clean[k] = _REDACTED
        else:
            clean[k] = v
    return clean


def _jsonify(data: Dict[str, Any]) -> str:
    """Stable JSON serialisation, truncated to _MAX_METADATA_LEN.

    ``default=str`` so datetime / Decimal / bytes never raise — the audit
    helper MUST NOT fail the underlying request.
    """
    try:
        out = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        out = json.dumps({"_serialisation_error": True}, ensure_ascii=False)
    if len(out) > _MAX_METADATA_LEN:
        out = out[: _MAX_METADATA_LEN - 1] + "…"
    return out


def log_audit(
    action: str,
    *,
    actor_id: Optional[int] = None,
    actor_email: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    old: Optional[Dict[str, Any]] = None,
    new: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    level: str = "warning",
) -> None:
    """Record an admin-relevant action to the audit table + logger + Sentry.

    Parameters
    ----------
    action : str
        Machine-readable action code, e.g. ``"ADMIN_BALANCE_CHANGE"``.
    actor_id, actor_email : the *who* — usually an admin user.
    target_type, target_id : the *what* — e.g. ``("order", "123")``.
    ip, user_agent : the *where* — automatically pulled from Flask
        request context by the caller in app.py.
    old, new : before/after state snapshots. Sensitive keys are redacted.
    metadata : free-form extra fields. Sensitive keys are redacted.
    level : "info" | "warning" | "error" — drives both logger level and
        Sentry breadcrumb category.

    This function NEVER raises. Observability failures must not break
    the request that triggered the audited action.
    """
    old_clean = _redact_metadata(old)
    new_clean = _redact_metadata(new)
    meta_clean = _redact_metadata(metadata)

    # 1) Persist to DB. Best-effort — if the table is missing or the DB is
    # busy, fall back to the logger only.
    try:
        # Local import so `audit` has no hard dependency on database at
        # import time (matters for test modules that reset sys.modules).
        from database import insert_audit_log

        insert_audit_log(
            action=action,
            actor_id=actor_id,
            actor_email=actor_email,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=ip,
            user_agent=(user_agent or "")[:500] or None,
            old_value=_jsonify(old_clean) if old_clean else None,
            new_value=_jsonify(new_clean) if new_clean else None,
            metadata=_jsonify(meta_clean) if meta_clean else None,
        )
    except Exception as exc:
        log.warning("audit DB insert failed for %s: %s", action, exc)

    # 2) Logger record — keeps the legacy "ADMIN_* ..." grep contract alive
    # so V50 / V50.2 audit dashboards continue to work.
    level_name = (level or "warning").lower()
    logger_func = getattr(log, level_name, log.warning)
    logger_func(
        "AUDIT %s actor_id=%s actor_email=%s target=%s:%s ip=%s",
        action, actor_id, actor_email, target_type, target_id, ip,
    )

    # 3) Sentry breadcrumb — gives crash reports the most recent admin
    # actions for free.
    if _sentry_initialised:
        try:
            import sentry_sdk

            sentry_sdk.add_breadcrumb(
                category="audit",
                level=level_name if level_name in {"info", "warning", "error"} else "warning",
                message=action,
                data={
                    "actor_id": actor_id,
                    "actor_email": actor_email,
                    "target_type": target_type,
                    "target_id": target_id,
                    "ip": ip,
                },
            )
        except Exception:
            pass


__all__ = [
    "init_sentry",
    "init_json_logging",
    "log_audit",
]
