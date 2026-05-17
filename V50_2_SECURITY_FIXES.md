# V50.2 Security Fixes — Medium + Low (22 findings)

This patch is the follow-up to `V50_SECURITY_FIXES.md` and applies the
**22 remaining findings** from the V49 security audit that were deferred
as *medium* or *low* severity. V50.1 already shipped the 14 critical/high
fixes.

## Summary

| # | Severity | Area | File(s) |
|---|---|---|---|
| M1 | 🟡 Medium | Game-image filenames are predictable (enumerable) | `app.py` |
| M2 | 🟡 Medium | Supplier error messages stored raw in order `note` | `tasks.py` |
| M3 | 🟡 Medium | No rate limit on `/reset-password/<token>` | `app.py` |
| M4 | 🟡 Medium | No rate limit on `/verify-email/<token>` | `app.py` |
| M5 | 🟡 Medium | No rate limit on `/resend-verification` | `app.py` |
| M6 | 🟡 Medium | No audit log on `admin_order_action` | `app.py` |
| M7 | 🟡 Medium | No audit log on `admin_deposit_action` | `app.py` |
| M8 | 🟡 Medium | No audit log on `admin_add_game` / `admin_game_image` | `app.py` |
| M9 | 🟡 Medium | CSRF-exempt `/api/*` endpoints trust any Origin | `app.py` |
| M10 | 🟡 Medium | CSP is missing `object-src`, `form-action`, `frame-src`, `upgrade-insecure-requests` | `app.py` |
| M11 | 🟡 Medium | `WTF_CSRF_SSL_STRICT=False` hard-coded | `app.py` |
| M12 | 🟡 Medium | Flask-Limiter uses in-memory backend (per-worker) | `app.py` |
| M13 | 🟡 Medium | Session lifetime is 14 days | `app.py`, `.env.example` |
| L1 | ⚪ Low | `.secret_key` / `*.log` missing from `.gitignore` | `.gitignore` |
| L2 | ⚪ Low | `/logout` accepts GET (CSRF-less logout) | `app.py`, `templates/base.html` |
| L3 | ⚪ Low | PDF deposit proofs allowed (can embed JS) | `app.py` |
| L4 | ⚪ Low | `robots.txt` exposes `/admin` and `/api` to crawlers | `static/robots.txt` |
| L5 | ⚪ Low | `.env.example` ships with a non-empty default `SECRET_KEY` | `.env.example` |
| L6 | ⚪ Low | Missing `X-Permitted-Cross-Domain-Policies` header | `app.py` |
| L7 | ⚪ Low | Missing `Cross-Origin-Opener-Policy` header | `app.py` |
| L8 | ⚪ Low | Missing `Cross-Origin-Resource-Policy` header | `app.py` |
| L9 | ⚪ Low | HSTS missing `preload` + max-age too short | `app.py` |

Every fix is commented in-code with a `V50.2` marker so future reviewers
can trace which change belongs to which finding.

## Details

### M1 — Predictable game-image filenames
`admin_game_image` was saving uploads as `static/uploads/games/{provider}_{game_key}.ext`.
Because `provider` and `game_key` are public information, anyone could
enumerate every game image URL (useful for asset scraping or detecting
which games the operator is about to launch). Added a random 10-char
suffix (`secrets.token_urlsafe(8)`) so filenames are unguessable.

### M2 — Supplier error sanitisation
`tasks.process_order` wrote the supplier's raw error message straight
into `orders.note`, which is then shown in the admin UI and returned to
users via the orders API. Supplier APIs occasionally echo our own API
key, HTML error pages, or internal identifiers. `_sanitise_supplier_note`
now redacts `key=…` / `token=…` patterns, strips HTML tags and control
characters, collapses whitespace, and truncates to 200 characters.

### M3 / M4 / M5 — Rate limits on token endpoints
Added Flask-Limiter decorators:

| Endpoint | Limit |
|---|---|
| `/verify-email/<token>` | 20 per hour |
| `/resend-verification` | 3 per minute, 20 per hour |
| `/reset-password/<token>` | 10 per hour |

Without these, an attacker with a list of leaked tokens could test them
at line rate against our server.

### M6 / M7 / M8 — Admin audit logs
V50.1 only logged `admin_user_balance` changes. V50.2 extends the
`log.warning("ADMIN_… …")` trail to **every** sensitive admin action:
`admin_order_action` (complete/reject), `admin_deposit_action`
(approve/reject), `admin_add_game`, and `admin_game_image`. Each entry
includes admin id/email, target resource, old/new values where relevant,
and source IP — enough to reconstruct a compromise after the fact.

### M9 — API Origin/Referer guard
CSRF protection is deliberately disabled on `/api/*` JSON endpoints
(they are consumed by same-origin fetch with manual CSRF token, by the
mobile/native client, or by `curl`). But "no CSRF token required" means
a browser-based attacker can still hit them if SameSite=Lax lets the
cookie through (rare, but possible for top-level navigations). A new
`before_request` hook rejects any non-GET `/api/*` request whose `Origin`
or `Referer` header points at a different host, and logs it. Requests
with no Origin+Referer (native / curl / server-to-server) are allowed.

