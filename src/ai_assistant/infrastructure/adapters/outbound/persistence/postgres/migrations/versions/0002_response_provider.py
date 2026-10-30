"""Proveedor que generó cada respuesta (assistant_responses.provider).

Revisión: 0002
Anterior: 0001
Fecha: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Las filas anteriores no registraron el proveedor: quedan como 'unknown'. Las nuevas siempre lo traen.
    op.add_column("assistant_responses", sa.Column("provider", sa.Text(), nullable=False, server_default="unknown"))
    op.alter_column("assistant_responses", "provider", server_default=None)


def downgrade() -> None:
    op.drop_column("assistant_responses", "provider")
