"""Caso de uso de conversación con proveedor simulado (SPEC-001)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from ai_assistant.infrastructure.adapters.outbound.llm.fake import FakeChatProvider
from ai_assistant.infrastructure.adapters.outbound.persistence.in_memory import (
    InMemoryConversationRepository,
)
from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.inbound.conversations import (
    ConversationNotFound,
    InvalidRequest,
    MessageAccepted,
    ResponseCancelled,
    ResponseCompleted,
    ResponseDelta,
    ResponseFailed,
)
from ai_assistant.application.ports.outbound.chat_provider import ProviderCompleted, ProviderFailed, TextChunk
from ai_assistant.application.use_cases.conversations.chat import ChatService
from ai_assistant.domain.conversations.model import (
    AssistantResponse,
    EmptyMessage,
    ResponseNotActive,
    ResponseNotFound,
    ResponseStatus,
    SendInProgress,
    UserMessage,
)

START = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class StepClock:
    """Avanza un segundo en cada lectura."""

    def __init__(self):
        self.now = START

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def done(coroutine):
    """Ejecuta una corrutina que no llega a suspenderse (con el repositorio en memoria), dentro o
    fuera de un bucle asyncio."""
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    coroutine.close()
    raise AssertionError("La corrutina se suspendió; usa await.")


def setup(script=None):
    provider = FakeChatProvider(script)
    repository = InMemoryConversationRepository()
    service = ChatService(repository, provider, clock=StepClock())
    conversation = done(repository.get(done(service.start_conversation()).conversation_id))
    return service, provider, conversation


def reply_of(conversation) -> AssistantResponse:
    return conversation.messages[-1]


async def collect(turn):
    return [event async for event in turn.events()]


def run_turn(service, conversation_id, text, model="model"):
    return asyncio.run(collect(done(service.send(conversation_id, model, text))))


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_ac01_empty_message_is_rejected_without_calling_provider(text):
    service, provider, conversation = setup()
    with pytest.raises(EmptyMessage):
        done(service.send(conversation.id, "model", text))
    assert provider.calls == []
    assert conversation.messages == [] and conversation.active_response_id is None


def test_missing_model_or_conversation_is_rejected():
    service, provider, conversation = setup()
    with pytest.raises(InvalidRequest):
        done(service.send(conversation.id, " ", "hola"))
    with pytest.raises(ConversationNotFound):
        done(service.send("does-not-exist", "model", "hola"))
    assert provider.calls == []


def test_ac02_chunks_then_completed_with_dates_and_models():
    service, provider, conversation = setup([TextChunk("Hola, "), TextChunk("mundo"),
                                             ProviderCompleted("gpt-6-luna-reported")])
    turn = done(service.send(conversation.id, "gpt-6-luna", "Saluda"))
    events = asyncio.run(collect(turn))
    reply = reply_of(conversation)
    assert events == [
        MessageAccepted(turn.message_id, turn.response_id, reply.created_at, "gpt-6-luna", "fake"),
        ResponseDelta(turn.response_id, "Hola, "),
        ResponseDelta(turn.response_id, "mundo"),
        ResponseCompleted(turn.response_id, reply.finished_at, "gpt-6-luna-reported"),
    ]
    assert (reply.text, reply.status) == ("Hola, mundo", ResponseStatus.COMPLETED)
    assert (reply.model_requested, reply.model_reported) == ("gpt-6-luna", "gpt-6-luna-reported")
    assert reply.provider == "fake"
    assert conversation.created_at < reply.created_at < reply.finished_at
    assert reply.created_at.tzinfo is timezone.utc
    assert provider.calls[0][0] == "gpt-6-luna"
    assert conversation.active_response_id is None


def test_turn_is_explicit_and_user_text_is_kept_as_sent():
    service, provider, conversation = setup()
    run_turn(service, conversation.id, "  hola\n")
    user, reply = conversation.messages
    assert isinstance(user, UserMessage) and isinstance(reply, AssistantResponse)
    assert reply.in_reply_to == user.id
    assert user.text == "  hola\n" and user.status == "sent"
    assert provider.calls[0][1][-1].text == "  hola\n"


def test_ac04_provider_unavailable_ends_with_recoverable_error():
    service, provider, conversation = setup([ProviderFailed(codes.PROVIDER_UNAVAILABLE, True)])
    events = run_turn(service, conversation.id, "hola")
    reply = reply_of(conversation)
    assert events[-1] == ResponseFailed(
        reply.id, "failed", codes.PROVIDER_UNAVAILABLE,
        "No se pudo contactar con el proveedor. Puedes reintentar.", True, reply.finished_at, None,
    )
    assert conversation.messages[0].text == "hola"  # Se conserva el texto del usuario.
    assert reply.status is ResponseStatus.FAILED and reply.error_code == codes.PROVIDER_UNAVAILABLE
    assert conversation.active_response_id is None


def test_unexpected_exception_is_internal_error_and_logged_without_details(caplog):
    service, provider, conversation = setup([ConnectionError("detalle-secreto")])
    with caplog.at_level(logging.ERROR):
        events = run_turn(service, conversation.id, "texto-del-usuario")
    assert (events[-1].code, events[-1].message, events[-1].retryable) == (
        codes.INTERNAL_ERROR, "Error interno del asistente.", False)
    assert "ConnectionError" in caplog.text
    assert "detalle-secreto" not in caplog.text and "texto-del-usuario" not in caplog.text
    assert reply_of(conversation).status is ResponseStatus.FAILED


def test_ac05_cut_mid_response_keeps_partial_and_is_not_complete():
    service, provider, conversation = setup([TextChunk("medio ")])  # Sin evento final.
    events = run_turn(service, conversation.id, "hola")
    assert (events[-1].status, events[-1].code, events[-1].retryable) == ("interrupted", codes.INTERRUPTED, True)
    assert not any(isinstance(e, ResponseCompleted) for e in events)
    assert reply_of(conversation).text == "medio "
    assert reply_of(conversation).status is ResponseStatus.INTERRUPTED


def test_provider_failure_after_text_is_interrupted_with_provider_code_and_model():
    service, provider, conversation = setup([TextChunk("a"),
                                             ProviderFailed(codes.USAGE_LIMIT, False, "m-reported")])
    events = run_turn(service, conversation.id, "hola")
    assert (events[-1].status, events[-1].code, events[-1].retryable) == ("interrupted", codes.USAGE_LIMIT, False)
    assert events[-1].model_reported == reply_of(conversation).model_reported == "m-reported"


def test_ac06_cancel_while_provider_is_waiting_is_immediate():
    async def scenario():
        gate = asyncio.Event()  # El proveedor no envía nada hasta que se active.
        service, provider, conversation = setup([TextChunk("parcial"), gate,
                                                 TextChunk(" tardío"), ProviderCompleted()])
        turn = done(service.send(conversation.id, "model", "hola"))
        events = turn.events()
        assert isinstance(await anext(events), MessageAccepted)
        assert await anext(events) == ResponseDelta(turn.response_id, "parcial")
        pending = asyncio.ensure_future(anext(events))
        await settle()
        assert not pending.done()  # Bloqueado esperando al proveedor.

        await asyncio.wait_for(service.cancel(conversation.id, turn.response_id), timeout=1)
        reply = reply_of(conversation)
        assert (reply.status, reply.text) == (ResponseStatus.CANCELLED, "parcial")
        assert conversation.active_response_id is None
        assert provider.closed_streams == 1  # Se cerró la conexión con el proveedor.
        assert await asyncio.wait_for(pending, timeout=1) == ResponseCancelled(turn.response_id, reply.finished_at)
        with pytest.raises(StopAsyncIteration):
            await anext(events)

        gate.set()  # Lo que el proveedor hubiera enviado después se descarta.
        await settle()
        assert reply.text == "parcial" and reply.status is ResponseStatus.CANCELLED
        provider.script = None
        assert isinstance((await collect(done(service.send(conversation.id, "model", "otra"))))[-1], ResponseCompleted)

    asyncio.run(scenario())


def test_ac06_cancel_before_streaming_does_not_call_provider():
    async def scenario():
        service, provider, conversation = setup()
        turn = done(service.send(conversation.id, "model", "hola"))
        await service.cancel(conversation.id, turn.response_id)
        events = await collect(turn)
        assert [type(e) for e in events] == [MessageAccepted, ResponseCancelled]
        assert provider.calls == []
        assert reply_of(conversation).status is ResponseStatus.CANCELLED

    asyncio.run(scenario())


def test_cancel_rejects_unknown_or_finished_responses():
    service, provider, conversation = setup()
    run_turn(service, conversation.id, "hola")
    finished = reply_of(conversation).id
    with pytest.raises(ResponseNotActive):
        asyncio.run(service.cancel(conversation.id, finished))
    with pytest.raises(ResponseNotFound):
        asyncio.run(service.cancel(conversation.id, "no-existe"))
    with pytest.raises(ConversationNotFound):
        asyncio.run(service.cancel("no-existe", finished))
    assert reply_of(conversation).status is ResponseStatus.COMPLETED


@pytest.mark.parametrize("chunks,status", [
    ([TextChunk("parcial")], ResponseStatus.INTERRUPTED),
    ([], ResponseStatus.FAILED),
])
def test_client_disconnect_without_cancel_is_interrupted(chunks, status):
    async def scenario():
        gate = asyncio.Event()
        service, provider, conversation = setup([*chunks, gate, ProviderCompleted()])
        turn = done(service.send(conversation.id, "model", "hola"))
        events = turn.events()
        await anext(events)
        if chunks:
            await anext(events)
        else:
            pending = asyncio.ensure_future(anext(events))
            await settle()
            pending.cancel()  # Así cancela Starlette la tarea al desconectarse el cliente.
            await asyncio.gather(pending, return_exceptions=True)
        await events.aclose()
        await settle()
        reply = reply_of(conversation)
        assert (reply.status, reply.error_code) == (status, codes.INTERRUPTED)
        assert conversation.active_response_id is None
        assert provider.closed_streams == 1

    asyncio.run(scenario())


def test_ac07_second_send_while_active_is_rejected():
    service, provider, conversation = setup()
    first = done(service.send(conversation.id, "model", "uno"))
    with pytest.raises(SendInProgress):
        done(service.send(conversation.id, "model", "dos"))  # Doble clic.
    asyncio.run(collect(first))
    assert len(provider.calls) == 1
    run_turn(service, conversation.id, "dos")
    assert len(provider.calls) == 2


def test_history_includes_only_completed_exchanges():
    service, provider, conversation = setup()
    run_turn(service, conversation.id, "primera")
    provider.script = [TextChunk("roto")]  # Esta respuesta queda interrumpida.
    run_turn(service, conversation.id, "segunda")
    provider.script = None
    run_turn(service, conversation.id, "tercera")
    _, history = provider.calls[-1]
    assert [(m.role, m.text) for m in history] == [
        ("user", "primera"),
        ("assistant", "Respuesta simulada a: primera"),
        ("user", "tercera"),
    ]


def test_manual_retry_is_a_new_turn_and_there_is_no_automatic_retry():
    service, provider, conversation = setup([TextChunk("a"), ProviderFailed(codes.INTERRUPTED, True)])
    run_turn(service, conversation.id, "hola")
    assert len(provider.calls) == 1
    provider.script = None
    run_turn(service, conversation.id, "hola")  # Reintento manual: mismo texto, turno nuevo.
    statuses = [m.status for m in conversation.messages if isinstance(m, AssistantResponse)]
    assert statuses == [ResponseStatus.INTERRUPTED, ResponseStatus.COMPLETED]
    assert len(conversation.messages) == 4
