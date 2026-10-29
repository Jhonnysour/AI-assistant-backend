import json
import sys
import time

import httpx
import pytest

from ai_assistant import probe
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.credentials import (
    CredentialRecord,
    ModelAccessLog,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="usa el almacén DPAPI")

TOKEN = "access-token-value"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ASSISTANT_DATA_DIR", str(tmp_path))
    context = probe.Context(probe.load_settings())
    context.credentials.save(CredentialRecord(
        issuer="https://auth.openai.com", subject="user-123", client_id="oaiapp_client_0001",
        ext_agent_host_id="urn:uuid:1", id_token="id", access_token=TOKEN,
        refresh_token="refresh-token-value", token_type="Bearer",
        expires_at=time.time() + 3600, scopes=["chatgpt.tokens.use.direct"],
    ))
    return context


def use_provider(monkeypatch, ctx, handler, calls):
    def counting(request):
        calls.append(request)
        return handler(request)
    monkeypatch.setattr(ctx, "http", lambda: httpx.Client(transport=httpx.MockTransport(counting)))
    monkeypatch.setattr(ctx, "async_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(counting)))
    monkeypatch.setattr(probe, "Context", lambda settings: ctx)


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def test_empty_message_does_not_call_provider(ctx, monkeypatch, capsys):
    calls = []
    use_provider(monkeypatch, ctx, lambda r: httpx.Response(500), calls)
    assert probe.main(["ask", "--model", "m", "   "]) == probe.EXIT_ACTION_NEEDED
    assert calls == []


def test_completed_marks_model_as_verified(ctx, monkeypatch, capsys):
    body = sse({"type": "response.output_text.delta", "delta": "Hola, mundo"}, {"type": "response.completed"})
    calls = []
    use_provider(monkeypatch, ctx, lambda r: httpx.Response(200, content=body), calls)
    assert probe.main(["ask", "--model", "model-a", "Di hola"]) == probe.EXIT_OK
    out = capsys.readouterr()
    assert "Hola, mundo" in out.out and "Estado: completed" in out.out
    assert TOKEN not in out.out + out.err
    assert set(ModelAccessLog(ctx.model_log.path).verified("oaiapp_client_0001")) == {"model-a"}


def test_partial_failure_is_not_verified_and_is_marked_partial(ctx, monkeypatch, capsys):
    body = sse({"type": "response.output_text.delta", "delta": "medio"})
    calls = []
    use_provider(monkeypatch, ctx, lambda r: httpx.Response(200, content=body), calls)
    assert probe.main(["ask", "--model", "model-a", "Di hola"]) == probe.EXIT_PROVIDER
    out = capsys.readouterr()
    assert "parcial" in out.err
    assert ModelAccessLog(ctx.model_log.path).verified("oaiapp_client_0001") == {}


def test_models_distinguishes_offered_from_verified(ctx, monkeypatch, capsys):
    ctx.model_log.record_completed("oaiapp_client_0001", "model-a")
    catalog = {"models": [
        {"slug": "model-a", "display_name": "A", "visibility": "list"},
        {"slug": "model-b", "display_name": "B", "visibility": "list"},
    ]}
    calls = []
    use_provider(monkeypatch, ctx, lambda r: httpx.Response(200, json=catalog), calls)
    assert probe.main(["models"]) == probe.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert "acceso comprobado" in next(line for line in lines if "model-a" in line)
    assert "sin comprobar" in next(line for line in lines if "model-b" in line)


def test_not_eligible_is_explained_and_not_retried(ctx, monkeypatch, capsys):
    error = {"error": {"code": "subscription_sharing_user_not_eligible", "message": "x"}}
    calls = []
    use_provider(monkeypatch, ctx, lambda r: httpx.Response(403, json=error), calls)
    assert probe.main(["ask", "--model", "m", "hola"]) == probe.EXIT_PROVIDER
    assert len(calls) == 1
    assert "No se reintenta" in capsys.readouterr().err
