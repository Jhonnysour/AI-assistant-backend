"""Proveedor simulado para pruebas y para desarrollar clientes sin consumir el plan."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from ai_assistant.application.ports.outbound.chat_provider import (
    ProviderCompleted,
    ProviderEvent,
    ProviderMessage,
    TextChunk,
)
from ai_assistant.domain.conversations.model import ModelOption

ScriptItem = ProviderEvent | Exception | asyncio.Event


class FakeChatProvider:
    """Reproduce un guion de eventos. Al llegar a una excepción la lanza; a un asyncio.Event,
    espera a que se active (simula un proveedor que tarda en enviar el siguiente fragmento).

    Sin guion, responde en fragmentos repitiendo el último mensaje del usuario.
    """

    name = "fake"

    def __init__(self, script: Sequence[ScriptItem] | None = None, delay_seconds: float = 0.0) -> None:
        self.script = script
        self._delay = delay_seconds
        self.calls: list[tuple[str, list[ProviderMessage]]] = []
        self.closed_streams = 0

    def _default(self, history: Sequence[ProviderMessage]) -> list[ProviderEvent]:
        words = f"Respuesta simulada a: {history[-1].text}".split(" ")
        return [TextChunk(w + " ") for w in words[:-1]] + [TextChunk(words[-1]), ProviderCompleted()]

    def stream_reply(self, model: str, history: Sequence[ProviderMessage]) -> AsyncIterator[ProviderEvent]:
        self.calls.append((model, list(history)))
        script = self.script if self.script is not None else self._default(history)
        return self._play(script)

    async def _play(self, script: Sequence[ScriptItem]) -> AsyncIterator[ProviderEvent]:
        try:
            for item in script:
                if self._delay:
                    await asyncio.sleep(self._delay)
                if isinstance(item, asyncio.Event):
                    await item.wait()
                elif isinstance(item, Exception):
                    raise item
                else:
                    yield item
        finally:
            self.closed_streams += 1


class FakeModelCatalog:
    """Catálogo del proveedor simulado: un único modelo, siempre disponible."""

    MODEL = ModelOption("fake-model", "Modelo simulado", "verified")

    async def list_models(self) -> list[ModelOption]:
        return [self.MODEL]
