"""Puerto de salida: almacenamiento de conversaciones."""

from __future__ import annotations

from typing import Protocol

from ai_assistant.domain.conversations.model import Conversation


class ConversationRepository(Protocol):
    def add(self, conversation: Conversation) -> None: ...

    def get(self, conversation_id: str) -> Conversation | None: ...

    def save(self, conversation: Conversation) -> None: ...
