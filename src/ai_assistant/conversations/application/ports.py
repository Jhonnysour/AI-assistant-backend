"""Puertos que necesita el caso de uso de conversación."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ai_assistant.conversations.domain.model import Conversation

# Códigos de error del proveedor, independientes de OpenAI. Los adaptadores traducen los suyos.
AUTH_REQUIRED = "auth_required"
NOT_ELIGIBLE = "not_eligible"
USAGE_LIMIT = "usage_limit"
MODEL_UNAVAILABLE = "model_unavailable"
PROVIDER_UNAVAILABLE = "provider_unavailable"
INCOMPLETE = "incomplete"
INTERRUPTED = "interrupted"
PROVIDER_ERROR = "provider_error"
INTERNAL_ERROR = "internal_error"  # Fallo del propio backend, no del proveedor.


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
    code: str
    retryable: bool
    model_reported: str | None = None


ProviderEvent = TextChunk | ProviderCompleted | ProviderFailed


class ChatProvider(Protocol):
    def stream_reply(self, model: str, history: Sequence[ProviderMessage]) -> AsyncIterator[ProviderEvent]:
        """Genera fragmentos y termina con ProviderCompleted o ProviderFailed.

        Los fallos del proveedor (red, proxy, sesión, límites) se emiten como ProviderFailed; una
        excepción se trata como fallo interno. Si el flujo termina sin evento final, el caso de uso
        lo trata como interrumpido. Cancelar la tarea que lo consume o cerrarlo (aclose) debe
        liberar la conexión con el proveedor.
        """
        ...


class ConversationRepository(Protocol):
    def add(self, conversation: Conversation) -> None: ...

    def get(self, conversation_id: str) -> Conversation | None: ...

    def save(self, conversation: Conversation) -> None: ...
