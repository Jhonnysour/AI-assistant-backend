"""Implementación del puerto ConversationRepository con PostgreSQL (SQLAlchemy Core y asyncpg).

Cada lectura devuelve una copia nueva de la conversación; la garantía de un envío activo entre
peticiones la da el índice único parcial de las respuestas en curso.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Row, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from ai_assistant.application.errors import StorageUnavailable
from ai_assistant.application.ports.outbound.conversation_repository import ConversationHeader
from ai_assistant.domain.conversations.model import (
    AssistantResponse,
    Conversation,
    ResponseStatus,
    SendInProgress,
    UserMessage,
)

from .tables import (
    ONE_STREAMING_RESPONSE,
    UNIQUE_POSITION,
    assistant_responses,
    conversations,
    user_messages,
)

CONNECT_TIMEOUT_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 10


def async_url(database_url: str) -> str:
    """postgresql://… (como se configura) -> postgresql+asyncpg://… (como lo usa SQLAlchemy)."""
    return make_url(database_url).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        async_url(database_url), pool_pre_ping=True,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS, "command_timeout": COMMAND_TIMEOUT_SECONDS},
    )


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(hex=value)
    except ValueError:
        return None  # Un identificador mal formado no existe.


def _constraint(error: IntegrityError) -> str | None:
    return getattr(getattr(error, "orig", None), "constraint_name", None) or getattr(
        getattr(getattr(error, "orig", None), "__cause__", None), "constraint_name", None)


class PostgresConversationRepository:
    durable = True

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with self._engine.begin() as connection:
                yield connection
        except (IntegrityError, ProgrammingError):
            raise  # Reglas del esquema o errores del propio código, no caídas de la base de datos.
        except (OSError, TimeoutError, DBAPIError) as error:
            # Sin el mensaje original: podría incluir parámetros con texto del usuario.
            raise StorageUnavailable(type(error).__name__) from None

    async def add(self, conversation: Conversation) -> None:
        async with self._transaction() as db:
            await db.execute(insert(conversations).values(
                id=_uuid(conversation.id), title=conversation.title,
                title_is_custom=conversation.title_is_custom,
                created_at=conversation.created_at, updated_at=conversation.updated_at,
            ))

    async def get(self, conversation_id: str) -> Conversation | None:
        key = _uuid(conversation_id)
        if key is None:
            return None
        async with self._transaction() as db:
            header = (await db.execute(select(conversations).where(conversations.c.id == key))).first()
            if header is None:
                return None
            users = (await db.execute(select(user_messages).where(user_messages.c.conversation_id == key)
                                      .order_by(user_messages.c.position))).all()
            replies = (await db.execute(select(assistant_responses)
                                        .where(assistant_responses.c.conversation_id == key))).all()
        return _conversation(header, users, replies)

    async def list_recent(self) -> list[ConversationHeader]:
        async with self._transaction() as db:
            rows = (await db.execute(select(conversations).order_by(
                conversations.c.updated_at.desc(), conversations.c.id))).all()
        return [ConversationHeader(row.id.hex, row.title, row.title_is_custom, row.created_at, row.updated_at)
                for row in rows]

    async def add_turn(self, conversation: Conversation, user: UserMessage, reply: AssistantResponse) -> None:
        key = _uuid(conversation.id)
        try:
            async with self._transaction() as db:
                await db.execute(insert(user_messages).values(
                    id=_uuid(user.id), conversation_id=key, position=conversation.user_position(user.id),
                    text=user.text, created_at=user.created_at,
                ))
                await db.execute(insert(assistant_responses).values(
                    id=_uuid(reply.id), conversation_id=key, in_reply_to=_uuid(user.id),
                    provider=reply.provider, **_response_values(reply), created_at=reply.created_at,
                ))
                await db.execute(_details_update(conversation))
        except IntegrityError as error:
            if _constraint(error) in (ONE_STREAMING_RESPONSE, UNIQUE_POSITION):
                raise SendInProgress("Ya hay una respuesta en curso en esta conversación.") from None
            raise

    async def save_response(self, conversation_id: str, reply: AssistantResponse) -> None:
        async with self._transaction() as db:
            await db.execute(update(assistant_responses).where(assistant_responses.c.id == _uuid(reply.id))
                             .values(**_response_values(reply)))

    async def save_details(self, conversation: Conversation) -> None:
        async with self._transaction() as db:
            await db.execute(_details_update(conversation))

    async def delete(self, conversation_id: str) -> None:
        key = _uuid(conversation_id)
        if key is None:
            return
        async with self._transaction() as db:
            await db.execute(delete(conversations).where(conversations.c.id == key))

    async def with_active_response(self) -> list[Conversation]:
        async with self._transaction() as db:
            keys = (await db.execute(select(assistant_responses.c.conversation_id).where(
                assistant_responses.c.status == ResponseStatus.STREAMING.value))).scalars().all()
        found = [await self.get(key.hex) for key in keys]
        return [conversation for conversation in found if conversation is not None]


def _response_values(reply: AssistantResponse) -> dict:
    return {
        "model_requested": reply.model_requested, "model_reported": reply.model_reported,
        "status": reply.status.value, "error_code": reply.error_code, "text": reply.text,
        "finished_at": reply.finished_at,
    }


def _details_update(conversation: Conversation):
    return update(conversations).where(conversations.c.id == _uuid(conversation.id)).values(
        title=conversation.title, title_is_custom=conversation.title_is_custom,
        updated_at=conversation.updated_at,
    )


def _conversation(header: Row, users: list[Row], replies: list[Row]) -> Conversation:
    by_message = {row.in_reply_to: row for row in replies}
    messages: list[UserMessage | AssistantResponse] = []
    active = None
    for row in users:
        messages.append(UserMessage(row.id.hex, row.text, row.created_at))
        reply = by_message.get(row.id)
        if reply is None:
            continue
        status = ResponseStatus(reply.status)
        messages.append(AssistantResponse(
            id=reply.id.hex, in_reply_to=row.id.hex, model_requested=reply.model_requested,
            provider=reply.provider, created_at=reply.created_at, text=reply.text, status=status, error_code=reply.error_code,
            model_reported=reply.model_reported, finished_at=reply.finished_at,
        ))
        if status is ResponseStatus.STREAMING:
            active = reply.id.hex
    return Conversation(
        id=header.id.hex, created_at=header.created_at, messages=messages, active_response_id=active,
        title=header.title, title_is_custom=header.title_is_custom, updated_at=header.updated_at,
    )
