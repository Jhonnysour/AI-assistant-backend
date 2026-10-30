"""Conversaciones en memoria del proceso: se pierden al reiniciar (pruebas y uso sin base de datos).

Los casos de uso comparten el mismo objeto Conversation; las escrituras solo lo registran.
"""

from __future__ import annotations

from ai_assistant.application.ports.outbound.conversation_repository import ConversationHeader
from ai_assistant.domain.conversations.model import AssistantResponse, Conversation, UserMessage


class InMemoryConversationRepository:
    durable = False

    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}

    async def add(self, conversation: Conversation) -> None:
        self._items[conversation.id] = conversation

    async def get(self, conversation_id: str) -> Conversation | None:
        return self._items.get(conversation_id)

    async def list_recent(self) -> list[ConversationHeader]:
        headers = [ConversationHeader(c.id, c.title, c.title_is_custom, c.created_at, c.updated_at)
                   for c in self._items.values()]
        return sorted(headers, key=lambda h: h.updated_at, reverse=True)

    async def add_turn(self, conversation: Conversation, user: UserMessage, reply: AssistantResponse) -> None:
        self._items[conversation.id] = conversation  # El dominio ya garantizó un envío activo (I1).

    async def save_response(self, conversation_id: str, reply: AssistantResponse) -> None:
        pass  # La respuesta es el mismo objeto que ya está en la conversación guardada.

    async def save_details(self, conversation: Conversation) -> None:
        self._items[conversation.id] = conversation

    async def delete(self, conversation_id: str) -> None:
        self._items.pop(conversation_id, None)

    async def with_active_response(self) -> list[Conversation]:
        return [c for c in self._items.values() if c.active_response_id is not None]
