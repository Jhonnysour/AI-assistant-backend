"""Caso de uso: enviar un mensaje y recibir la respuesta como flujo de eventos (asyncio).

El proveedor se consume en una tarea propia del turno. Así, cancelar (ChatService.cancel) marca la
respuesta como cancelada en el acto, interrumpe la espera de red, cierra la conexión con el
proveedor y descarta los fragmentos tardíos. Si el consumidor abandona el flujo sin cancelar (p. ej.
se corta la conexión del cliente), la respuesta termina como interrumpida. No hay reintentos
automáticos.

El servicio se usa desde un único bucle asyncio: comprobar y reservar un envío no tiene ningún
await en medio, por lo que es atómico sin locks. Con persistencia, esa garantía pasará al
repositorio.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import AsyncIterator

from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.inbound.conversations import (
    ChatEvent,
    ConversationNotFound,
    ConversationStarted,
    InvalidRequest,
    MessageAccepted,
    ResponseCancelled,
    ResponseCompleted,
    ResponseDelta,
    ResponseFailed,
)
from ai_assistant.application.ports.outbound.chat_provider import (
    ChatProvider,
    ProviderCompleted,
    ProviderFailed,
    ProviderMessage,
    TextChunk,
)
from ai_assistant.application.ports.outbound.clock import Clock
from ai_assistant.application.ports.outbound.conversation_repository import ConversationRepository
from ai_assistant.application.use_cases.conversations.messages import user_message
from ai_assistant.domain.conversations.model import (
    AssistantResponse,
    Conversation,
    ResponseNotActive,
    UserMessage,
)

_log = logging.getLogger(__name__)

# Tiempo máximo que cancel() espera a que se cierre la conexión con el proveedor. La respuesta ya
# está cancelada y la conversación liberada antes de esperar.
CANCEL_CLEANUP_SECONDS = 5.0

FinalEvent = ResponseCompleted | ResponseFailed | ResponseCancelled


def _provider_message(item: UserMessage | AssistantResponse) -> ProviderMessage:
    return ProviderMessage("user" if isinstance(item, UserMessage) else "assistant", item.text)


class Turn:
    """Un envío aceptado (implementa ResponseStream). Hay que consumir events() o llamar a close()
    para liberar la conversación."""

    def __init__(self, service: ChatService, conversation: Conversation, user: UserMessage,
                 reply: AssistantResponse, history: list[ProviderMessage]) -> None:
        self._service = service
        self._conversation = conversation
        self._reply = reply
        self.message_id = user.id
        self.response_id = reply.id
        self._history = history
        self._queue: asyncio.Queue[FinalEvent | ResponseDelta] = asyncio.Queue()
        self._pump: asyncio.Task | None = None
        self._started = False

    @property
    def active(self) -> bool:
        return self._conversation.is_active(self.response_id)

    async def events(self) -> AsyncIterator[ChatEvent]:
        """MessageAccepted, fragmentos y exactamente un evento final."""
        if self._started:
            raise RuntimeError("El flujo de eventos de un turno solo se consume una vez.")
        self._started = True
        try:
            yield MessageAccepted(self.message_id, self.response_id, self._reply.created_at,
                                  self._reply.model_requested)
            if self.active:
                self._pump = asyncio.create_task(self._consume_provider())
                self._service._tasks.add(self._pump)  # asyncio solo guarda referencias débiles.
                self._pump.add_done_callback(self._service._tasks.discard)
            elif self._queue.empty():  # Terminó antes de empezar (p. ej. abandonado con close()).
                return
            while True:
                event = await self._queue.get()
                yield event
                if not isinstance(event, ResponseDelta):
                    return
        finally:
            self.close()

    def close(self) -> None:
        """El consumidor abandona el turno. Si seguía en curso, queda interrumpido (no cancelado)."""
        if self.active:
            self._fail(codes.INTERRUPTED, True)
        self._stop_provider()
        self._service._turns.pop(self.response_id, None)

    def cancel(self) -> asyncio.Task | None:
        """Cancelación pedida por el usuario. Devuelve la tarea del proveedor que se está cerrando."""
        at = self._service._now()
        self._conversation.cancel(self.response_id, at)
        self._service._conversations.save(self._conversation)
        self._queue.put_nowait(ResponseCancelled(self.response_id, at))
        return self._stop_provider()

    def _stop_provider(self) -> asyncio.Task | None:
        if self._pump is None or self._pump.done():
            return None
        self._pump.cancel()
        return self._pump

    def _fail(self, code: str, retryable: bool, model_reported: str | None = None) -> ResponseFailed:
        at = self._service._now()
        status = self._conversation.fail(self.response_id, code, at, model_reported)
        self._service._conversations.save(self._conversation)
        return ResponseFailed(self.response_id, status.value, code, user_message(code), retryable, at,
                              model_reported)

    async def _consume_provider(self) -> None:
        stream = self._service._provider.stream_reply(self._reply.model_requested, self._history)
        try:
            async for event in stream:
                if not self.active:  # Cancelada o abandonada: lo que llegue después se descarta.
                    return
                if isinstance(event, TextChunk):
                    if event.text:
                        self._conversation.append_text(self.response_id, event.text)
                        self._service._conversations.save(self._conversation)
                        self._queue.put_nowait(ResponseDelta(self.response_id, event.text))
                elif isinstance(event, ProviderCompleted):
                    at = self._service._now()
                    self._conversation.complete(self.response_id, at, event.model_reported)
                    self._service._conversations.save(self._conversation)
                    self._queue.put_nowait(ResponseCompleted(self.response_id, at, event.model_reported))
                    return
                elif isinstance(event, ProviderFailed):
                    self._queue.put_nowait(self._fail(event.code, event.retryable, event.model_reported))
                    return
            if self.active:
                self._queue.put_nowait(self._fail(codes.INTERRUPTED, True))
        except Exception as error:
            # Solo el tipo y las líneas de código: el mensaje podría contener datos del usuario.
            _log.error("Fallo interno en una respuesta (%s):\n%s", type(error).__name__,
                       "".join(traceback.format_tb(error.__traceback__)))
            if self.active:
                self._queue.put_nowait(self._fail(codes.INTERNAL_ERROR, False))
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()


class ChatService:
    """Implementa StartConversation, SendMessage y CancelResponse."""

    def __init__(self, conversations: ConversationRepository, provider: ChatProvider, clock: Clock) -> None:
        self._conversations = conversations
        self._provider = provider
        self._now = clock
        self._turns: dict[str, Turn] = {}
        self._tasks: set[asyncio.Task] = set()

    def start_conversation(self) -> ConversationStarted:
        conversation = Conversation.start(self._now())
        self._conversations.add(conversation)
        return ConversationStarted(conversation.id, conversation.created_at, persisted=False)

    def _get(self, conversation_id: str) -> Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound("La conversación no existe.")
        return conversation

    def send(self, conversation_id: str, model: str, text: str) -> Turn:
        """Valida y reserva el envío. Lanza EmptyMessage, SendInProgress, ConversationNotFound
        o InvalidRequest sin llamar al proveedor."""
        if not model or not model.strip():
            raise InvalidRequest("Falta el modelo.")
        conversation = self._get(conversation_id)
        user, reply = conversation.begin_turn(text, model.strip(), self._now())
        history = [_provider_message(item) for item in conversation.context_for(reply.id)]
        self._conversations.save(conversation)
        turn = Turn(self, conversation, user, reply, history)
        self._turns[reply.id] = turn
        return turn

    async def cancel(self, conversation_id: str, response_id: str) -> None:
        """Cancela una respuesta en curso. Lanza ConversationNotFound, ResponseNotFound o
        ResponseNotActive (ya terminó)."""
        conversation = self._get(conversation_id)
        conversation.response(response_id)
        turn = self._turns.get(response_id)
        if turn is None or not turn.active:
            raise ResponseNotActive("La respuesta ya terminó.")
        closing = turn.cancel()
        if closing is not None:
            await asyncio.wait({closing}, timeout=CANCEL_CLEANUP_SECONDS)
