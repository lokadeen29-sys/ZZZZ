"""V53 REFACTOR (phase 1): business-logic services extracted from app.py.

Modules in this package contain code that has **no Flask request/session
coupling**. They take primitives or DB models, return primitives or models.
This makes them easy to unit-test and reusable from a CLI, RQ worker, or
future API server.

Phase 1 modules:
- images:  upload sanitisation (magic-byte check, EXIF strip, WebP convert,
           SVG XSS sanitiser).
- pricing: USD/SYP conversion, manual-SYP overrides, profit-percent calcs.
- mail:    SMTP sender + Jinja-rendered transactional emails.

Future phases will add wallet, orders, etc.
"""
