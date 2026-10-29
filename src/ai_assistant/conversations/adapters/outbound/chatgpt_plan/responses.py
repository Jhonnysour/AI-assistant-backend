"""Catálogo de modelos y peticiones en streaming a la Responses API con el token del plan.

Referencias (consultadas el 2026-10-29):
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

import httpx

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


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str | None, str]]:
    """Agrupa líneas Server-Sent Events en (event, data)."""
    event: str | None = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith(":"):
            continue
        else:
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    if data:
        yield event, "\n".join(data)


def interpret_events(sse_events: Iterable[tuple[str | None, str]]) -> Iterator[StreamEvent]:
    """Traduce eventos de Responses. Solo response.completed confirma una respuesta completa."""
    for name, raw in sse_events:
        if raw == "[DONE]":
            break
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        kind = payload.get("type") or name
        if kind == "response.output_text.delta":
            yield TextDelta(payload.get("delta", ""))
        elif kind == "response.completed":
            yield StreamOutcome("completed")
            return
        elif kind == "response.failed":
            error = (payload.get("response") or {}).get("error") or {}
            yield StreamOutcome("failed", error.get("code") or "unknown_error", error.get("message"))
            return
        elif kind == "response.incomplete":
            details = (payload.get("response") or {}).get("incomplete_details") or {}
            yield StreamOutcome("incomplete", details.get("reason"))
            return
        elif kind == "error":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            yield StreamOutcome("failed", error.get("code") or "stream_error", error.get("message"))
            return
    yield StreamOutcome("interrupted", "stream_ended_without_completion")


def request_body(model: str, text: str) -> dict:
    # store=false y stream=true son obligatorios en esta vía; no se envían temperature ni
    # max_output_tokens porque no están admitidos.
    return {
        "model": model,
        "input": [{"role": "user", "content": text}],
        "store": False,
        "stream": True,
    }


def stream_response(http: httpx.Client, access_token: str, model: str, text: str) -> Iterator[StreamEvent]:
    headers = {**_auth_headers(access_token), "Accept": "text/event-stream"}
    try:
        with http.stream("POST", f"{API_BASE}/responses", json=request_body(model, text), headers=headers) as response:
            if response.status_code != 200:
                response.read()
                raise _provider_error(response)
            yield from interpret_events(parse_sse(response.iter_lines()))
    except httpx.TransportError as error:
        yield StreamOutcome("interrupted", type(error).__name__)