### M10 — CSP hardening
Added four directives:

- `object-src 'none'` — blocks `<object>`, `<embed>`, legacy Flash
- `form-action 'self'` — forms can only POST to our origin
- `frame-src 'none'` — we do not embed third-party frames
- `upgrade-insecure-requests` — any stray `http://` asset is auto-upgraded

`style-src 'unsafe-inline'` is kept because ~70 templates still have
`style="…"` attributes; removing it is tracked as a follow-up refactor
and is not a 22-item security fix.

### M11 — `WTF_CSRF_SSL_STRICT` in production
Was hard-coded `False`. Now set to `True` when `FLASK_ENV=production` so
POSTs over HTTPS must have a same-origin `Referer` header, and kept
`False` in dev so local `http://127.0.0.1` testing still works.

### M12 — Flask-Limiter Redis backend
Previously defaulted to in-memory storage, meaning each gunicorn worker
had its own counter (so "10 per minute" was actually "10 × workers per
minute"). When `REDIS_URL` is set the limiter now shares state across
workers via Redis. Falls back to in-memory (with a warning) otherwise.

### M13 — Session lifetime 14 → 7 days
`PERMANENT_SESSION_LIFETIME` is now 7 days by default (overridable via
`SESSION_LIFETIME_DAYS` env var). Long-lived sessions are a stolen-device
risk; a weekly re-login is a sensible compromise.

### L1 — `.gitignore` hardening
`.secret_key` (the dev fallback file written by `app.py` when no
`SECRET_KEY` env var is set) plus `*.log`, `*.sqlite*`, and `rq.db`
are now all ignored.

### L2 — `/logout` via POST
`/logout` now accepts both GET (for back-compat with existing email
links and bookmarked URLs) and POST. The base template has been
updated to use a CSRF-protected POST form so malicious sites cannot
force-logout logged-in users via `<img src="/logout">` or similar.
GET logouts are still accepted but are now logged with a deprecation
warning so we can track and migrate remaining clients.

### L3 — PDF proof uploads removed
`ALLOWED_UPLOAD_EXTS` and `_PROOF_MAGIC` no longer accept `pdf`. PDFs
can embed JavaScript (and have shipped zero-days in PDF readers), and
a payment-proof screenshot is always an image. Admins who really need
a PDF can attach it via the support channel.

### L4 — `robots.txt` blocks sensitive paths
Added `Disallow` rules for `/admin`, `/api`, `/login`, `/register`,
`/wallet`, `/dashboard`, `/reset-password/`, `/verify-email/`,
`/uploads/`, and `/static/uploads/`. These routes require auth or
return JSON — there is zero SEO upside to indexing them and it just
helps attackers map our surface.

### L5 — `.env.example` default secret
`SECRET_KEY=change-this-secret-key` was a literal placeholder that
could be deployed by accident. Now the value is empty and the comment
explicitly says "leave empty; the app refuses to start in prod if
this is unset or default".

### L6 / L7 / L8 — Isolation headers
Added:

- `X-Permitted-Cross-Domain-Policies: none` — blocks Flash/Silverlight
  policy files
- `Cross-Origin-Opener-Policy: same-origin` — isolates browsing
  context, mitigates Spectre and cross-origin `window` references
- `Cross-Origin-Resource-Policy: same-site` — blocks other origins
  from embedding our resources as images/scripts

### L9 — HSTS preload
`Strict-Transport-Security` now sends `max-age=63072000; includeSubDomains; preload`
(two years, the minimum for the hstspreload.org preload list). Admins
still need to submit the domain at hstspreload.org.

## Environment variables (new / changed)

```
SESSION_LIFETIME_DAYS=7       # was effectively 14, now configurable
REDIS_URL=redis://…/0         # now ALSO enables shared Flask-Limiter state
FLASK_ENV=production          # now additionally enables WTF_CSRF_SSL_STRICT
```

Nothing in this patch is a breaking change: unsetting these variables
keeps the previous behaviour or uses a safer default.

## Files changed

```
 app.py                   | +150 -20
 tasks.py                 |  +40 -5
 templates/base.html      |   +8 -1
 static/robots.txt        |  +17 -0
 .gitignore               |   +7 -0
 .env.example             |   +7 -1
 V50_2_SECURITY_FIXES.md  | +200  (this doc)
```

## What is still deferred

The **architectural** items from `V50_SECURITY_FIXES.md` are out of
scope for a security patch and need dedicated migration work:

- SQLite → PostgreSQL
- 2FA for admin accounts
- Sentry / structured logging / on-disk audit table
- DB backups + WAF / Cloudflare fronting
- Removal of all inline `style="…"` (would unlock `style-src 'self'`)

These remain tracked in the audit report and should be scheduled as
ordinary roadmap items rather than security patches.
