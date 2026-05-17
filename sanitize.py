"""
V53 security — input sanitization helpers (Defense in depth).

Even though Jinja2 autoescape is on and templates use `innerText`,
we sanitize at *input* time to protect against future refactors that
might introduce `innerHTML` or `|safe` by mistake.
"""
from __future__ import annotations

import bleach

# For plain-text fields (address, phone, IBAN, name): no HTML at all
PLAIN_TEXT_TAGS: list = []
PLAIN_TEXT_ATTRS: dict = {}

# For rich-text fields (instructions): limited safe HTML
RICH_TEXT_TAGS = ["br", "b", "i", "strong", "em", "ul", "ol", "li", "p", "a"]
RICH_TEXT_ATTRS = {"a": ["href", "title"]}
RICH_TEXT_PROTOCOLS = ["http", "https", "mailto"]


def clean_plain_text(value: str, max_len: int = 500) -> str:
    """Strip ALL HTML tags and cap length. For simple text fields."""
    if not value:
        return ""
    cleaned = bleach.clean(value, tags=PLAIN_TEXT_TAGS, attributes=PLAIN_TEXT_ATTRS, strip=True)
    return cleaned[:max_len].strip()


def clean_rich_text(value: str, max_len: int = 2000) -> str:
    """Allow limited safe HTML (for instructions). Strips scripts, event handlers,
    javascript: URIs etc. Also linkifies plain-text URLs."""
    if not value:
        return ""
    cleaned = bleach.clean(
        value,
        tags=RICH_TEXT_TAGS,
        attributes=RICH_TEXT_ATTRS,
        protocols=RICH_TEXT_PROTOCOLS,
        strip=True,
    )
    cleaned = bleach.linkify(cleaned)
    return cleaned[:max_len].strip()
