"""orders_provider_response_raw — V73 orphan-recovery restore.

Revision ID: 0002_orphan_recovery
Revises: 0001_baseline
Create Date: 2026-05-20 00:00:00 UTC

Adds the ``orders.provider_response_raw`` text column and the
``idx_orders_orphan`` index. Together they let the worker save the raw
supplier reply for every order *before* parsing — so even orders whose
``provider_order_id`` extraction failed (the V73 bug) keep enough
forensic data to be replayed manually.

The index is built as a partial index on Postgres
(``WHERE provider_response_raw IS NOT NULL``) so it only covers rows
the orphan-watch query actually scans. SQLite's index planner does not
make use of ``WHERE`` clauses on partial indexes the same way, and
``op.create_index`` with ``sqlite_where=`` is a recent Alembic feature;
we keep SQLite simple and fall back to a regular composite index there.

Both ``upgrade()`` and ``downgrade()`` are dialect-aware to keep the
migration symmetric on either backend.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
# NOTE: keep this string ≤ 32 characters — Alembic creates the
# ``alembic_version.version_num`` column as ``VARCHAR(32)`` by default,
# and Postgres rejects longer values with StringDataRightTruncation
# (the original ``0002_orders_provider_response_raw`` id was 33 chars
# and broke the V73 deploy on 2026-05-20).
revision: str = "0002_orphan_recovery"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX_NAME = "idx_orders_orphan"


def upgrade() -> None:
    # 1) Add the column. Nullable so existing rows backfill cleanly.
    #    On SQLite this still emits ``ALTER TABLE … ADD COLUMN``; the
    #    ``render_as_batch`` setting in ``migrations/env.py`` only
    #    rewrites operations that SQLite cannot do natively, and ADD
    #    COLUMN works fine without batch mode.
    op.add_column(
        "orders",
        sa.Column("provider_response_raw", sa.Text(), nullable=True),
    )

    # 2) Create the orphan-watch index. Partial on Postgres only.
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    if dialect == "postgresql":
        # Partial composite — indexes only the rows we actually scan
        # for orphan recovery, and stays small even as the orders
        # table grows past a million rows.
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS "
                + _INDEX_NAME
                + " ON orders (status, provider_order_id) "
                "WHERE provider_response_raw IS NOT NULL"
            )
        )
    else:
        # SQLite + any other dialect: plain composite index. The
        # planner will still hit it for ``WHERE status = ? AND
        # provider_order_id …`` queries from
        # ``list_orders_for_auto_refresh``.
        op.create_index(
            _INDEX_NAME,
            "orders",
            ["status", "provider_order_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    # ``DROP INDEX IF EXISTS`` is portable; both engines accept it.
    if dialect == "postgresql":
        op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
    else:
        try:
            op.drop_index(_INDEX_NAME, table_name="orders")
        except Exception:
            # If the index never made it (e.g. partial upgrade),
            # don't block the downgrade.
            pass

    # SQLite cannot DROP COLUMN before 3.35 in some packagings; use
    # batch_alter_table so Alembic falls back to its CREATE-NEW +
    # COPY-DATA recipe when needed. On Postgres this is a direct
    # ``ALTER TABLE … DROP COLUMN``.
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("provider_response_raw")
