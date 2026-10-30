"""Caso de uso: al arrancar, cerrar las respuestas que quedaron en curso por una caída (Q4 de SPEC-002).

Supone un único proceso del backend: cualquier respuesta guardada en streaming al arrancar ya no
tiene a nadie consumiendo el proveedor.
"""

from __future__ import annotations

from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.outbound.clock import Clock
from ai_assistant.application.ports.outbound.conversation_repository import ConversationRepository


class RecoverInterruptedResponses:
    def __init__(self, conversations: ConversationRepository, clock: Clock) -> None:
        self._conversations = conversations
        self._now = clock

    async def run(self) -> int:
        """Devuelve cuántas respuestas se cerraron como interrumpidas (o fallidas, si no tenían texto)."""
        recovered = 0
        for conversation in await self._conversations.with_active_response():
            response_id = conversation.active_response_id
            conversation.fail(response_id, codes.INTERRUPTED, self._now())
            await self._conversations.save_response(conversation.id, conversation.response(response_id))
            recovered += 1
        return recovered
