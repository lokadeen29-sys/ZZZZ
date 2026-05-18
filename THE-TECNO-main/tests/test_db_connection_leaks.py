"""V53: CI guard — prevent direct connect() usage outside db_conn() context manager.

This test ensures no new code bypasses the db_conn() context manager by calling
connect() directly, which would reintroduce connection leak risks.
"""
import re
from pathlib import Path


# Pattern: `conn = connect()` or `variable = connect()` at any indentation,
# but NOT inside the db_conn() function definition itself.
_DIRECT_CONNECT_RE = re.compile(r"^\s*\w+\s*=\s*connect\(\)", re.MULTILINE)


def test_no_direct_connect_outside_db_conn():
    """Every DB access must go through db_conn() context manager."""
    db_file = Path(__file__).resolve().parent.parent / "database.py"
    content = db_file.read_text(encoding="utf-8")

    # Find all matches
    matches = list(_DIRECT_CONNECT_RE.finditer(content))

    # Filter out the one legitimate usage inside db_conn() itself
    # (the line `conn = connect()` inside the context manager body)
    offenders = []
    lines = content.splitlines()
    for match in matches:
        line_num = content[:match.start()].count("\n") + 1
        # Check if this is inside db_conn() definition (within ~5 lines of @contextmanager)
        context_start = max(0, line_num - 15)
        context_lines = "\n".join(lines[context_start:line_num])
        if "def db_conn()" in context_lines or "Use instead of the old pattern:" in context_lines:
            continue  # This is the legitimate usage inside db_conn or its docstring
        offenders.append(f"  line {line_num}: {lines[line_num - 1].strip()}")

    assert not offenders, (
        "Direct connect() usage found outside db_conn(). "
        "Use `with db_conn() as conn:` instead:\n" + "\n".join(offenders)
    )


def test_db_conn_usage_count():
    """Sanity check: db_conn() should be used extensively (>70 times)."""
    db_file = Path(__file__).resolve().parent.parent / "database.py"
    content = db_file.read_text(encoding="utf-8")
    count = content.count("with db_conn()")
    assert count >= 70, (
        f"Expected at least 70 uses of `with db_conn()`, found {count}. "
        "New DB functions must use the context manager."
    )
