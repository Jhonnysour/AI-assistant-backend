"""Puerto de salida: almacenamiento de conversaciones.

Las escrituras van por partes para no reescribir toda la conversación en cada punto de control.
Cualquier fallo del almacenamiento se lanza como application.errors.StorageUnavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ai_assistant.domain.conversations.model import AssistantResponse, Conversation, UserMessage


@dataclass(frozen=True)
class ConversationHeader:
    """Datos de una conversación sin sus mensajes, para listarlas."""

    id: str
    title: str | None
    title_is_custom: bool
    created_at: datetime
    updated_at: datetime


class ConversationRepository(Protocol):
    durable: bool  # True si los datos sobreviven a un reinicio del proceso.

    async def add(self, conversation: Conversation) -> None:
        """Guarda una conversación recién creada (sin mensajes)."""
        ...

    async def get(self, conversation_id: str) -> Conversation | None: ...

    async def list_recent(self) -> list[ConversationHeader]:
        """Conversaciones por última actividad, la más reciente primero."""
        ...

    async def add_turn(self, conversation: Conversation, user: UserMessage, reply: AssistantResponse) -> None:
        """Guarda un envío recién aceptado y los datos de la conversación (título, actividad).

        Lanza SendInProgress si el almacenamiento ya tiene una respuesta en curso en la conversación.
        """
        ...

    async def save_response(self, conversation_id: str, reply: AssistantResponse) -> None:
        """Guarda el estado actual de una respuesta (punto de control o final)."""
        ...

    async def save_details(self, conversation: Conversation) -> None:
        """Guarda los datos de la conversación: título y última actividad."""
        ...

    async def delete(self, conversation_id: str) -> None: ...

    async def with_active_response(self) -> list[Conversation]:
        """Conversaciones con una respuesta en curso guardada (para recuperar tras una caída)."""
        ...
