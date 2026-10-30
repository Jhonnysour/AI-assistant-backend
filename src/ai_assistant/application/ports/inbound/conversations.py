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

from ai_assistant.domain.conversations.model import (
    ConversationError,
    EmptyMessage,
    ModelOption,
    ResponseNotActive,
    ResponseNotFound,
    SendInProgress,
)

__all__ = [
    "CancelResponse", "ChatEvent", "ConversationError", "ConversationNotFound", "ConversationStarted",
    "EmptyMessage", "InvalidRequest", "ListModels", "MessageAccepted", "ModelOption", "ModelsUnavailable",
    "ResponseCancelled", "ResponseCompleted", "ResponseDelta", "ResponseFailed", "ResponseNotActive",
    "ResponseNotFound", "ResponseStream", "SendInProgress", "SendMessage", "StartConversation",
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
    persisted: bool  # False mientras la conversación solo viva en memoria.


@dataclass(frozen=True)
class MessageAccepted:
    message_id: str
    response_id: str
    created_at: datetime
    model_requested: str


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


class StartConversation(Protocol):
    def start_conversation(self) -> ConversationStarted: ...


class SendMessage(Protocol):
    def send(self, conversation_id: str, model: str, text: str) -> ResponseStream:
        """Valida y reserva el envío sin llamar al proveedor. Lanza EmptyMessage, InvalidRequest,
        ConversationNotFound o SendInProgress."""
        ...


class CancelResponse(Protocol):
    async def cancel(self, conversation_id: str, response_id: str) -> None:
        """Cancela una respuesta en curso. Lanza ConversationNotFound, ResponseNotFound o
        ResponseNotActive."""
        ...


class ListModels(Protocol):
    async def list_models(self) -> list[ModelOption]:
        """Modelos ofrecidos con su acceso comprobado o no. Lanza ModelsUnavailable."""
        ...
