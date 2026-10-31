import asyncio
import json

import httpx
import pytest

from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan import responses
from ai_assistant.infrastructure.sse import parse_sse
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.responses import (
    StreamOutcome,
    TextDelta,
)

TOKEN = "secret-access-token"


def sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def delta(text):
    return {"type": "response.output_text.delta", "delta": text}


def run(body: bytes, status: int = 200, seen: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["request"] = request
        headers = {"content-type": "text/event-stream"} if status == 200 else {}
        return httpx.Response(status, content=body, headers=headers)

    return stream(httpx.MockTransport(handler), "model-a", [{"role": "user", "content": "Hola"}])


def stream(transport, model, items):
    async def go():
        async with httpx.AsyncClient(transport=transport) as http:
            return [event async for event in responses.stream_response(http, TOKEN, model, items)]
    return asyncio.run(go())


def test_parse_sse_groups_lines_and_ignores_comments():
    lines = [": keep-alive", "event: a", "data: {\"x\": 1}", "", "data: line1", "data: line2", ""]
    assert list(parse_sse(lines)) == [("a", '{"x": 1}'), (None, "line1\nline2")]


def test_deltas_then_completed():
    events = run(sse(delta("Hola, "), delta("mundo"), {"type": "response.completed", "response": {}}))
    assert events == [TextDelta("Hola, "), TextDelta("mundo"), StreamOutcome("completed")]
    assert events[-1].completed


def test_failed_carries_error_code():
    failed = {"type": "response.failed", "response": {"error": {
        "code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}
    events = run(sse(delta("parcial"), failed))
    assert events[-1] == StreamOutcome("failed", "subscription_sharing_usage_limit_exceeded", "limit")
    assert not events[-1].completed


def test_incomplete_is_not_completed():
    incomplete = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}
    events = run(sse(delta("a"), incomplete))
    assert events[-1] == StreamOutcome("incomplete", "max_output_tokens")


def test_stream_cut_without_terminal_event_is_interrupted():
    events = run(sse(delta("medio")))
    assert events == [TextDelta("medio"), StreamOutcome("interrupted", "stream_ended_without_completion")]


def test_transport_failure_mid_stream_is_interrupted():
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse({"type": "response.created", "response": {"model": "m-1"}}, delta("parcial"))
            raise httpx.ReadError("conexión cortada")

    def handler(request):
        return httpx.Response(200, stream=Broken(), headers={"content-type": "text/event-stream"})

    events = stream(httpx.MockTransport(handler), "m", [{"role": "user", "content": "hola"}])
    assert events == [TextDelta("parcial"), StreamOutcome("interrupted", "ReadError", model="m-1")]


def test_reported_model_is_attached_to_the_outcome():
    events = run(sse({"type": "response.created", "response": {"model": "gpt-6-luna"}}, delta("ok"),
                     {"type": "response.completed", "response": {"model": "gpt-6-luna"}}))
    assert events == [TextDelta("ok"), StreamOutcome("completed", model="gpt-6-luna")]
    assert run(sse(delta("ok"), {"type": "response.completed"}))[-1].model is None


def test_interpret_events_stops_at_done_marker():
    events = list(responses.interpret_events([(None, json.dumps(delta("a"))), (None, "[DONE]"),
                                              (None, json.dumps(delta("b")))]))
    assert events == [TextDelta("a"), StreamOutcome("interrupted", "stream_ended_without_completion")]


def test_request_follows_plan_route_requirements():
    seen = {}
    run(sse({"type": "response.completed"}), seen=seen)
    request = seen["request"]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert body == {"model": "model-a", "input": [{"role": "user", "content": "Hola"}],
                    "store": False, "stream": True}


@pytest.mark.parametrize("payload,code,detail", [
    ({"error": {"code": "subscription_sharing_user_not_eligible", "message": "no"}},
     "subscription_sharing_user_not_eligible", "no"),
    ({"detail": "region not allowed"}, None, "region not allowed"),
])
def test_admission_errors_raise_without_leaking_token(payload, code, detail):
    with pytest.raises(responses.ProviderError) as error:
        run(json.dumps(payload).encode(), status=403)
    assert (error.value.status, error.value.code, error.value.detail) == (403, code, detail)
    assert TOKEN not in str(error.value)


def test_catalog_keeps_listed_models_in_server_order():
    payload = {"models": [
        {"slug": "b", "display_name": "Modelo B", "visibility": "list"},
        {"slug": "hidden", "display_name": "Oculto", "visibility": "hide"},
        {"slug": "a", "display_name": "Modelo A", "visibility": "list"},
    ]}
    assert responses.parse_catalog(payload) == [
        responses.CatalogModel("b", "Modelo B"), responses.CatalogModel("a", "Modelo A"),
    ]
