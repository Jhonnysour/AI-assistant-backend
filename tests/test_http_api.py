"""Contrato chat-v1 sobre FastAPI: endpoints, errores y SSE (proveedor simulado, sin red externa)."""

import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from ai_assistant.infrastructure.config import load_settings
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.responses import parse_sse
from ai_assistant.infrastructure.adapters.outbound.llm.fake import FakeChatProvider, FakeModelCatalog
from ai_assistant.infrastructure.adapters.outbound.persistence.in_memory import (
    InMemoryConversationRepository,
)
from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.outbound.chat_provider import ProviderCompleted, ProviderFailed, TextChunk
from ai_assistant.application.ports.outbound.model_catalog import CatalogUnavailable
from ai_assistant.domain.conversations.model import ResponseStatus
from ai_assistant.infrastructure.bootstrap.server import app_from_settings, build_use_cases, create_app


class Backend:
    def __init__(self, script=None, catalog=None):
        self.provider = FakeChatProvider(script)
        self.repository = InMemoryConversationRepository()
        self.use_cases = build_use_cases(self.repository, self.provider, catalog or FakeModelCatalog())
        self.app = create_app(self.use_cases)

    def reply(self, conversation_id):
        return self.repository.get(conversation_id).messages[-1]


def events_of(lines):
    return [(name, json.loads(data)) for name, data in parse_sse(lines)]


def new_conversation(client):
    return client.post("/v1/conversations").json()["conversation_id"]


def send(client, conversation_id, text="hola", model="fake-model"):
    with client.stream("POST", f"/v1/conversations/{conversation_id}/messages",
                       json={"model": model, "text": text}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return events_of(response.iter_lines())


@pytest.fixture
def backend():
    return Backend()


@pytest.fixture
def client(backend):
    with TestClient(backend.app) as test_client:
        yield test_client


def test_create_conversation(client):
    response = client.post("/v1/conversations")
    assert response.status_code == 201
    body = response.json()
    assert body["persisted"] is False and body["conversation_id"]
    assert body["created_at"].endswith("Z")


def test_models_from_catalog(client):
    assert client.get("/v1/models").json() == {
        "models": [{"id": "fake-model", "display_name": "Modelo simulado", "access": "verified"}],
    }


@pytest.mark.parametrize("code,status", [
    (codes.AUTH_REQUIRED, 401), (codes.NOT_ELIGIBLE, 403), (codes.USAGE_LIMIT, 503),
    (codes.PROVIDER_UNAVAILABLE, 503), (codes.PROVIDER_ERROR, 502), (codes.INTERNAL_ERROR, 500),
])
def test_models_errors_follow_contract(code, status):
    class FailingCatalog:
        async def list_models(self):
            raise CatalogUnavailable(code)

    with TestClient(Backend(catalog=FailingCatalog()).app) as client:
        response = client.get("/v1/models")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_send_streams_events_until_completed(backend, client):
    conversation_id = new_conversation(client)
    events = send(client, conversation_id, "Saluda", model="gpt-6-luna")
    names = [name for name, _ in events]
    assert names[0] == "message.accepted" and names[-1] == "response.completed"
    assert set(names[1:-1]) == {"response.delta"}
    accepted, completed = events[0][1], events[-1][1]
    assert accepted["model_requested"] == "gpt-6-luna" and accepted["created_at"].endswith("Z")
    assert completed["response_id"] == accepted["response_id"] and completed["finished_at"].endswith("Z")
    text = "".join(data["text"] for name, data in events if name == "response.delta")
    assert text == "Respuesta simulada a: Saluda"
    assert backend.reply(conversation_id).status is ResponseStatus.COMPLETED


def test_provider_failure_is_a_failed_event(client, backend):
    backend.provider.script = [TextChunk("a"), ProviderFailed(codes.USAGE_LIMIT, False)]
    name, data = send(client, new_conversation(client))[-1]
    assert name == "response.failed"
    assert data["status"] == "interrupted"
    assert data["error"] == {"code": "usage_limit", "retryable": False,
                             "message": "Se alcanzó el límite de uso del proveedor. Inténtalo más tarde."}


@pytest.mark.parametrize("body,status,code", [
    ({"model": "m", "text": "   "}, 400, "empty_message"),
    ({"model": " ", "text": "hola"}, 400, "invalid_request"),
    ({"text": "hola"}, 400, "invalid_request"),
    ({"model": "m", "text": 5}, 400, "invalid_request"),
])
def test_errors_before_stream_do_not_call_provider(client, backend, body, status, code):
    response = client.post(f"/v1/conversations/{new_conversation(client)}/messages", json=body)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "hola" not in response.text  # No se repite el texto enviado.
    assert backend.provider.calls == []


def test_unknown_conversation_is_404(client):
    response = client.post("/v1/conversations/nope/messages", json={"model": "m", "text": "hola"})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "conversation_not_found")


