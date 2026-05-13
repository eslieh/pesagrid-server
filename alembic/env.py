"""Alembic environment — multi-schema, multi-module setup.

All module models are imported here so their metadata is registered with the
shared Base before autogenerate runs.  Each module's tables live in their own
PostgreSQL schema (e.g. auth.users, accounts.invoices …).

The alembic_version tracking table itself stays in the public schema.
"""
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, text
from alembic import context
import os
import sys

# ---------------------------------------------------------------------------
# Make sure the project root is on sys.path so app.* imports work.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# App settings (provides DATABASE_URL).
# ---------------------------------------------------------------------------
from app.core.config import settings  # noqa: E402

# ---------------------------------------------------------------------------
# Import EVERY module's models so SQLAlchemy registers their tables with Base.
# Add a new import here whenever you create a new module.
# ---------------------------------------------------------------------------
from app.core.base import Base  # noqa: E402, F401
import app.modules.auth.models  # noqa: E402, F401
import app.modules.accounts.models  # noqa: E402, F401
import app.modules.dashboard.models  # noqa: E402, F401
import app.modules.ingestion.models  # noqa: E402, F401
import app.modules.notifications.models  # noqa: E402, F401
import app.modules.obligations.models  # noqa: E402, F401
import app.modules.reciepts.models  # noqa: E402, F401
import app.modules.billing.models   # noqa: E402, F401

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the URL from settings so we never need to hard-code it in alembic.ini
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# All module metadata flows through the shared Base
target_metadata = Base.metadata

# The schemas that Alembic should manage.
# Each entry must match the schema= value set on your models' __table_args__.
MANAGED_SCHEMAS = [
    "auth",
    "accounts",
    "dashboard",
    "ingestion",
    "notifications",
    "obligations",
    "reciepts",
    "billing",
]


def include_object(object, name, type_, reflected, compare_to):
    """Tell autogenerate which objects to track.

    - Drop tables that belong to unmanaged schemas (e.g. PostGIS internal tables).
    - Skip the alembic_version table — Alembic owns that, not our migrations.
    - Track everything in our MANAGED_SCHEMAS list.
    """
    if type_ == "table":
        # Never touch the version-tracking table
        if name == "alembic_version":
            return False
        schema = object.schema if hasattr(object, "schema") else None
        if schema not in MANAGED_SCHEMAS and schema is not None:
            return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Schema support
        include_schemas=True,
        include_object=include_object,
        # Keep alembic_version in public schema
        version_table="alembic_version",
        version_table_schema="public",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure all managed schemas exist before running migrations
        for schema in MANAGED_SCHEMAS:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Schema support
            include_schemas=True,
            include_object=include_object,
            # Keep alembic_version in public schema
            version_table="alembic_version",
            version_table_schema="public",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()