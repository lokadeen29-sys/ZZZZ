# V50 Security Fixes — Critical + High Only

This patch applies the 14 security fixes classified as **Critical** or **High** severity in the V49 audit. Medium and Low findings are deferred to a follow-up patch.

## Summary

| # | Severity | Area | File(s) |
|---|---|---|---|
| C1 | 🔴 Critical | `player_id` storage bomb | `app.py`, `templates/checkout.html` |
| C2 | 🔴 Critical | Predictable `order_code` | `database.py` |
| CA | 🔴 Critical | Predictable `deposit_code` | `database.py` |
| C3 | 🔴 Critical | Unbounded deposit amount | `app.py`, `templates/wallet.html` |
| CB | 🔴 Critical | `debug=True` default in `__main__` | `app.py` |
| CC | 🔴 Critical | IDOR latent in `get_order_public(user_id=None)` | `database.py` |
| H4 | 🟠 High | Deposit proofs under `static/uploads/` (publicly served) | `app.py` |
| H7 | 🟠 High | No rate-limit on sensitive admin routes | `app.py` |
| HD | 🟠 High | No max length on password / email / name → CPU DoS | `app.py`, `templates/*.html` |
| HE | 🟠 High | No max length on `proof_text` (storage bomb) | `app.py`, `templates/wallet.html` |
| HF | 🟠 High | `safe_next_url` open-redirect bypass (`\evil`, `//evil`, `:` schemes) | `app.py` |
| HG | 🟠 High | `admin_user_balance` accepts negatives / billions + no audit log | `app.py` |
| HH | 🟠 High | `current_user()` does not re-check `active=1` | `app.py` |
| M10 | 🟠 Adjacent | No logging on failed login attempts | `app.py` |

## Details

### C1 — `player_id` length cap
Previously only a lower bound (`len >= 3`) was enforced. An attacker could submit megabyte-sized `player_id` values that would be stored in `orders.player_id` (TEXT column) and cause storage / memory pressure. Both `/checkout` and `/api/orders` now reject `len(player_id) > MAX_PLAYER_ID_LEN` (64). The HTML input gets `maxlength="64"`.

### C2 / CA — Predictable codes
`order_code = f"ORD{now}{user_id}"` and `deposit_code = f"DEP{now}{user_id}"` were trivially guessable. They are now `f"ORD{secrets.token_urlsafe(10)}"` / `f"DEP{secrets.token_urlsafe(10)}"` — ~80 bits of entropy, no user information leaked. The UNIQUE constraint on both columns catches the astronomically rare collision.

### C3 — Deposit ceiling
`wallet()` only checked `amount > 0`. A malicious user could submit a "deposit" for `1e18` USD, polluting admin queues and risking float-overflow in aggregation. A hard ceiling `MAX_DEPOSIT_USD` (default 10,000, configurable via `MAX_DEPOSIT_USD` env var) is now enforced post-currency-conversion.

### CB — Debugger in `__main__`
`app.run(debug=True)` was hard-coded. Running `python app.py` on any host (even by accident) exposed the Werkzeug debugger, which is remote code execution on that box. Now `debug` is `False` when `FLASK_ENV=production` and defaults to "development" otherwise.

### CC — IDOR in `get_order_public`
The function accepted `user_id=None` and silently returned the order regardless of ownership. It now raises `ValueError` unless an explicit owner id (or the sentinel `"*"` for admin-initiated access) is provided.

### H4 — Uploads moved out of `static/`
Flask's built-in static handler serves `/static/*` to anyone, bypassing the `login_required` on `/uploads/proof/<file>`. `UPLOAD_FOLDER` is now `data/uploads/` (outside `static/`). Existing files in `static/uploads/` are migrated on startup. A new explicit route `/static/uploads/<path>` returns HTTP 403 so any hard-coded legacy URLs fail closed instead of leaking.

### H7 — Admin rate limiting
Added `@limiter.limit(...)` to:
- `admin_user_balance` — 30/min (balance writes)
- `admin_order_action` — 60/min (order approve/reject)
- `admin_deposit_action` — 60/min (deposit approve/reject)
- `admin_add_game` — 20/min

### HD — Input length caps
`login`, `register`, `api_login`, `api_register` now reject any password > 128, email > 120, name > 80, phone > 32 characters **before** calling `check_password_hash` / `generate_password_hash`. Without this a 10 MB password would burn CPU in PBKDF2 for seconds per request.

### HE — Proof text cap
`wallet()` rejects `proof_text` longer than 2000 characters.

### HF — `safe_next_url` hardening
Now rejects:
- any URL with `:` (blocks `javascript:`, `data:`, full `http(s):` schemes)
- any URL with `\` (blocks `/\evil.com` browser quirks)
- encoded protocol-relative paths (`/%2fevil.com`)
- control characters / null bytes
- length > 512

### HG — Admin balance bounds + audit log
`admin_user_balance` now rejects values outside `[0, MAX_ADMIN_BALANCE]` (default 1,000,000). Every change is logged via `log.warning("ADMIN_BALANCE_CHANGE ...")` with admin id, target user id, old/new values, and source IP.

### HH — `current_user()` re-checks `active`
`authenticate()` verifies `active=1` at login, but `current_user()` did not on subsequent requests. A deactivated user kept their 14-day session. Fixed by clearing the session when `user.active != 1`.

### M10 — Failed login logging
Both `/login` and `/api/login` now emit `log.warning("Failed login attempt for email=%s from ip=%s", ...)` on bad credentials so fail2ban / SIEM tooling can see bursts.

## Environment variables (new / relevant)

```
MAX_DEPOSIT_USD=10000       # cap for single deposit (default 10000)
MAX_ADMIN_BALANCE=1000000   # cap for admin-set balance (default 1M)
FLASK_ENV=production        # disables debug; also already blocks weak admin pw
```

## NOT included in this patch (deferred)

Medium / Low severity findings from the full audit:
- Game-image predictable filenames (M8)
- Provider error message data disclosure (M12)
- No rate limit on `/reset-password/<token>`, `/verify-email/<token>` (I, J)
- `.secret_key` missing from `.gitignore` (K)
- No audit log for other admin actions (M)
- CSRF-exempt `/api/*` without Origin check (N)
- CSP `style-src 'unsafe-inline'` (O)
- `/logout` via GET (P)
- PDF proof uploads (Q)
- `WTF_CSRF_SSL_STRICT=False` in production (15)
- Flask-Limiter in-memory backend (W)
- 14-day session lifetime (X)
- `robots.txt` doesn't block admin/api (Z)
- Architectural: SQLite → PostgreSQL, 2FA for admin, RQ+Redis always, Sentry, DB backups, WAF.

These are tracked in the security report and should be addressed in a follow-up `security-v50-medium-low` branch.
