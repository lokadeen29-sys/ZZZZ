"""V53 REFACTOR (phase 1): pure utility helpers extracted from app.py.

This package holds modules that have **no Flask app coupling** beyond
`flask.session` / `flask.request` (i.e. they don't touch `current_app`,
extensions, or DB session lifecycle). They are safe to import from any
Blueprint, service, or test module.

Phase 1 modules:
- settings_cache: 30-second in-memory cache around database.get_setting/set_setting
- i18n:           public-facing translation table + current_lang / tr / lang_url
- filters:        Jinja template filters (syria_time, money, status labels, ...)

Future phases will add:
- auth (login_required, admin_required, current_user)
- security (validate_password_strength, safe_next_url)
"""
