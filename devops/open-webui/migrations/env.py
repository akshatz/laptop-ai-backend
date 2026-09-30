import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Hand-written migrations only: these tables have no ORM models to autogenerate from.
target_metadata = None

# Open WebUI's database (the same one its DATABASE_URL points at). A plain postgresql:// URL uses
# the psycopg 3 driver under SQLAlchemy 2.1.
DATABASE_URL = os.environ["OPEN_WEBUI_FN_DATABASE_URL"]

# Open WebUI keeps its own migration history in `alembic_version` in this same database;
# using a different table keeps the two histories from ever seeing each other.
VERSION_TABLE = "fn_alembic_version"


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, version_table=VERSION_TABLE)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
