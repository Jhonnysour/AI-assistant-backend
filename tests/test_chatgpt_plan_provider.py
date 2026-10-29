"""Adaptador real contra transporte simulado: traducción de eventos y errores al puerto."""

import asyncio
import json

import httpx
import pytest

from ai_assistant.conversations.adapters.outbound.chatgpt_plan.credentials import ModelAccessLog
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.provider import ChatGptPlanProvider
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.session import SessionUnavailable
from ai_assistant.conversations.application import ports
from ai_assistant.conversations.application.ports import ProviderMessage


class FakeSession:
    def __init__(self, error=None):
        self.error = error

    def current(self, http):
        if self.error:
            raise self.error
        return type("Record", (), {"access_token": "tok", "client_id": "oaiapp_x"})()


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def run(handler, session=None, history=(ProviderMessage("user", "hola"),), model_log=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = ChatGptPlanProvider(http, session or FakeSession(), refresh_http=None, model_log=model_log)
            return [event async for event in provider.stream_reply("gpt-6-luna", list(history))]
    return asyncio.run(go())


def test_chunks_and_completion_and_history_in_input():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(
            {"type": "response.output_text.delta", "delta": "Hola"},
            {"type": "response.completed"},
        ))

    history = [ProviderMessage("user", "a"), ProviderMessage("assistant", "b"), ProviderMessage("user", "c")]
    assert run(handler, history=history) == [ports.TextChunk("Hola"), ports.ProviderCompleted()]
    assert seen["body"]["input"] == [
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"},
    ]
    assert seen["body"]["store"] is False and seen["body"]["stream"] is True


@pytest.mark.parametrize("status,payload,expected", [
    (403, {"error": {"code": "subscription_sharing_user_not_eligible"}}, (ports.NOT_ELIGIBLE, False)),
    (429, {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}, (ports.USAGE_LIMIT, False)),
    (400, {"error": {"code": "model_not_found"}}, (ports.MODEL_UNAVAILABLE, False)),
    (503, {"detail": "direct routing disabled"}, (ports.PROVIDER_UNAVAILABLE, True)),
    (401, {"detail": "not accepted"}, (ports.AUTH_REQUIRED, False)),
    (403, {"detail": "region"}, (ports.PROVIDER_ERROR, False)),
])
def test_admission_errors_are_translated(status, payload, expected):
    events = run(lambda r: httpx.Response(status, json=payload))
    assert events == [ports.ProviderFailed(*expected)]


def test_failed_event_after_text_is_translated():
    body = sse({"type": "response.output_text.delta", "delta": "x"},
               {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
    events = run(lambda r: httpx.Response(200, content=body))
    assert events == [ports.TextChunk("x"), ports.ProviderFailed(ports.USAGE_LIMIT, False)]


def test_cut_after_text_is_interrupted_and_before_text_is_unavailable():
    cut = run(lambda r: httpx.Response(200, content=sse({"type": "response.output_text.delta", "delta": "x"})))
    assert cut[-1] == ports.ProviderFailed(ports.INTERRUPTED, True)

    def refuse(request):
        raise httpx.ConnectError("proxy no disponible")

    assert run(refuse) == [ports.ProviderFailed(ports.PROVIDER_UNAVAILABLE, True)]


def test_incomplete_is_reported_as_retryable():
    body = sse({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "x"}}})
    assert run(lambda r: httpx.Response(200, content=body)) == [ports.ProviderFailed(ports.INCOMPLETE, True)]


def test_reported_model_and_verified_model_log(tmp_path):
    log = ModelAccessLog(tmp_path / "verified-models.json")
    body = sse({"type": "response.created", "response": {"model": "gpt-6-luna-x"}},
               {"type": "response.output_text.delta", "delta": "ok"},
               {"type": "response.completed", "response": {"model": "gpt-6-luna-x"}})
    events = run(lambda r: httpx.Response(200, content=body), model_log=log)
    assert events[-1] == ports.ProviderCompleted("gpt-6-luna-x")
    assert set(ModelAccessLog(log.path).verified("oaiapp_x")) == {"gpt-6-luna"}  # El modelo pedido.


def test_failed_response_does_not_mark_model_as_verified(tmp_path):
    log = ModelAccessLog(tmp_path / "verified-models.json")
    body = sse({"type": "response.created", "response": {"model": "m"}},
               {"type": "response.failed", "response": {"model": "m", "error": {"code": "model_not_found"}}})
    events = run(lambda r: httpx.Response(200, content=body), model_log=log)
    assert events == [ports.ProviderFailed(ports.MODEL_UNAVAILABLE, False, "m")]
    assert ModelAccessLog(log.path).verified("oaiapp_x") == {}


def test_missing_session_requires_auth_without_calling_provider():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    events = run(handler, session=FakeSession(SessionUnavailable("no_session", "x")))
    assert events == [ports.ProviderFailed(ports.AUTH_REQUIRED, False)]
    assert calls == []


def test_cancel_while_waiting_for_provider_closes_http_stream():
    """Núcleo + adaptador real + httpx: cancelar interrumpe la espera de red y cierra el stream."""
    from ai_assistant.conversations.adapters.outbound.in_memory_repository import (
        InMemoryConversationRepository,
    )
    from ai_assistant.conversations.application.chat import ChatService, ResponseCancelled, ResponseDelta
    from ai_assistant.conversations.domain.model import ResponseStatus

    class Stalled(httpx.AsyncByteStream):
        def __init__(self):
            self.gate = asyncio.Event()
            self.closed = False

        async def __aiter__(self):
            yield sse({"type": "response.output_text.delta", "delta": "parcial"})
            await self.gate.wait()  # El proveedor deja de enviar datos.
            yield sse({"type": "response.completed"})

        async def aclose(self):
            self.closed = True

    async def scenario():
        body = Stalled()
        transport = httpx.MockTransport(lambda r: httpx.Response(200, stream=body))
        async with httpx.AsyncClient(transport=transport) as http:
            service = ChatService(InMemoryConversationRepository(),
                                  ChatGptPlanProvider(http, FakeSession(), refresh_http=None))
            conversation = service.start_conversation()
            turn = service.send(conversation.id, "gpt-6-luna", "hola")
            events = turn.events()
            await anext(events)
            assert await anext(events) == ResponseDelta(turn.response_id, "parcial")
            pending = asyncio.ensure_future(anext(events))
            await asyncio.sleep(0.05)
            assert not pending.done()
            await asyncio.wait_for(service.cancel(conversation.id, turn.response_id), timeout=1)
            assert isinstance(await asyncio.wait_for(pending, timeout=1), ResponseCancelled)
            assert body.closed
            reply = conversation.response(turn.response_id)
            assert (reply.status, reply.text) == (ResponseStatus.CANCELLED, "parcial")

    asyncio.run(scenario())
