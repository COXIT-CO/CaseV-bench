"""Alembic environment for the `experiments` schema.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from results_store.models import SCHEMA, metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

URL_VARIABLE = "CASEV_EXPERIMENTS_DDL_DATABASE_URL"


def database_url() -> str:
    url = os.environ.get(URL_VARIABLE)
    if not url:
        sys.exit(
            f"{URL_VARIABLE} is not set."
        )

    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table":
        return obj.schema == SCHEMA
    if type_ == "column":
        return obj.table.schema == SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()

    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
