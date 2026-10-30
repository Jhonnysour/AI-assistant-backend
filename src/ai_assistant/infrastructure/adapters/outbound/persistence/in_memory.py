"""Conversaciones temporales en memoria del proceso (SPEC-001: sin persistencia)."""

from __future__ import annotations

from ai_assistant.domain.conversations.model import Conversation


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}

    def add(self, conversation: Conversation) -> None:
        self._items[conversation.id] = conversation

    def get(self, conversation_id: str) -> Conversation | None:
        return self._items.get(conversation_id)

    def save(self, conversation: Conversation) -> None:
        self._items[conversation.id] = conversation
