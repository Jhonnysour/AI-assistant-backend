"""Conversación temporal: mensajes del usuario, respuestas del asistente y reglas de envío.

Sin dependencias de framework, red ni proveedor. Las fechas las aporta el caso de uso (UTC).
Modelo descrito en docs/domain/conversations.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal


class ResponseStatus(StrEnum):
    STREAMING = "streaming"
    COMPLETED = "completed"  # Solo tras la confirmación de finalización del proveedor.
    FAILED = "failed"  # Error o corte sin texto recibido.
    INTERRUPTED = "interrupted"  # Error o corte con texto parcial.
    CANCELLED = "cancelled"  # El usuario pidió cancelar; puede tener texto parcial.


class ConversationError(Exception):
    code = "conversation_error"


class EmptyMessage(ConversationError):
    code = "empty_message"


class SendInProgress(ConversationError):
    code = "send_in_progress"


class ResponseNotFound(ConversationError):
    code = "response_not_found"


class ResponseNotActive(ConversationError):
    code = "response_not_active"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class UserMessage:
    id: str
    text: str  # Tal como se envió, sin recortar espacios.
    created_at: datetime
    status: Literal["sent"] = "sent"  # Único estado: los mensajes rechazados no llegan a existir.


@dataclass
class AssistantResponse:
    id: str
    in_reply_to: str
    model_requested: str
    created_at: datetime
    text: str = ""
    status: ResponseStatus = ResponseStatus.STREAMING
    error_code: str | None = None
    model_reported: str | None = None
    finished_at: datetime | None = None


@dataclass
class Conversation:
    id: str
    created_at: datetime
    messages: list[UserMessage | AssistantResponse] = field(default_factory=list)
    active_response_id: str | None = None

    @classmethod
    def start(cls, at: datetime) -> Conversation:
        return cls(id=new_id(), created_at=at)

    def begin_turn(self, text: str, model: str, at: datetime) -> tuple[UserMessage, AssistantResponse]:
        """Acepta un mensaje del usuario y crea su respuesta en curso. Un envío activo a la vez."""
        if not text.strip():
            raise EmptyMessage("El mensaje está vacío.")
        if self.active_response_id is not None:
            raise SendInProgress("Ya hay una respuesta en curso en esta conversación.")
        user = UserMessage(new_id(), text, at)
        reply = AssistantResponse(new_id(), user.id, model, at)
        self.messages += [user, reply]
        self.active_response_id = reply.id
        return user, reply

    def response(self, response_id: str) -> AssistantResponse:
        for item in self.messages:
            if isinstance(item, AssistantResponse) and item.id == response_id:
                return item
        raise ResponseNotFound("La respuesta no existe en esta conversación.")

    def is_active(self, response_id: str) -> bool:
        return self.active_response_id == response_id

    def context_for(self, response_id: str) -> list[UserMessage | AssistantResponse]:
        """Historial que se envía al proveedor: intercambios completados y el mensaje actual.

        Las respuestas parciales, fallidas o canceladas (y su pregunta) no se reenvían.
        """
        users = {m.id: m for m in self.messages if isinstance(m, UserMessage)}
        context: list[UserMessage | AssistantResponse] = []
        for item in self.messages:
            if not isinstance(item, AssistantResponse):
                continue
            if item.id == response_id:
                context.append(users[item.in_reply_to])
                break
            if item.status is ResponseStatus.COMPLETED:
                context += [users[item.in_reply_to], item]
        return context

    def _active(self, response_id: str) -> AssistantResponse:
        reply = self.response(response_id)
        if not self.is_active(response_id):
            raise ResponseNotActive("La respuesta ya terminó.")
        return reply

    def append_text(self, response_id: str, text: str) -> None:
        self._active(response_id).text += text

    def complete(self, response_id: str, at: datetime, model_reported: str | None = None) -> None:
        self._finish(response_id, ResponseStatus.COMPLETED, at, model_reported=model_reported)

    def fail(self, response_id: str, error_code: str, at: datetime,
             model_reported: str | None = None) -> ResponseStatus:
        status = ResponseStatus.INTERRUPTED if self._active(response_id).text else ResponseStatus.FAILED
        self._finish(response_id, status, at, error_code, model_reported)
        return status

    def cancel(self, response_id: str, at: datetime) -> None:
        self._finish(response_id, ResponseStatus.CANCELLED, at)

    def _finish(self, response_id: str, status: ResponseStatus, at: datetime,
                error_code: str | None = None, model_reported: str | None = None) -> None:
        reply = self._active(response_id)
        reply.status = status
        reply.error_code = error_code
        reply.model_reported = model_reported
        reply.finished_at = at
        self.active_response_id = None
