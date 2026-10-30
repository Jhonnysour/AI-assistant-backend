"""Aplica las migraciones del esquema de PostgreSQL (SPEC-002).

Uso: python -m ai_assistant.infrastructure.tools.migrate
Lee la base de datos de AI_ASSISTANT_DATABASE_URL. Nunca imprime la contraseña.
"""

from __future__ import annotations

import sys

from sqlalchemy.engine import make_url

from ai_assistant.infrastructure.adapters.outbound.persistence.postgres import schema
from ai_assistant.infrastructure.config import load_settings


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    settings = load_settings()
    if not settings.database_url:
        print("Falta AI_ASSISTANT_DATABASE_URL (ver docs/development.md).", file=sys.stderr)
        return 2
    print(f"Base de datos: {make_url(settings.database_url).render_as_string(hide_password=True)}")
    schema.upgrade(settings.database_url)
    print(f"Esquema al día (revisión {schema.head_revision()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
