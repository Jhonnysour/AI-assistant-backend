"""Conversaciones, mensajes del usuario y respuestas del asistente (SPEC-002).

Revisión: 0001
Anterior:
Fecha: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATUSES = "'streaming', 'completed', 'failed', 'interrupted', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.Text()),
        sa.Column("title_is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index("ix_conversations_updated_at", "conversations", [sa.text("updated_at DESC")])

    op.create_table(
        "user_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_messages"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE",
                                name="fk_user_messages_conversation_id_conversations"),
        sa.UniqueConstraint("conversation_id", "position", name="uq_user_messages_conversation_id_position"),
    )

    op.create_table(
        "assistant_responses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("in_reply_to", sa.Uuid(), nullable=False),
        sa.Column("model_requested", sa.Text(), nullable=False),
        sa.Column("model_reported", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_assistant_responses"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE",
                                name="fk_assistant_responses_conversation_id_conversations"),
        sa.ForeignKeyConstraint(["in_reply_to"], ["user_messages.id"], ondelete="CASCADE",
                                name="fk_assistant_responses_in_reply_to_user_messages"),
        sa.UniqueConstraint("in_reply_to", name="uq_assistant_responses_in_reply_to"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_assistant_responses_status"),
        sa.CheckConstraint("(status = 'streaming') = (finished_at IS NULL)",
                           name="ck_assistant_responses_finished_at"),
    )
    op.create_index("ix_assistant_responses_conversation_id", "assistant_responses", ["conversation_id"])
    op.create_index("one_streaming_response_per_conversation", "assistant_responses", ["conversation_id"],
                    unique=True, postgresql_where=sa.text("status = 'streaming'"))


def downgrade() -> None:
    op.drop_table("assistant_responses")
    op.drop_table("user_messages")
    op.drop_table("conversations")
