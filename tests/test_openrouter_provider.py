"""Adaptador de OpenRouter contra transporte simulado (sin red ni créditos)."""

import asyncio
import json
import sys

import httpx
import pytest

from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.outbound.chat_provider import (
    ProviderCompleted,
    ProviderFailed,
    ProviderMessage,
    TextChunk,
)
from ai_assistant.application.ports.outbound.model_catalog import CatalogUnavailable
from ai_assistant.domain.conversations.model import ModelOption
from ai_assistant.infrastructure.adapters.outbound.llm.openrouter.credentials import (
    ApiKeyMissing,
    InvalidApiKey,
    OpenRouterState,
)
from ai_assistant.infrastructure.adapters.outbound.llm.openrouter.provider import (
    DEFAULT_MODEL,
    OpenRouterCatalog,
    OpenRouterProvider,
)
from ai_assistant.infrastructure.model_access_log import ModelAccessLog

KEY = "sk-or-v1-" + "0" * 64  # Ficticia.


def chunk(content=None, finish=None, model=DEFAULT_MODEL, **extra):
    delta = {"content": content} if content is not None else {}
    return {"model": model, "choices": [{"delta": delta, "finish_reason": finish}], **extra}


def sse(*events, done=True):
    body = ": OPENROUTER PROCESSING\n\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def run(handler, model_log=None, history=(ProviderMessage("user", "hola"),)):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = OpenRouterProvider(http, KEY, model_log)
            return [event async for event in provider.stream_reply(DEFAULT_MODEL, list(history))]
    return asyncio.run(go())


def test_streamed_text_completes_and_usage_frame_is_only_accounting(tmp_path):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(
            chunk("Hola, "), chunk("mundo"), chunk(None, "stop"),
            chunk(None, "stop", usage={"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.0001}),
        ))

    log = ModelAccessLog(tmp_path / "verified.json")
    history = [ProviderMessage("user", "hola"), ProviderMessage("assistant", "¿qué tal?"),
               ProviderMessage("user", "bien")]
    events = run(handler, log, history)
    assert events == [TextChunk("Hola, "), TextChunk("mundo"), ProviderCompleted(DEFAULT_MODEL)]
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"] == {"model": DEFAULT_MODEL, "stream": True, "messages": [
        {"role": "user", "content": "hola"}, {"role": "assistant", "content": "¿qué tal?"},
        {"role": "user", "content": "bien"}]}
    assert DEFAULT_MODEL in log.verified("openrouter")


def test_text_and_finish_in_the_same_chunk():
    events = run(lambda r: httpx.Response(200, content=sse(chunk("todo", "stop"))))
    assert events == [TextChunk("todo"), ProviderCompleted(DEFAULT_MODEL)]


@pytest.mark.parametrize("status,expected", [
    (400, (codes.PROVIDER_ERROR, False)),
    (401, (codes.AUTH_REQUIRED, False)),
    (402, (codes.INSUFFICIENT_CREDITS, False)),
    (403, (codes.NOT_ELIGIBLE, False)),
    (404, (codes.MODEL_UNAVAILABLE, False)),
    (408, (codes.PROVIDER_UNAVAILABLE, True)),
    (429, (codes.RATE_LIMITED, True)),
    (502, (codes.PROVIDER_UNAVAILABLE, True)),
    (503, (codes.PROVIDER_UNAVAILABLE, True)),
])
def test_http_errors_before_the_stream(status, expected):
    body = {"error": {"code": status, "message": "detalle"}}
    assert run(lambda r: httpx.Response(status, json=body)) == [ProviderFailed(*expected)]


def test_error_inside_a_200_stream_is_a_failure_not_a_completion():
    error = {"model": DEFAULT_MODEL, "error": {"code": 502, "message": "upstream"},
             "choices": [{"delta": {}, "finish_reason": "error"}]}
    events = run(lambda r: httpx.Response(200, content=sse(chunk("parcial"), error)))
    assert events == [TextChunk("parcial"), ProviderFailed(codes.PROVIDER_UNAVAILABLE, True, DEFAULT_MODEL)]


@pytest.mark.parametrize("finish,expected", [
    ("length", ProviderFailed(codes.INCOMPLETE, True, DEFAULT_MODEL)),
    ("content_filter", ProviderFailed(codes.PROVIDER_ERROR, False, DEFAULT_MODEL)),
])
def test_other_finish_reasons_are_not_completed(finish, expected, tmp_path):
    log = ModelAccessLog(tmp_path / "verified.json")
    events = run(lambda r: httpx.Response(200, content=sse(chunk("x"), chunk(None, finish))), log)
    assert events[-1] == expected and log.verified("openrouter") == {}


