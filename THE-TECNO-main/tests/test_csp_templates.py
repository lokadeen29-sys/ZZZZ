"""V53: CI guard — prevent inline <script> without nonce in any template.

V70 (this PR) extends the guard to inline DOM event-handler attributes
(onclick, onsubmit, oninput, onchange, onload, onfocus, onblur, onerror).
The application sets a strict CSP::

    script-src 'self' 'nonce-...'

with no `'unsafe-inline'` and no `'unsafe-hashes'`. Under that policy
inline event-handler attributes are silently blocked by the browser, so
any new occurrence is a real production bug. The test fails fast with
the offending file/line so it cannot regress.
"""
import re
from pathlib import Path

SCRIPT_RE = re.compile(
    r'<script(?![^>]*(?:type=["\']application/ld\+json["\']|nonce=["\']))[^>]*>',
    re.IGNORECASE,
)

# V70: catch HTML attribute usage like `onclick="..."` or `onsubmit='...'`.
# The trailing `\s*=\s*['"]` is what makes this an *attribute* match — it
# avoids false positives in prose/comments that merely mention the name.
INLINE_HANDLER_RE = re.compile(
    r'\bon(?:click|submit|input|change|load|focus|blur|error)\s*=\s*["\']',
    re.IGNORECASE,
)


def _iter_templates():
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    for html in templates_dir.rglob("*.html"):
        yield templates_dir, html


def _strip_jinja_comments(text: str) -> str:
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


def test_no_inline_script_without_nonce():
    """Every <script> tag must have nonce= or be type=application/ld+json."""
    offenders = []
    for templates_dir, html in _iter_templates():
        cleaned = _strip_jinja_comments(html.read_text(encoding="utf-8"))
        for match in SCRIPT_RE.finditer(cleaned):
            offenders.append(
                f"{html.relative_to(templates_dir)}:{match.start()}: {match.group()[:80]}"
            )
    assert not offenders, (
        "Inline <script> without nonce found. Add nonce=\"{{ csp_nonce }}\" or "
        "move JS to static/js/pages/:\n" + "\n".join(offenders)
    )


def test_no_inline_event_handler_attributes():
    """No template may use inline DOM event-handler attributes.

    Strict CSP (script-src 'self' 'nonce-...') silently blocks these, so
    any new occurrence ships a broken button to production. Use a data-*
    attribute and bind the listener from a <script nonce> block or a
    nonce'd external file in static/js/.
    """
    offenders = []
    for templates_dir, html in _iter_templates():
        cleaned = _strip_jinja_comments(html.read_text(encoding="utf-8"))
        for match in INLINE_HANDLER_RE.finditer(cleaned):
            # Report 1-indexed line number for easier navigation.
            line_no = cleaned.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{html.relative_to(templates_dir)}:{line_no}: {match.group()}"
            )
    assert not offenders, (
        "Inline event-handler attribute(s) found. These are blocked by the "
        "site CSP (script-src 'self' 'nonce-...'). Replace with a data-* "
        "attribute + addEventListener in a nonce'd <script> block:\n"
        + "\n".join(offenders)
    )
