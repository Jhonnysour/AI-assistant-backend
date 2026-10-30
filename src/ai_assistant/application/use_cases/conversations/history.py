"""Casos de uso: listar, leer, renombrar y borrar conversaciones guardadas (SPEC-002)."""

from __future__ import annotations

from ai_assistant.application.ports.inbound.conversations import (
    AssistantResponseView,
    ConversationDetail,
    ConversationNotFound,
    ConversationSummary,
    UserMessageView,
)
from ai_assistant.application.ports.outbound.clock import Clock
from ai_assistant.application.ports.outbound.conversation_repository import (
    ConversationHeader,
    ConversationRepository,
)
from ai_assistant.application.use_cases.conversations.messages import user_message
from ai_assistant.domain.conversations.model import AssistantResponse, Conversation, UserMessage


def _summary(item: Conversation | ConversationHeader) -> ConversationSummary:
    return ConversationSummary(item.id, item.title, item.title_is_custom, item.created_at, item.updated_at)


def _view(item: UserMessage | AssistantResponse) -> UserMessageView | AssistantResponseView:
    if isinstance(item, UserMessage):
        return UserMessageView(item.id, item.text, item.created_at)
    return AssistantResponseView(
        item.id, item.in_reply_to, item.text, item.status.value, item.error_code,
        user_message(item.error_code) if item.error_code else None,
        item.model_requested, item.model_reported, item.created_at, item.finished_at,
    )


class ConversationHistoryService:
    """Implementa ListConversations, GetConversation, RenameConversation y DeleteConversation."""

    def __init__(self, conversations: ConversationRepository, clock: Clock) -> None:
        self._conversations = conversations
        self._now = clock

    async def _get(self, conversation_id: str) -> Conversation:
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound("La conversación no existe.")
        return conversation

    async def list_conversations(self) -> list[ConversationSummary]:
        return [_summary(header) for header in await self._conversations.list_recent()]

    async def get_conversation(self, conversation_id: str) -> ConversationDetail:
        conversation = await self._get(conversation_id)
        return ConversationDetail(_summary(conversation), self._conversations.durable,
                                  [_view(item) for item in conversation.messages])

    async def rename_conversation(self, conversation_id: str, title: str | None) -> ConversationSummary:
        conversation = await self._get(conversation_id)
        conversation.rename(title, self._now())
        await self._conversations.save_details(conversation)
        return _summary(conversation)

    async def delete_conversation(self, conversation_id: str) -> None:
        conversation = await self._get(conversation_id)
        conversation.ensure_deletable()
        await self._conversations.delete(conversation_id)