def test_stream_ending_without_finish_reason_is_interrupted():
    assert run(lambda r: httpx.Response(200, content=sse(chunk("medio"))))[-1] == ProviderFailed(
        codes.INTERRUPTED, True, DEFAULT_MODEL)
    assert run(lambda r: httpx.Response(200, content=sse(chunk("medio"), done=False)))[-1] == ProviderFailed(
        codes.INTERRUPTED, True, DEFAULT_MODEL)


def test_network_failure_is_provider_unavailable():
    def refuse(request):
        raise httpx.ConnectError("proxy caído")
    assert run(refuse) == [ProviderFailed(codes.PROVIDER_UNAVAILABLE, True)]


def test_closing_the_stream_releases_the_connection():
    class Stalled(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            yield sse(chunk("parcial"), done=False)
            await asyncio.Event().wait()  # El proveedor deja de enviar.

        async def aclose(self):
            self.closed = True

    async def go():
        body = Stalled()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body))) as http:
            stream = OpenRouterProvider(http, KEY).stream_reply(DEFAULT_MODEL, [ProviderMessage("user", "hola")])
            assert await anext(stream) == TextChunk("parcial")
            pending = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0.05)
            pending.cancel()  # Así cancela el caso de uso al pedir cancelar.
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            assert body.closed
    asyncio.run(go())


def catalog(handler, allowed, tmp_path, verified=()):
    log = ModelAccessLog(tmp_path / "verified.json")
    for model in verified:
        log.record_completed("openrouter", model)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await OpenRouterCatalog(http, KEY, allowed, log).list_models()
    return asyncio.run(go())


def test_catalog_shows_only_allowed_models_that_are_offered(tmp_path):
    data = {"data": [{"id": "otro/modelo", "name": "Otro"},
                     {"id": DEFAULT_MODEL, "name": "DeepSeek: DeepSeek V4.1 Flash 1017"}]}
    models = catalog(lambda r: httpx.Response(200, json=data), [DEFAULT_MODEL, "no/existe"], tmp_path,
                     verified=[DEFAULT_MODEL])
    assert models == [ModelOption(DEFAULT_MODEL, "DeepSeek: DeepSeek V4.1 Flash 1017", "verified")]


@pytest.mark.parametrize("handler,code", [
    (lambda r: httpx.Response(401, json={}), codes.AUTH_REQUIRED),
    (lambda r: httpx.Response(429, json={}), codes.RATE_LIMITED),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")), codes.PROVIDER_UNAVAILABLE),
])
def test_catalog_errors(handler, code, tmp_path):
    with pytest.raises(CatalogUnavailable) as error:
        catalog(handler, [DEFAULT_MODEL], tmp_path)
    assert error.value.code == code


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI solo en Windows")
def test_api_key_is_stored_encrypted_and_validated(tmp_path):
    state = OpenRouterState.open(tmp_path)
    with pytest.raises(ApiKeyMissing):
        state.api_key.require()
    with pytest.raises(InvalidApiKey):
        state.api_key.save("no-es-una-key")
    state.api_key.save(f"  {KEY}\n")
    assert state.api_key.require() == KEY
    stored = (tmp_path / "openrouter" / "api-key.dpapi").read_bytes()
    assert KEY.encode() not in stored
    state.api_key.delete()
    assert state.api_key.load() is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI solo en Windows")
def test_cli_set_key_from_stdin_never_prints_it(tmp_path, monkeypatch, capsys):
    import io

    from ai_assistant.infrastructure.tools import openrouter as cli

    monkeypatch.setenv("AI_ASSISTANT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(KEY + "\n"))
    assert cli.main(["set-key"]) == 0
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "guardada" in out and KEY not in out
    assert OpenRouterState.open(tmp_path).api_key.load() == KEY


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI solo en Windows")
def test_server_refuses_to_start_with_openrouter_without_key(tmp_path):
    from ai_assistant.infrastructure.bootstrap.server import app_from_settings
    from ai_assistant.infrastructure.config import load_settings

    settings = load_settings({"AI_ASSISTANT_PROVIDER": "openrouter", "AI_ASSISTANT_DATA_DIR": str(tmp_path)})
    with pytest.raises(ApiKeyMissing):
        app_from_settings(settings)
    OpenRouterState.open(tmp_path).api_key.save(KEY)
    app = app_from_settings(settings)
    assert type(app.state.use_cases.send._provider).__name__ == "OpenRouterProvider"
