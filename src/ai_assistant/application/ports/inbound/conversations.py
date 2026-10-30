"""Puertos de entrada: casos de uso de conversación que ofrece la aplicación.

Los adaptadores de entrada (HTTP, CLI…) dependen solo de este módulo: de los protocolos, de los datos
que devuelven y de los errores que lanzan. Los errores del dominio se reexportan aquí porque forman
parte del contrato de estos casos de uso.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ai_assistant.application.errors import StorageUnavailable
from ai_assistant.domain.conversations.model import (
    ConversationError,
    EmptyMessage,
    InvalidTitle,
    ModelOption,
    ResponseNotActive,
    ResponseNotFound,
    SendInProgress,
)

__all__ = [
    "AssistantResponseView", "CancelResponse", "ChatEvent", "ConversationDetail", "ConversationError",
    "ConversationNotFound", "ConversationStarted", "ConversationSummary", "DeleteConversation",
    "EmptyMessage", "GetConversation", "InvalidRequest", "InvalidTitle", "ListConversations", "ListModels",
    "MessageAccepted", "ModelOption", "ModelsUnavailable", "RenameConversation", "ResponseCancelled",
    "ResponseCompleted", "ResponseDelta", "ResponseFailed", "ResponseNotActive", "ResponseNotFound",
    "ResponseStream", "SendInProgress", "SendMessage", "StartConversation", "StorageUnavailable",
    "UserMessageView",
]


# --- Errores (además de los del dominio reexportados arriba) ---

class ConversationNotFound(ConversationError):
    code = "conversation_not_found"


class InvalidRequest(ConversationError):
    code = "invalid_request"


class ModelsUnavailable(Exception):
    """No se pudo obtener el catálogo. code es uno de application.error_codes; message se puede
    mostrar al usuario."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


# --- Datos que devuelven los casos de uso ---

@dataclass(frozen=True)
class ConversationStarted:
    conversation_id: str
    created_at: datetime
    persisted: bool  # False si el backend funciona sin base de datos.


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    title: str | None  # Nulo hasta el primer mensaje.
    title_is_custom: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserMessageView:
    message_id: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class AssistantResponseView:
    response_id: str
    in_reply_to: str
    text: str  # En streaming, el del último punto de control.
    status: str
    error_code: str | None
    error_message: str | None  # Se puede mostrar al usuario.
    model_requested: str
    model_reported: str | None
    provider: str
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class ConversationDetail:
    summary: ConversationSummary
    persisted: bool
    messages: list[UserMessageView | AssistantResponseView]


@dataclass(frozen=True)
class MessageAccepted:
    message_id: str
    response_id: str
    created_at: datetime
    model_requested: str
    provider: str


@dataclass(frozen=True)
class ResponseDelta:
    response_id: str
    text: str


@dataclass(frozen=True)
class ResponseCompleted:
    response_id: str
    finished_at: datetime
    model_reported: str | None


@dataclass(frozen=True)
class ResponseFailed:
    response_id: str
    status: Literal["failed", "interrupted"]
    code: str
    message: str  # Se puede mostrar al usuario; sin detalles técnicos.
    retryable: bool
    finished_at: datetime
    model_reported: str | None


@dataclass(frozen=True)
class ResponseCancelled:
    response_id: str
    finished_at: datetime


ChatEvent = MessageAccepted | ResponseDelta | ResponseCompleted | ResponseFailed | ResponseCancelled


# --- Casos de uso ---

class ResponseStream(Protocol):
    """Un envío aceptado. Hay que consumir events() o llamar a close() para liberar la conversación."""

    message_id: str
    response_id: str

    def events(self) -> AsyncIterator[ChatEvent]:
        """MessageAccepted, fragmentos y exactamente un evento final. Se consume una sola vez."""
        ...

    def close(self) -> None:
        """El consumidor abandona el envío; si seguía en curso, queda interrumpido."""
        ...


# Todos los casos de uso pueden lanzar StorageUnavailable si la base de datos no responde.

class StartConversation(Protocol):
    async def start_conversation(self) -> ConversationStarted: ...


class SendMessage(Protocol):
    async def send(self, conversation_id: str, model: str, text: str) -> ResponseStream:
        """Valida y reserva el envío sin llamar al proveedor. Lanza EmptyMessage, InvalidRequest,
        ConversationNotFound o SendInProgress."""
        ...


class CancelResponse(Protocol):
    async def cancel(self, conversation_id: str, response_id: str) -> None:
        """Cancela una respuesta en curso. Lanza ConversationNotFound, ResponseNotFound o
        ResponseNotActive."""
        ...


class ListConversations(Protocol):
    async def list_conversations(self) -> list[ConversationSummary]:
        """Por última actividad, la más reciente primero."""
        ...


class GetConversation(Protocol):
    async def get_conversation(self, conversation_id: str) -> ConversationDetail:
        """Lanza ConversationNotFound."""
        ...


class RenameConversation(Protocol):
    async def rename_conversation(self, conversation_id: str, title: str | None) -> ConversationSummary:
        """Título propio, o None para volver al derivado. Lanza ConversationNotFound o InvalidTitle."""
        ...


class DeleteConversation(Protocol):
    async def delete_conversation(self, conversation_id: str) -> None:
        """Borrado definitivo. Lanza ConversationNotFound o SendInProgress (respuesta en curso)."""
        ...


class ListModels(Protocol):
    async def list_models(self) -> list[ModelOption]:
        """Modelos ofrecidos con su acceso comprobado o no. Lanza ModelsUnavailable."""
        ...
