"""Tablas de conversaciones (SQLAlchemy Core). Las crea la migración inicial; la prueba de migraciones
comprueba que coinciden con estas definiciones."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    false,
)

from ai_assistant.domain.conversations.model import ResponseStatus

ONE_STREAMING_RESPONSE = "one_streaming_response_per_conversation"
UNIQUE_POSITION = "uq_user_messages_conversation_id_position"

metadata = MetaData(naming_convention={
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
})

conversations = Table(
    "conversations", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("title", Text),
    Column("title_is_custom", Boolean, nullable=False, server_default=false()),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_conversations_updated_at", conversations.c.updated_at.desc())

user_messages = Table(
    "user_messages", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("conversation_id", Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"),
           nullable=False),
    Column("position", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "position"),
)

_statuses = ", ".join(f"'{status.value}'" for status in ResponseStatus)

assistant_responses = Table(
    "assistant_responses", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("conversation_id", Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"),
           nullable=False),
    Column("in_reply_to", Uuid(as_uuid=True), ForeignKey("user_messages.id", ondelete="CASCADE"),
           nullable=False, unique=True),
    Column("model_requested", Text, nullable=False),
    Column("model_reported", Text),
    Column("status", Text, nullable=False),
    Column("error_code", Text),
    Column("text", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(f"status IN ({_statuses})", name="status"),
    CheckConstraint("(status = 'streaming') = (finished_at IS NULL)", name="finished_at"),
)
Index("ix_assistant_responses_conversation_id", assistant_responses.c.conversation_id)
Index(ONE_STREAMING_RESPONSE, assistant_responses.c.conversation_id, unique=True,
      postgresql_where=assistant_responses.c.status == ResponseStatus.STREAMING.value)
