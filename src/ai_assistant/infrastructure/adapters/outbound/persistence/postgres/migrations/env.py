"""Entorno de Alembic. La URL llega en config.attributes (nunca en un archivo): ver schema.py."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from ai_assistant.infrastructure.adapters.outbound.persistence.postgres.repository import async_url
from ai_assistant.infrastructure.adapters.outbound.persistence.postgres.tables import metadata

config = context.config


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(async_url(config.attributes["database_url"]))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Las migraciones se ejecutan contra la base de datos; el modo offline no se usa.")
asyncio.run(_run_online())
