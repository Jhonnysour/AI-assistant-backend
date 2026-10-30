"""Casos de uso de SPEC-002: historial, renombrar, borrar, recuperación y puntos de control."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.inbound.conversations import (
    AssistantResponseView,
    ConversationNotFound,
    InvalidTitle,
    SendInProgress,
    UserMessageView,
)
from ai_assistant.application.ports.outbound.chat_provider import ProviderCompleted, ProviderFailed, TextChunk
from ai_assistant.application.use_cases.conversations.chat import ChatService
from ai_assistant.application.use_cases.conversations.history import ConversationHistoryService
from ai_assistant.application.use_cases.conversations.recovery import RecoverInterruptedResponses
from ai_assistant.domain.conversations.model import Conversation, ResponseStatus, derive_title
from ai_assistant.infrastructure.adapters.outbound.llm.fake import FakeChatProvider
from ai_assistant.infrastructure.adapters.outbound.persistence.in_memory import InMemoryConversationRepository

START = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class StepClock:
    def __init__(self):
        self.now = START

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class RecordingRepository(InMemoryConversationRepository):
    """En memoria, pero apunta qué estado de la respuesta se guardó en cada escritura."""

    def __init__(self):
        super().__init__()
        self.saved: list[tuple[str, str]] = []

    async def save_response(self, conversation_id, reply):
        self.saved.append((reply.status.value, reply.text))


def services(script=None, repository=None, checkpoint_seconds=1.0, delay_seconds=0.0):
    repository = repository or InMemoryConversationRepository()
    clock = StepClock()
    provider = FakeChatProvider(script, delay_seconds=delay_seconds)
    chat = ChatService(repository, provider, clock, checkpoint_seconds=checkpoint_seconds)
    return repository, chat, ConversationHistoryService(repository, clock)


async def converse(chat, conversation_id, text):
    return [event async for event in (await chat.send(conversation_id, "m", text)).events()]


def test_title_is_derived_from_first_message():
    assert derive_title("  hola   mundo \n") == "hola mundo"
    long = "palabra " * 20
    assert len(derive_title(long)) == 60 and derive_title(long).endswith("…")


def test_list_get_rename_and_delete():
    async def scenario():
        _, chat, history = services()
        first = (await chat.start_conversation()).conversation_id
        second = (await chat.start_conversation()).conversation_id
        await converse(chat, first, "¿Qué tiempo hace?")
        assert [s.conversation_id for s in await history.list_conversations()] == [first, second]

        detail = await history.get_conversation(first)
        assert detail.summary.title == "¿Qué tiempo hace?" and not detail.summary.title_is_custom
        assert detail.persisted is False
        user, reply = detail.messages
        assert isinstance(user, UserMessageView) and isinstance(reply, AssistantResponseView)
        assert (reply.in_reply_to, reply.status, reply.error_code) == (user.message_id, "completed", None)

        renamed = await history.rename_conversation(second, "Ideas")
        assert (renamed.title, renamed.title_is_custom) == ("Ideas", True)
        assert [s.conversation_id for s in await history.list_conversations()] == [second, first]
        for bad in ["", " Ideas", "x" * 121]:
            with pytest.raises(InvalidTitle):
                await history.rename_conversation(second, bad)
        reset = await history.rename_conversation(first, None)
        assert (reset.title, reset.title_is_custom) == ("¿Qué tiempo hace?", False)
        assert (await history.rename_conversation(second, None)).title is None  # Sin mensajes.

        await history.delete_conversation(first)
        with pytest.raises(ConversationNotFound):
            await history.get_conversation(first)
        with pytest.raises(ConversationNotFound):
            await history.delete_conversation(first)
    asyncio.run(scenario())


def test_failed_response_in_history_has_user_message():
    async def scenario():
        _, chat, history = services([ProviderFailed(codes.USAGE_LIMIT, False)])
        conversation_id = (await chat.start_conversation()).conversation_id
        await converse(chat, conversation_id, "hola")
        reply = (await history.get_conversation(conversation_id)).messages[1]
        assert (reply.status, reply.error_code) == ("failed", codes.USAGE_LIMIT)
        assert reply.error_message == "Se alcanzó el límite de uso del proveedor. Inténtalo más tarde."
    asyncio.run(scenario())


def test_cannot_delete_while_a_response_is_streaming():
    async def scenario():
        _, chat, history = services()
        conversation_id = (await chat.start_conversation()).conversation_id
        turn = await chat.send(conversation_id, "m", "hola")
        with pytest.raises(SendInProgress):
            await history.delete_conversation(conversation_id)
        await chat.cancel(conversation_id, turn.response_id)
        await history.delete_conversation(conversation_id)
    asyncio.run(scenario())


def test_pc03_recovery_closes_responses_left_streaming():
    async def scenario():
        repository = InMemoryConversationRepository()
        with_text, without_text, finished = (Conversation.start(START) for _ in range(3))
        for conversation in (with_text, without_text, finished):
            await repository.add(conversation)
        reply = with_text.begin_turn("a", "m", START, provider="fake")[1]
        with_text.append_text(reply.id, "parcial")
        without_text.begin_turn("b", "m", START, provider="fake")
        done = finished.begin_turn("c", "m", START, provider="fake")[1]
        finished.complete(done.id, START)

        assert await RecoverInterruptedResponses(repository, StepClock()).run() == 2
        assert (with_text.messages[1].status, with_text.messages[1].error_code) == (
            ResponseStatus.INTERRUPTED, codes.INTERRUPTED)
        assert without_text.messages[1].status is ResponseStatus.FAILED
        assert finished.messages[1].status is ResponseStatus.COMPLETED
        assert await RecoverInterruptedResponses(repository, StepClock()).run() == 0
    asyncio.run(scenario())


def test_pc04_checkpoints_and_final_state_are_saved():
    async def scenario():
        repository = RecordingRepository()
        script = [TextChunk("a"), TextChunk("b"), TextChunk("c"), ProviderCompleted()]
        # Un punto de control por fragmento; el proveedor tarda un poco entre fragmentos.
        _, chat, _ = services(script, repository, checkpoint_seconds=0, delay_seconds=0.01)
        conversation_id = (await chat.start_conversation()).conversation_id
        await converse(chat, conversation_id, "hola")
        assert repository.saved == [("streaming", "a"), ("streaming", "ab"), ("streaming", "abc"),
                                    ("completed", "abc")]

        repository.saved.clear()
        _, chat, _ = services(script, repository, checkpoint_seconds=0)  # Sin pausas: se agrupan.
        conversation_id = (await chat.start_conversation()).conversation_id
        await converse(chat, conversation_id, "hola")
        assert repository.saved[-1] == ("completed", "abc") and len(repository.saved) < 4

        repository.saved.clear()
        _, chat, _ = services(script, repository, checkpoint_seconds=3600)  # Sin puntos de control.
        conversation_id = (await chat.start_conversation()).conversation_id
        await converse(chat, conversation_id, "hola")
        assert repository.saved == [("completed", "abc")]
    asyncio.run(scenario())


def test_cancel_returns_after_the_cancelled_state_is_saved():
    async def scenario():
        repository = RecordingRepository()
        gate = asyncio.Event()
        _, chat, _ = services([TextChunk("parcial"), gate, ProviderCompleted()], repository, 3600)
        conversation_id = (await chat.start_conversation()).conversation_id
        turn = await chat.send(conversation_id, "m", "hola")
        events = turn.events()
        await anext(events)
        await anext(events)
        await chat.cancel(conversation_id, turn.response_id)
        assert repository.saved[-1] == ("cancelled", "parcial")
        await events.aclose()
    asyncio.run(scenario())


def test_failed_checkpoint_is_logged_and_stream_continues(caplog):
    class FlakyRepository(InMemoryConversationRepository):
        async def save_response(self, conversation_id, reply):
            raise RuntimeError(reply.text)  # El mensaje llevaría texto del usuario.

    async def scenario():
        _, chat, _ = services([TextChunk("texto-del-usuario"), ProviderCompleted()], FlakyRepository(), 0)
        conversation_id = (await chat.start_conversation()).conversation_id
        events = await converse(chat, conversation_id, "hola")
        assert type(events[-1]).__name__ == "ResponseCompleted"
    asyncio.run(scenario())
    assert "No se pudo guardar" in caplog.text and "texto-del-usuario" not in caplog.text
