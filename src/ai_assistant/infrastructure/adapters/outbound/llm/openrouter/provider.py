"""Implementación de los puertos ChatProvider y ModelCatalog con OpenRouter.

Streaming de Chat Completions: fragmentos `choices[0].delta.content`, comentarios SSE de
mantenimiento (`: OPENROUTER PROCESSING`), un fragmento final de contabilidad con `usage` y
`data: [DONE]`. Un error después de empezar el stream llega con HTTP 200 en un fragmento con `error`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import httpx

from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.outbound.chat_provider import (
    ProviderCompleted,
    ProviderEvent,
    ProviderFailed,
    ProviderMessage,
    TextChunk,
)
from ai_assistant.application.ports.outbound.model_catalog import CatalogUnavailable
from ai_assistant.domain.conversations.model import ModelOption
from ai_assistant.infrastructure.model_access_log import ModelAccessLog
from ai_assistant.infrastructure.sse import sse_events

from .credentials import MODEL_LOG_SCOPE

API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"

# Estado HTTP de OpenRouter -> (código del puerto, se puede reintentar manualmente).
_STATUS = {
    401: (codes.AUTH_REQUIRED, False),  # Key inválida o desactivada.
    402: (codes.INSUFFICIENT_CREDITS, False),
    403: (codes.NOT_ELIGIBLE, False),  # Límite de gasto, key bloqueada o moderación.
    404: (codes.MODEL_UNAVAILABLE, False),  # Modelo desconocido o sin proveedor que lo sirva.
    408: (codes.PROVIDER_UNAVAILABLE, True),
    429: (codes.RATE_LIMITED, True),
}


def _from_status(status: object) -> tuple[str, bool]:
    if isinstance(status, int):
        if status in _STATUS:
            return _STATUS[status]
        if status >= 500:  # 502: falló el modelo (no se cobra); 503: ningún proveedor disponible.
            return codes.PROVIDER_UNAVAILABLE, True
    return codes.PROVIDER_ERROR, False


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


class OpenRouterProvider:
    """Si se pasa model_log, cada respuesta completada marca el modelo pedido como comprobado."""

    name = "openrouter"

    def __init__(self, http: httpx.AsyncClient, api_key: str, model_log: ModelAccessLog | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._model_log = model_log

    async def stream_reply(self, model: str,
                           history: Sequence[ProviderMessage]) -> AsyncIterator[ProviderEvent]:
        body = {"model": model, "stream": True,
                "messages": [{"role": m.role, "content": m.text} for m in history]}
        headers = {**_headers(self._api_key), "Accept": "text/event-stream"}
        reported: str | None = None
        received_text = False
        try:
            async with self._http.stream("POST", f"{API_BASE}/chat/completions", json=body,
                                         headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    yield ProviderFailed(*_from_status(response.status_code))
                    return
                async for _, raw in sse_events(response):
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    reported = chunk.get("model") or reported
                    if isinstance(chunk.get("error"), dict):
                        code, retryable = _from_status(chunk["error"].get("code"))
                        yield ProviderFailed(code, retryable, reported)
                        return
                    choices = chunk.get("choices") or [{}]
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        received_text = True
                        yield TextChunk(text)
                    if finish := choice.get("finish_reason"):
                        # El primer finish_reason decide; el fragmento de usage que lo repite es contabilidad.
                        yield self._finish(finish, model, reported)
                        return
        except httpx.TransportError:
            pass
        # Fin del stream (o corte de red) sin finish_reason: la respuesta no está confirmada.
        yield ProviderFailed(codes.INTERRUPTED if received_text else codes.PROVIDER_UNAVAILABLE, True, reported)

    def _finish(self, finish: str, model: str, reported: str | None) -> ProviderEvent:
        if finish == "stop":
            if self._model_log is not None:
                self._model_log.record_completed(MODEL_LOG_SCOPE, model)
            return ProviderCompleted(reported)
        if finish == "length":
            return ProviderFailed(codes.INCOMPLETE, True, reported)
        return ProviderFailed(codes.PROVIDER_ERROR, False, reported)  # error, content_filter, tool_calls…


class OpenRouterCatalog:
    """Modelos permitidos en la configuración que OpenRouter ofrece, marcados con el acceso
    comprobado. El catálogo completo tiene cientos de modelos; solo se muestran los permitidos."""

    def __init__(self, http: httpx.AsyncClient, api_key: str, allowed: Sequence[str],
                 model_log: ModelAccessLog) -> None:
        self._http = http
        self._api_key = api_key
        self._allowed = list(allowed)
        self._model_log = model_log

    async def list_models(self) -> list[ModelOption]:
        try:
            response = await self._http.get(f"{API_BASE}/models", headers=_headers(self._api_key))
        except httpx.TransportError as error:
            raise CatalogUnavailable(codes.PROVIDER_UNAVAILABLE) from error
        if response.status_code != 200:
            raise CatalogUnavailable(_from_status(response.status_code)[0])
        offered = {
            item["id"]: item.get("name") or item["id"]
            for item in response.json().get("data", []) if isinstance(item, dict) and item.get("id")
        }
        verified = self._model_log.verified(MODEL_LOG_SCOPE)
        return [
            ModelOption(slug, offered[slug], "verified" if slug in verified else "unverified")
            for slug in self._allowed if slug in offered
        ]