def test_second_send_while_active_is_409(client, backend):
    conversation_id = new_conversation(client)
    backend.use_cases.send.send(conversation_id, "m", "uno")  # Envío aceptado y aún en curso.
    response = client.post(f"/v1/conversations/{conversation_id}/messages", json={"model": "m", "text": "dos"})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "send_in_progress")


def test_cancel_endpoint(client, backend):
    conversation_id = new_conversation(client)
    turn = backend.use_cases.send.send(conversation_id, "m", "hola")
    url = f"/v1/conversations/{conversation_id}/responses/{turn.response_id}/cancel"
    assert client.post(url).status_code == 204
    assert backend.reply(conversation_id).status is ResponseStatus.CANCELLED
    assert (client.post(url).status_code, client.post(url).json()["error"]["code"]) == (409, "response_not_active")
    missing = client.post(f"/v1/conversations/{conversation_id}/responses/nope/cancel")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "response_not_found")
    assert client.post(f"/v1/conversations/nope/responses/{turn.response_id}/cancel").status_code == 404


def test_unexpected_error_is_internal_error_without_details(backend):
    class BrokenCatalog:
        async def list_models(self):
            raise RuntimeError("detalle-interno")

    with TestClient(Backend(catalog=BrokenCatalog()).app, raise_server_exceptions=False) as client:
        response = client.get("/v1/models")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Error interno del asistente."}}


def test_default_composition_uses_fake_provider():
    with TestClient(app_from_settings(load_settings({}))) as client:
        events = send(client, new_conversation(client))
    assert events[-1][0] == "response.completed"


# --- Servidor uvicorn real: cancelación y desconexión a mitad del stream ---


@contextmanager
def running(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(5)


def read_until(lines, wanted):
    """Lee eventos SSE de un stream abierto hasta encontrar el evento wanted."""
    name = None
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: ") and name == wanted:
            return json.loads(line[len("data: "):])
    raise AssertionError(f"No llegó {wanted}")


def wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def stalled_backend():
    # El proveedor envía un fragmento y se queda esperando indefinidamente.
    return Backend([TextChunk("parcial"), asyncio.Event(), ProviderCompleted()])


def test_server_cancel_while_provider_is_waiting():
    backend = stalled_backend()
    with running(backend.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
        conversation_id = new_conversation(client)
        with client.stream("POST", f"/v1/conversations/{conversation_id}/messages",
                           json={"model": "m", "text": "hola"}) as response:
            lines = response.iter_lines()
            response_id = read_until(lines, "message.accepted")["response_id"]
            assert read_until(lines, "response.delta")["text"] == "parcial"
            started = time.monotonic()
            cancel = client.post(f"/v1/conversations/{conversation_id}/responses/{response_id}/cancel")
            assert cancel.status_code == 204 and time.monotonic() - started < 1
            assert read_until(lines, "response.cancelled")["response_id"] == response_id
        reply = backend.reply(conversation_id)
        assert (reply.status, reply.text) == (ResponseStatus.CANCELLED, "parcial")
        assert backend.provider.closed_streams == 1
        backend.provider.script = None
        assert send(client, conversation_id)[-1][0] == "response.completed"


def test_server_client_disconnect_without_cancel_is_interrupted():
    backend = stalled_backend()
    with running(backend.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
        conversation_id = new_conversation(client)
        with client.stream("POST", f"/v1/conversations/{conversation_id}/messages",
                           json={"model": "m", "text": "hola"}) as response:
            read_until(response.iter_lines(), "response.delta")
        # Al salir del bloque se cierra la conexión sin llamar a cancel.
        assert wait_for(lambda: backend.reply(conversation_id).status is not ResponseStatus.STREAMING)
        reply = backend.reply(conversation_id)
        assert (reply.status, reply.error_code, reply.text) == (ResponseStatus.INTERRUPTED, "interrupted", "parcial")
        assert wait_for(lambda: backend.provider.closed_streams == 1)
