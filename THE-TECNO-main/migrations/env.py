"""Alembic environment script — V72 (Postgres migration phase 2).

This file is invoked by `alembic` for every command (upgrade, downgrade,
revision, autogenerate, ...). Its job is to:

  1. Resolve the database URL from `app.db.base.DATABASE_URL` (which honours
     the `DATABASE_URL` env var with a fallback to `data/site.db`). This
     way the same migrations work against SQLite locally and Postgres on
     Hetzner without touching `alembic.ini`.

  2. Hand `Base.metadata` (the union of every ORM model in
     `app/db/models.py`) to Alembic so `--autogenerate` can diff models
     against the live DB.

  3. Enable SQLite batch mode in `render_as_batch=True` so future
     migrations that need to ALTER columns work on SQLite (which has no
     native ALTER COLUMN). This is a no-op on Postgres.

The standard alembic-init-generated env.py was inlined and trimmed: the
unused branches (multi-engine, x_argument parsing, async support) were
removed because we ship one DB at a time and don't use them.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


# ---------------------------------------------------------------------------
# Make the application package importable.
#
# `alembic` is invoked from the repo root, but `app.db.base` lives in
# `<repo_root>/app/`. Adding the parent of this file's parent (i.e. the
# repo root) to sys.path covers `python -m alembic`, IDE runs, and the
# test harness in `tests/test_alembic.py` equally.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Now safe to import the ORM layer.
from app.db.base import DATABASE_URL  # noqa: E402
from app.db import models as _models  # noqa: E402,F401  — registers tables
from app.db.base import Base  # noqa: E402


# ---------------------------------------------------------------------------
# Alembic Config object — points at alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Inject the live URL from app.db.base into the Alembic config so both
# offline (--sql) and online modes use the same source of truth. We use
# set_main_option (not env var manipulation) because that's what the rest
# of Alembic reads from.
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Apply logging configured in alembic.ini, but only if the file exists.
# Tests sometimes call into env.py with a synthetic Config and no ini file.
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # Logging misconfiguration must NEVER block a migration. Swallow.
        pass


# Target metadata for `--autogenerate`. Every model imported above is now
# registered on Base.metadata.
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _common_context_kwargs(url: str) -> dict:
    """Kwargs shared between offline + online context configuration.

    `render_as_batch` rewrites ALTER TABLE operations into a CREATE-NEW +
    COPY-DATA + DROP-OLD recipe on SQLite. Postgres ignores it. Always
    keep it on so future migrations are portable.

    `compare_type` lets autogenerate detect column-type changes (e.g.
    Integer → BigInteger). Without it, type drift in models is silent.

    `compare_server_default` similarly catches drift in DEFAULT clauses.
    """
    return {
        "target_metadata": target_metadata,
        "render_as_batch": _is_sqlite(url),
        "compare_type": True,
        "compare_server_default": True,
        # Audit log column is named `metadata` in the DB but `meta` on the
        # model. Suppress autogen warnings for that single mapping by
        # NOT including it in include_object filters; it works because the
        # ORM uses Column("metadata", ...) so the DB column name is
        # already correct from autogen's perspective.
    }


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout, no DB connection.
# Useful for generating a migration script we apply manually with `psql`.
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (`alembic upgrade --sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_context_kwargs(url),
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — open a real connection and run migrations against the DB.
# This is the default path for `alembic upgrade head`.
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")

    # `engine_from_config` gives us a properly-pooled engine respecting
    # everything in alembic.ini. We keep the pool small (NullPool) because
    # migrations are short-lived; we do not want to leak idle Postgres
    # connections after `alembic upgrade head` returns.
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # SQLite needs check_same_thread=False because Alembic's batch
        # mode reuses one connection across multiple threads internally.
        connect_args={"check_same_thread": False} if _is_sqlite(url) else {},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            **_common_context_kwargs(url),
        )

        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
