"""Migraciones del esquema (Alembic) y comprobación de que la base de datos está al día."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class SchemaOutdated(RuntimeError):
    """La base de datos no tiene la última migración aplicada."""


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # En attributes y no en las opciones: no pasa por configparser ni acaba escrita en ningún sitio.
    config.attributes["database_url"] = database_url
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    """Aplica las migraciones pendientes. Usa su propio bucle asyncio: llamar fuera de uno."""
    command.upgrade(alembic_config(database_url), revision)


def head_revision() -> str | None:
    return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(lambda sync: MigrationContext.configure(sync).get_current_revision())


async def ensure_current(engine: AsyncEngine) -> None:
    current, head = await current_revision(engine), head_revision()
    if current != head:
        raise SchemaOutdated(f"El esquema está en {current or 'ninguna revisión'} y el código espera {head}.")
