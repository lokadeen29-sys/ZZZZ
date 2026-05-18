# Requirements: Splitting the Monolithic `app.py`

## Context

The current Flask application has a single `app.py` file of **3,724 lines** that
mixes routes (public/admin/API), business logic (pricing, orders, wallet),
email templates, image processing, the translation system, helpers, and
security middleware. This is the largest maintenance risk in the project.

A previous attempt (V53) started extracting `routes/auth_bp.py` but stopped
midway, leaving the codebase in a partially-migrated state.

## Goals

1. **Split `app.py` into focused modules** following the Application Factory
   pattern with layered Blueprints and a Services layer.
2. **Zero functional regression** — every route, behavior, and side effect
   must remain identical to the current production behavior.
3. **Incremental migration** — each phase must be independently deployable
   and testable. The site keeps working at every step.
4. **Preserve existing work** — `routes/auth_bp.py` is kept; we extend it,
   not replace it.

## Non-Goals (out of scope for this refactor)

- DB schema changes
- Template (Jinja HTML) changes, except moving Python email-template strings
  into `templates/email/*.html` files
- Adding new features
- Adding tests (separate effort; tests should be enabled by this refactor,
  not delivered with it)
- Performance tuning beyond what the new structure naturally enables
- Renaming routes or changing URLs

## Success Criteria

- `app.py` shrinks from 3,724 lines to a thin entrypoint (~80 lines or
  replaced by `wsgi.py` calling `create_app()`).
- Each new module has a single, clear responsibility.
- No circular imports.
- All existing routes return the same responses (verified by manual smoke
  tests at the end of each phase).
- The PR for each phase is small enough to review in one sitting
  (target: <800 lines diff per PR, excluding moves).

## Constraints

- The live site must keep working between phases.
- Backwards compatibility: `wsgi.py` (and any deployment config) must keep
  working without changes until the final phase.
- All decorators (`@app.route`, `@login_required`, `@limiter.limit`, CSRF
  exemptions, etc.) preserved exactly as they appear today.
- Existing `.kiro/` content, env vars, and config files unchanged.
