# Tasks: Phased Implementation Plan

> **How to resume in a new chat:** open the new chat and say
> "Continue the app.py refactor from `.kiro/specs/app-refactor/tasks.md`".
> Kiro will read the three spec files and pick up from the next unchecked phase.

## Status Legend
- [ ] Not started
- [~] In progress (branch open, PR not merged)
- [x] Done (PR merged)

---

## Phase 0: Plan + structure documented
- [x] Create `.kiro/specs/app-refactor/{requirements,design,tasks}.md`
- [x] Open PR `refactor/app-py-plan` and merge it (PR #3 merged 2026-05-18)
- **Output:** This spec, agreed upon. No code changes.

---

## Phase 1: Extract pure utilities and services
**Branch:** `refactor/phase-1-utils-services`
**Estimated diff:** ~600 lines moved, ~50 lines new bridge code

- [x] Create `app/utils/settings_cache.py` (lines 45-66 of app.py)
- [x] Create `app/services/images.py` (lines 271-441)
- [x] Create `app/utils/i18n.py` (lines 457-593)
- [x] Create `app/services/pricing.py` (lines 623-731)
- [x] Create `app/services/mail.py` (lines 733-1041) + extract HTML to
      `templates/email/{base,verify,reset_password,change_email}.html`
- [x] Create `app/utils/filters.py` (template filters: syria_time, money,
      clean_package_name, order_status_label, order_status_class)
- [x] In `app.py`: replace moved code with re-exports
      (`from app.services.mail import send_email`, etc.)
- [ ] Smoke test: send test email from admin, verify language toggle,
      open product page (price renders), upload an image in admin.
- [x] PR description lists every moved symbol + line ranges.

**Acceptance:** `app.py` drops from 3,724 to ~2,700 lines. Site works.

**Phase 1 result (2026-05-18):** `app.py` shrank from 3,724 to **3,099 lines**
(−625 lines, 17%). The remainder above the ~2,700 target is unavoidable
re-export glue + section-header comments mandated by the "transitional
bridge" rule in design.md (every moved symbol gets a one-line `from … import`
plus 2-3 lines of context for reviewers and future-phase grep'ability). The
bridge will be deleted in Phase 5.

**Files added in Phase 1 (under `THE-TECNO-main/`):**
- `utils/__init__.py`
- `utils/settings_cache.py`
- `utils/i18n.py`
- `utils/filters.py`
- `services/__init__.py`
- `services/images.py`
- `services/pricing.py`
- `services/mail.py`
- `templates/email/base.html`
- `templates/email/verify.html`
- `templates/email/reset_password.html`
- `templates/email/change_email.html`

---

## Phase 2: Complete `auth_bp.py` (existing) + extract `oauth_bp.py`
**Branch:** `refactor/phase-2-auth-oauth`

- [ ] Audit current `routes/auth_bp.py` for missing routes vs. app.py
- [ ] Move remaining auth routes (login, register, logout, password reset,
      verify-email, change-email confirm) into `auth_bp.py`
- [ ] Create `app/routes/oauth_bp.py` (Google OAuth flow + `_inject_oauth_flags`
      context processor)
- [ ] Move `/sw.js` (service worker) into `oauth_bp.py` OR a tiny `misc_bp.py`
- [ ] Smoke test: login, register, password reset email, Google login.

**Acceptance:** All auth & OAuth routes live in their own blueprint files.

---

## Phase 3: Extract `public_bp.py` and `wallet_bp.py`
**Branch:** `refactor/phase-3-public-wallet`
**Estimated diff:** ~700 lines moved

- [ ] Create `app/routes/public_bp.py`:
      home, dashboard, servers, games, all_games, products, products_group,
      checkout, profile, confirm_email_change, orders, legal pages
      (privacy, terms, refund, contact), robots.txt, manifest.json,
      sitemap.xml, legacy_redirect, serve_proof, _block_static_uploads,
      email_info, reset_lang, set_language.
- [ ] Create `app/routes/wallet_bp.py`:
      wallet, wallet_transactions.
- [ ] Smoke test: full purchase flow, deposit flow, all legal pages render.

**Acceptance:** Public-facing routes are out of `app.py`.

---

## Phase 4: Extract `admin_bp.py` and `admin_2fa_bp.py`
**Branch:** `refactor/phase-4-admin`
**Estimated diff:** ~1,300 lines moved

- [ ] Create `app/routes/admin_2fa_bp.py`:
      setup, confirm, challenge, disable, regenerate_backup_codes.
- [ ] Create `app/routes/admin_bp.py`:
      dashboard, orders, order_action, users, user_detail, user_balance,
      balances, games, add_game, game_image, game_products,
      update_manual_syp_prices, accounting, deposits, deposit_action,
      refresh_pending_orders, payment_methods, payment_method_edit,
      test_email, settings.
- [ ] Smoke test: admin login (with 2FA if enabled), order approve/reject,
      deposit approve/reject, settings save, image upload.

**Acceptance:** Admin routes are out of `app.py`.

---

## Phase 5: Extract `api_bp.py` + introduce `create_app()` factory
**Branch:** `refactor/phase-5-factory`

- [ ] Create `app/routes/api_bp.py`:
      api_me, api_games, api_game, api_login, api_register, api_logout,
      api_orders, api_payment_methods, api_wallet, api_validate_player.
      Include `_api_origin_guard` as `@api_bp.before_request`.
- [ ] Create `app/extensions.py` (csrf, limiter, babel, compress instances).
- [ ] Create `app/config.py` (Config classes).
- [ ] Create `app/middleware.py`
      (lang_cookie_reset, error handlers, CSP/cache-control after_request,
      gzip helper).
- [ ] Create `app/bootstrap.py` (setup_once content).
- [ ] Create `app/__init__.py` with `create_app(config_name)`.
- [ ] Create `app/routes/__init__.py` with `register_blueprints(app)`.
- [ ] Replace `app.py` with a thin shim:
      `from app import create_app; app = create_app()`
      (keep this filename so `wsgi.py`/`Procfile` keep working).
- [ ] Remove the transitional re-export bridge.
- [ ] Smoke test: full regression — login, browse, buy, admin, API.

**Acceptance:** `app.py` is ~5 lines. The whole app is in `app/`.

---

## Decisions Log
(append decisions as we make them; do NOT delete entries)

- 2026-05-18: Adopted Application Factory + Layered Blueprints + Services.
- 2026-05-18: `routes/auth_bp.py` is preserved; do not rewrite from scratch.
- 2026-05-18: Email templates move out of Python into Jinja files.
- 2026-05-18: Two admin blueprints (core + 2fa) for separation.
- 2026-05-18: Transitional re-export bridge in `app.py` until final phase.
- 2026-05-18 (Phase 1): **Modules placed at top-level `utils/` and `services/`,
  not under `app/utils/` and `app/services/` as the design.md path suggested.**
  Reason: Python prefers a package (`app/__init__.py`) over a module (`app.py`)
  when both share a name, so creating `app/utils/...` while `app.py` is still
  a module silently breaks every `from app import ...` (wsgi.py, routes/auth_bp.py,
  tests). The packages will be moved into the `app/` package atomically in
  Phase 5 alongside the `create_app()` factory swap. This matches where
  `routes/` already lives today.
- 2026-05-18 (Phase 1): **Email worker threads now spawn from
  `services/mail.py` at import time (not from `app.py`).** The two
  `threading.Thread(target=_email_worker, daemon=True, ...)` calls were
  removed from `app.py` to avoid double-spawning. Behaviour is identical:
  same count (2), same daemon flag, same names (`email-worker-0/1`).
- 2026-05-18 (Phase 1): **`_build_email_html` now defers to a Jinja base
  template (`templates/email/base.html`).** The legacy f-string remains
  invocable via the same name for backwards compatibility — it just
  renders the template now. `send_verification_email`,
  `send_password_reset_email`, and `send_email_change_confirmation`
  render their child templates (`verify.html` / `reset_password.html` /
  `change_email.html`) directly via `render_template`.
- 2026-05-18 (Phase 1): **`MAIL_*` constants and `_aligned_envelope_sender`
  duplicated in `services/mail.py`.** Both modules read from `os.getenv`
  independently. `app.py` re-exports the names for the few callers that
  reference them (e.g. admin `test-email` route). Single source of truth
  in Phase 5 when `app.config` consumes them.

## Open Questions
(answer before starting the relevant phase)

- ~~Q1 (Phase 1): Do we want async email sending preserved exactly, or is
      this a chance to introduce a proper task queue?~~ **Decision (2026-05-18):
      preserved exactly.** Phase 1 is behaviour-preserving by definition;
      switching to a proper task queue is out of scope. The existing dual
      path (RQ when `REDIS_URL` is set, in-process thread queue otherwise)
      is moved to `services/mail.py` verbatim.
- Q2 (Phase 5): Should `app.py` become a 1-line shim, or should we update
      `Procfile` / `wsgi.py` to point at `app:create_app()` directly?
      **Decision pending.**
