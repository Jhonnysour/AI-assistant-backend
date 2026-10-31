"""Catálogo de modelos y peticiones en streaming a la Responses API con el token del plan.

Referencias (consultadas el 2026-10-29):
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

import httpx

from ai_assistant.infrastructure.sse import sse_events

API_BASE = "https://api.openai.com/v1"


class ProviderError(RuntimeError):
    """Error antes de abrir el stream. El mensaje no incluye cabeceras ni tokens."""

    def __init__(self, status: int, code: str | None, detail: str | None = None) -> None:
        super().__init__(f"El proveedor respondió {status} ({code or 'sin código'}).")
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CatalogModel:
    slug: str
    display_name: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class StreamOutcome:
    status: Literal["completed", "failed", "incomplete", "interrupted"]
    code: str | None = None
    message: str | None = None
    model: str | None = None  # response.model informado por el proveedor, si llegó.

    @property
    def completed(self) -> bool:
        return self.status == "completed"


StreamEvent = TextDelta | StreamOutcome


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _provider_error(response: httpx.Response) -> ProviderError:
    code = detail = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code, detail = error.get("code"), error.get("message")
        elif isinstance(payload.get("detail"), str):
            detail = payload["detail"]  # Admisión directa: texto diagnóstico, no código estable.
    return ProviderError(response.status_code, code, detail)


def parse_catalog(payload: dict) -> list[CatalogModel]:
    """Modelos ofrecidos para mostrar (visibility == "list"), en el orden del servidor.

    Es un catálogo, no una comprobación de acceso.
    """
    return [
        CatalogModel(item["slug"], item.get("display_name") or item["slug"])
        for item in payload.get("models", [])
        if item.get("visibility") == "list" and item.get("slug")
    ]


def list_models(http: httpx.Client, access_token: str) -> list[CatalogModel]:
    response = http.get(f"{API_BASE}/models", headers=_auth_headers(access_token))
    if response.status_code != 200:
        raise _provider_error(response)
    return parse_catalog(response.json())


async def alist_models(http: httpx.AsyncClient, access_token: str) -> list[CatalogModel]:
    response = await http.get(f"{API_BASE}/models", headers=_auth_headers(access_token))
    if response.status_code != 200:
        raise _provider_error(response)
    return parse_catalog(response.json())


class EventInterpreter:
    """Traduce eventos de Responses. Solo response.completed confirma una respuesta completa.

    Recuerda el último response.model informado y lo adjunta al resultado final.
    """

    def __init__(self) -> None:
        self.model: str | None = None
        self.finished = False

    def feed(self, name: str | None, raw: str) -> StreamEvent | None:
        if raw == "[DONE]":
            return self.end()
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        kind = payload.get("type") or name
        if kind == "response.output_text.delta":
            return TextDelta(payload.get("delta", ""))
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        if isinstance(response.get("model"), str):
            self.model = response["model"]
        if kind == "response.completed":
            return self._final("completed")
        if kind == "response.failed":
            error = response.get("error") or {}
            return self._final("failed", error.get("code") or "unknown_error", error.get("message"))
        if kind == "response.incomplete":
            details = response.get("incomplete_details") or {}
            return self._final("incomplete", details.get("reason"))
        if kind == "error":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            return self._final("failed", error.get("code") or "stream_error", error.get("message"))
        return None

    def end(self, code: str = "stream_ended_without_completion") -> StreamOutcome:
        """Resultado si el flujo termina sin evento final."""
        self.finished = True
        return StreamOutcome("interrupted", code, model=self.model)

    def _final(self, status, code=None, message=None) -> StreamOutcome:
        self.finished = True
        return StreamOutcome(status, code, message, self.model)


def interpret_events(sse_events: Iterable[tuple[str | None, str]]) -> Iterator[StreamEvent]:
    interpreter = EventInterpreter()
    for name, raw in sse_events:
        if (event := interpreter.feed(name, raw)) is not None:
            yield event
        if interpreter.finished:
            return
    yield interpreter.end()


def request_body(model: str, input_items: list[dict]) -> dict:
    # store=false y stream=true son obligatorios en esta vía; no se envían temperature ni
    # max_output_tokens porque no están admitidos. Sin store, el historial va completo en input.
    return {
        "model": model,
        "input": input_items,
        "store": False,
        "stream": True,
    }


async def stream_response(
    http: httpx.AsyncClient, access_token: str, model: str, input_items: list[dict],
) -> AsyncIterator[StreamEvent]:
    """Cerrar el iterador (aclose o cancelar la tarea) cierra la conexión con el proveedor."""
    headers = {**_auth_headers(access_token), "Accept": "text/event-stream"}
    body = request_body(model, input_items)
    interpreter = EventInterpreter()
    try:
        async with http.stream("POST", f"{API_BASE}/responses", json=body, headers=headers) as response:
            if response.status_code != 200:
                await response.aread()
                raise _provider_error(response)
            async for name, raw in sse_events(response):
                if (event := interpreter.feed(name, raw)) is not None:
                    yield event
                if interpreter.finished:
                    return
            yield interpreter.end()
    except httpx.TransportError as error:
        yield interpreter.end(type(error).__name__)
