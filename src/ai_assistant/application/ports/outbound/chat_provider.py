"""Puerto de salida: proveedor de LLM que genera la respuesta en streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ProviderMessage:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class TextChunk:
    text: str


@dataclass(frozen=True)
class ProviderCompleted:
    model_reported: str | None = None  # Modelo que el proveedor declara haber usado.


@dataclass(frozen=True)
class ProviderFailed:
    code: str  # Uno de application.error_codes.
    retryable: bool
    model_reported: str | None = None


ProviderEvent = TextChunk | ProviderCompleted | ProviderFailed


class ChatProvider(Protocol):
    name: str  # Identifica el proveedor en cada respuesta guardada; estable entre versiones.

    def stream_reply(self, model: str, history: Sequence[ProviderMessage]) -> AsyncIterator[ProviderEvent]:
        """Genera fragmentos y termina con ProviderCompleted o ProviderFailed.

        Los fallos del proveedor (red, proxy, sesión, límites) se emiten como ProviderFailed; una
        excepción se trata como fallo interno. Si el flujo termina sin evento final, el caso de uso
        lo trata como interrumpido. Cancelar la tarea que lo consume o cerrarlo (aclose) debe
        liberar la conexión con el proveedor.
        """
        ...
