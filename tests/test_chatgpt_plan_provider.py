"""Adaptador real contra transporte simulado: traducción de eventos y errores al puerto."""

import asyncio
import json

import httpx
import pytest

from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.credentials import ModelAccessLog
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.provider import ChatGptPlanProvider
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.session import SessionUnavailable
from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.outbound.chat_provider import (
    ProviderCompleted,
    ProviderFailed,
    ProviderMessage,
    TextChunk,
)
from ai_assistant.application.ports.outbound.model_catalog import CatalogUnavailable
from ai_assistant.domain.conversations.model import ModelOption


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
    assert run(handler, history=history) == [TextChunk("Hola"), ProviderCompleted()]
    assert seen["body"]["input"] == [
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"},
    ]
    assert seen["body"]["store"] is False and seen["body"]["stream"] is True


@pytest.mark.parametrize("status,payload,expected", [
    (403, {"error": {"code": "subscription_sharing_user_not_eligible"}}, (codes.NOT_ELIGIBLE, False)),
    (429, {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}, (codes.USAGE_LIMIT, False)),
    (400, {"error": {"code": "model_not_found"}}, (codes.MODEL_UNAVAILABLE, False)),
    (503, {"detail": "direct routing disabled"}, (codes.PROVIDER_UNAVAILABLE, True)),
    (401, {"detail": "not accepted"}, (codes.AUTH_REQUIRED, False)),
    (403, {"detail": "region"}, (codes.PROVIDER_ERROR, False)),
])
def test_admission_errors_are_translated(status, payload, expected):
    events = run(lambda r: httpx.Response(status, json=payload))
    assert events == [ProviderFailed(*expected)]


def test_failed_event_after_text_is_translated():
    body = sse({"type": "response.output_text.delta", "delta": "x"},
               {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
    events = run(lambda r: httpx.Response(200, content=body))
    assert events == [TextChunk("x"), ProviderFailed(codes.USAGE_LIMIT, False)]


def test_cut_after_text_is_interrupted_and_before_text_is_unavailable():
    cut = run(lambda r: httpx.Response(200, content=sse({"type": "response.output_text.delta", "delta": "x"})))
    assert cut[-1] == ProviderFailed(codes.INTERRUPTED, True)

    def refuse(request):
        raise httpx.ConnectError("proxy no disponible")

    assert run(refuse) == [ProviderFailed(codes.PROVIDER_UNAVAILABLE, True)]


def test_incomplete_is_reported_as_retryable():
    body = sse({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "x"}}})
    assert run(lambda r: httpx.Response(200, content=body)) == [ProviderFailed(codes.INCOMPLETE, True)]


def test_reported_model_and_verified_model_log(tmp_path):
    log = ModelAccessLog(tmp_path / "verified-models.json")
    body = sse({"type": "response.created", "response": {"model": "gpt-6-luna-x"}},
               {"type": "response.output_text.delta", "delta": "ok"},
               {"type": "response.completed", "response": {"model": "gpt-6-luna-x"}})
    events = run(lambda r: httpx.Response(200, content=body), model_log=log)
    assert events[-1] == ProviderCompleted("gpt-6-luna-x")
    assert set(ModelAccessLog(log.path).verified("oaiapp_x")) == {"gpt-6-luna"}  # El modelo pedido.


def test_failed_response_does_not_mark_model_as_verified(tmp_path):
    log = ModelAccessLog(tmp_path / "verified-models.json")
    body = sse({"type": "response.created", "response": {"model": "m"}},
               {"type": "response.failed", "response": {"model": "m", "error": {"code": "model_not_found"}}})
    events = run(lambda r: httpx.Response(200, content=body), model_log=log)
    assert events == [ProviderFailed(codes.MODEL_UNAVAILABLE, False, "m")]
    assert ModelAccessLog(log.path).verified("oaiapp_x") == {}


def test_missing_session_requires_auth_without_calling_provider():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    events = run(handler, session=FakeSession(SessionUnavailable("no_session", "x")))
    assert events == [ProviderFailed(codes.AUTH_REQUIRED, False)]
    assert calls == []


def test_cancel_while_waiting_for_provider_closes_http_stream():
    """Núcleo + adaptador real + httpx: cancelar interrumpe la espera de red y cierra el stream."""
    from ai_assistant.infrastructure.adapters.outbound.persistence.in_memory import (
        InMemoryConversationRepository,
    )
    from ai_assistant.application.ports.inbound.conversations import ResponseCancelled, ResponseDelta
    from ai_assistant.application.use_cases.conversations.chat import ChatService
    from ai_assistant.infrastructure.adapters.outbound.system_clock import SystemClock
    from ai_assistant.domain.conversations.model import ResponseStatus

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
            repository = InMemoryConversationRepository()
            service = ChatService(repository, ChatGptPlanProvider(http, FakeSession(), refresh_http=None),
                                  SystemClock())
            conversation = await repository.get((await service.start_conversation()).conversation_id)
            turn = await service.send(conversation.id, "gpt-6-luna", "hola")
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


def run_catalog(handler, tmp_path, session=None):
    from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.provider import ChatGptPlanCatalog

    log = ModelAccessLog(tmp_path / "verified-models.json")
    log.record_completed("oaiapp_x", "gpt-6-luna")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ChatGptPlanCatalog(http, session or FakeSession(), None, log).list_models()
    return asyncio.run(go())


def test_catalog_marks_verified_models(tmp_path):
    payload = {"models": [
        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list"},
        {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "visibility": "list"},
        {"slug": "hidden", "display_name": "Oculto", "visibility": "hide"},
    ]}
    assert run_catalog(lambda r: httpx.Response(200, json=payload), tmp_path) == [
        ModelOption("gpt-6-luna", "GPT-6-Luna", "verified"),
        ModelOption("gpt-6-sol", "GPT-6-Sol", "unverified"),
    ]


@pytest.mark.parametrize("handler,session,code", [
    (lambda r: httpx.Response(403, json={"error": {"code": "subscription_sharing_user_not_eligible"}}),
     None, codes.NOT_ELIGIBLE),
    (lambda r: httpx.Response(429, json={}), None, codes.USAGE_LIMIT),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("proxy")), None, codes.PROVIDER_UNAVAILABLE),
    (lambda r: httpx.Response(500), FakeSession(SessionUnavailable("no_session", "x")), codes.AUTH_REQUIRED),
])
def test_catalog_errors_are_translated(tmp_path, handler, session, code):
    with pytest.raises(CatalogUnavailable) as error:
        run_catalog(handler, tmp_path, session)
    assert error.value.code == code
