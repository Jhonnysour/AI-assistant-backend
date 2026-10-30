"""Implementación del puerto ChatProvider con el plan de ChatGPT."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import httpx

from ai_assistant.conversations.application import ports

from . import responses
from .credentials import ModelAccessLog
from .session import ChatGptPlanSession, SessionUnavailable

# Código de OpenAI -> (código del puerto, se puede reintentar manualmente).
_ERROR_CODES = {
    "subscription_sharing_user_not_eligible": (ports.NOT_ELIGIBLE, False),
    "chatpass_v2_scope_not_authorized": (ports.NOT_ELIGIBLE, False),
    "chatpass_v2_invalid_authorization_context": (ports.NOT_ELIGIBLE, False),
    "subscription_sharing_usage_limit_exceeded": (ports.USAGE_LIMIT, False),
    "subscription_sharing_usage_unavailable": (ports.PROVIDER_UNAVAILABLE, True),
    "subscription_sharing_user_unavailable": (ports.PROVIDER_UNAVAILABLE, True),
    "subscription_sharing_invalid_user": (ports.AUTH_REQUIRED, False),
    "subscription_sharing_unsupported_capability": (ports.MODEL_UNAVAILABLE, False),
    "model_not_found": (ports.MODEL_UNAVAILABLE, False),
}


def _from_status(status: int) -> tuple[str, bool]:
    if status == 401:
        return ports.AUTH_REQUIRED, False
    if status == 429:
        return ports.USAGE_LIMIT, False
    if status >= 500:
        return ports.PROVIDER_UNAVAILABLE, True
    return ports.PROVIDER_ERROR, False


class ChatGptPlanProvider:
    """El streaming usa el cliente asíncrono. La renovación de tokens reutiliza el flujo OAuth
    síncrono (también lo usa la CLI) en un hilo aparte; ocurre como mucho una vez por hora.

    Si se pasa model_log, cada respuesta completada marca el modelo pedido como comprobado.
    """

    def __init__(self, http: httpx.AsyncClient, session: ChatGptPlanSession,
                 refresh_http: httpx.Client, model_log: ModelAccessLog | None = None) -> None:
        self._http = http
        self._session = session
        self._refresh_http = refresh_http
        self._model_log = model_log

    async def stream_reply(self, model: str,
                           history: Sequence[ports.ProviderMessage]) -> AsyncIterator[ports.ProviderEvent]:
        try:
            record = await asyncio.to_thread(self._session.current, self._refresh_http)
        except SessionUnavailable:
            yield ports.ProviderFailed(ports.AUTH_REQUIRED, False)
            return
        except httpx.TransportError:
            yield ports.ProviderFailed(ports.PROVIDER_UNAVAILABLE, True)
            return
        items = [{"role": m.role, "content": m.text} for m in history]
        received_text = False
        stream = responses.stream_response(self._http, record.access_token, model, items)
        try:
            async for event in stream:
                if isinstance(event, responses.TextDelta):
                    received_text = received_text or bool(event.text)
                    yield ports.TextChunk(event.text)
                elif event.completed:
                    if self._model_log is not None:
                        self._model_log.record_completed(record.client_id, model)
                    yield ports.ProviderCompleted(event.model)
                elif event.status == "failed":
                    code, retryable = _ERROR_CODES.get(event.code, (ports.PROVIDER_ERROR, False))
                    yield ports.ProviderFailed(code, retryable, event.model)
                elif event.status == "incomplete":
                    yield ports.ProviderFailed(ports.INCOMPLETE, True, event.model)
                elif received_text:
                    yield ports.ProviderFailed(ports.INTERRUPTED, True, event.model)
                else:  # Sin texto: no se llegó a establecer el flujo (red, proxy).
                    yield ports.ProviderFailed(ports.PROVIDER_UNAVAILABLE, True, event.model)
        except responses.ProviderError as error:
            yield ports.ProviderFailed(*_ERROR_CODES.get(error.code, _from_status(error.status)))
        finally:
            await stream.aclose()


class ChatGptPlanCatalog:
    """Catálogo ofrecido por el proveedor, marcado con el acceso comprobado de este registro."""

    def __init__(self, http: httpx.AsyncClient, session: ChatGptPlanSession,
                 refresh_http: httpx.Client, model_log: ModelAccessLog) -> None:
        self._http = http
        self._session = session
        self._refresh_http = refresh_http
        self._model_log = model_log

    async def list_models(self) -> list[ports.ModelOption]:
        try:
            record = await asyncio.to_thread(self._session.current, self._refresh_http)
            catalog = await responses.alist_models(self._http, record.access_token)
        except SessionUnavailable as error:
            raise ports.CatalogUnavailable(ports.AUTH_REQUIRED) from error
        except httpx.TransportError as error:
            raise ports.CatalogUnavailable(ports.PROVIDER_UNAVAILABLE) from error
        except responses.ProviderError as error:
            code, _ = _ERROR_CODES.get(error.code, _from_status(error.status))
            raise ports.CatalogUnavailable(code) from error
        verified = self._model_log.verified(record.client_id)
        return [
            ports.ModelOption(m.slug, m.display_name, "verified" if m.slug in verified else "unverified")
            for m in catalog
        ]
