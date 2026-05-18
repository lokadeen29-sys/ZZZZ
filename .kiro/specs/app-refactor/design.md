# Design: Target Architecture for the Refactored App

## Pattern

**Application Factory + Layered Blueprints + Services** — the standard for
production Flask apps of this size.

## Final Directory Layout

```
THE-TECNO-main/
|-- app/
|   |-- __init__.py              # create_app() factory
|   |-- extensions.py            # csrf, limiter, babel, compress (single instantiation)
|   |-- config.py                # Config classes (Dev/Prod/Test)
|   |-- middleware.py            # before/after_request, error handlers, CSP, gzip
|   |-- bootstrap.py             # first-request setup hook (init_db, seed_admin)
|   |
|   |-- routes/
|   |   |-- __init__.py          # register_blueprints(app)
|   |   |-- auth_bp.py           # already exists, will be completed
|   |   |-- public_bp.py         # home, products, checkout, profile, orders, legal
|   |   |-- wallet_bp.py         # /wallet, /wallet/transactions
|   |   |-- admin_bp.py          # /admin/* core
|   |   |-- admin_2fa_bp.py      # /admin/2fa/* (self-contained, separate file)
|   |   |-- api_bp.py            # /api/* (origin guard registered here)
|   |   `-- oauth_bp.py          # Google OAuth + service worker
|   |
|   |-- services/                # Business logic, no Flask request/session here
|   |   |-- pricing.py           # display_price_text, product_sell_usd, USD->SYP rate
|   |   |-- wallet.py            # deposits, refunds, transactions
|   |   |-- orders.py            # enqueue_order_job + helpers
|   |   |-- mail.py              # _send_email_sync, queue, _email_worker, senders
|   |   `-- images.py            # process_upload_to_webp, _sanitise_svg, magic checks
|   |
|   |-- utils/
|   |   |-- i18n.py              # PUBLIC_TRANSLATIONS, current_lang, tr, lang_url
|   |   |-- auth.py              # login_required, admin_required, current_user
|   |   |-- filters.py           # syria_time, money, order_status_label, ...
|   |   |-- security.py          # validate_password_strength, safe_next_url
|   |   `-- settings_cache.py    # get_setting/set_setting (TTL cache)
|   |
|   `-- templates/email/         # Jinja templates instead of Python HTML strings
|       |-- base.html
|       |-- verify.html
|       |-- reset_password.html
|       `-- change_email.html
|
|-- wsgi.py                      # `from app import create_app; app = create_app()`
`-- ... (unchanged: static/, templates/, instance/, etc.)
```

## Key Design Decisions

### 1. Application Factory (`create_app`)
Solves the circular-import problem that blocked the V53 work. Modules import
extensions from `app.extensions` and request context, not the global `app`.

### 2. Single Extensions File
`csrf`, `limiter`, `babel`, `compress` are instantiated once in
`app/extensions.py`. The factory binds them to the app via `init_app(app)`.

### 3. Services Layer (no Flask coupling)
Pure functions for pricing, wallet, orders, mail. They take primitives or
DB models, return primitives or models. They do NOT touch `request`,
`session`, or `flash`. This makes them testable and reusable for a future
CLI / FastAPI / worker process.

### 4. Email Templates in Jinja
The current `_build_email_html()` (~60 lines of HTML inside Python) becomes
`templates/email/base.html` + child templates. The mail service renders them
via `render_template(...)`.

### 5. Two Admin Blueprints
`admin_2fa_bp.py` is split out because the 2FA flow is self-contained, has
its own session keys, and is easier to audit/test in isolation.

### 6. URL prefixes via Blueprint registration
Where possible, use `url_prefix` on the blueprint instead of repeating it on
every route, but only when it doesn't change the final URL.

### 7. Settings cache stays
The 30s TTL cache for SQLite hits (`get_setting/set_setting`) is preserved
verbatim in `utils/settings_cache.py`.

## Migration Strategy

### Per-Phase PR Recipe
1. Create branch `refactor/phase-N-xxx`.
2. Move/extract code; in `app.py` replace removed code with imports from new
   module so existing references (e.g. `from app import send_email`) keep
   working until the final phase.
3. Run smoke tests manually (login, place order, deposit, admin pages).
4. Open PR, review, merge.
5. Update `.kiro/specs/app-refactor/tasks.md` (check off phase, add notes).

### Backwards-Compatibility Bridge
Until the final phase, `app.py` re-exports moved symbols:
```python
# app.py (transitional)
from app.services.mail import send_email, send_verification_email
from app.utils.i18n import tr, current_lang
# ... etc, so any module still doing `from app import X` keeps working
```
This bridge is removed in the final phase.

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Hidden imports of `app.X` from other modules | Phase-by-phase re-exports; grep before each phase for `from app import` |
| Blueprint registration order affects routes | Document order in `routes/__init__.py`; matches current top-down read of `app.py` |
| `before_request` hook order changes | Preserve order: lang reset, setup_once, api_origin_guard |
| CSRF / Limiter scopes change | Re-bind on each blueprint identically; keep `csrf.exempt(api_bp)` if it exists today |
| Gzip after_request loses ordering | Move to `middleware.py` and register exactly once |
