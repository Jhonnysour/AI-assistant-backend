"""Adaptador de entrada HTTP: contrato chat-v1 (docs/api/chat-v1.md) con FastAPI y SSE.

Solo depende de los puertos de entrada: no conoce los servicios que los implementan.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from ai_assistant.application import error_codes as codes
from ai_assistant.application.ports.inbound.conversations import (
    AssistantResponseView,
    CancelResponse,
    ChatEvent,
    ConversationDetail,
    ConversationError,
    ConversationNotFound,
    ConversationSummary,
    DeleteConversation,
    EmptyMessage,
    GetConversation,
    InvalidRequest,
    InvalidTitle,
    ListConversations,
    ListModels,
    MessageAccepted,
    ModelsUnavailable,
    RenameConversation,
    ResponseCancelled,
    ResponseCompleted,
    ResponseDelta,
    ResponseFailed,
    ResponseNotActive,
    ResponseNotFound,
    ResponseStream,
    SendInProgress,
    SendMessage,
    StartConversation,
    StorageUnavailable,
)

_log = logging.getLogger(__name__)

# Errores fuera del stream: código HTTP y mensaje al usuario (los del proveedor llegan con el suyo).
REQUEST_ERRORS: dict[str, tuple[int, str]] = {
    EmptyMessage.code: (400, "El mensaje está vacío."),
    InvalidRequest.code: (400, "La petición no es válida."),
    InvalidTitle.code: (400, "El título debe tener entre 1 y 120 caracteres, sin espacios en los extremos."),
    ConversationNotFound.code: (404, "La conversación no existe. Crea una nueva."),
    ResponseNotFound.code: (404, "La respuesta no existe en esta conversación."),
    SendInProgress.code: (409, "Ya hay una respuesta en curso en esta conversación."),
    ResponseNotActive.code: (409, "La respuesta ya terminó."),
}

# Códigos HTTP de los errores de GET /v1/models; el resto, 502.
CATALOG_STATUS = {
    codes.AUTH_REQUIRED: 401,
    codes.NOT_ELIGIBLE: 403,
    codes.USAGE_LIMIT: 503,
    codes.INSUFFICIENT_CREDITS: 503,
    codes.RATE_LIMITED: 503,
    codes.PROVIDER_UNAVAILABLE: 503,
    codes.INTERNAL_ERROR: 500,
}

INTERNAL_ERROR_MESSAGE = "Error interno del asistente."
STORAGE_UNAVAILABLE_MESSAGE = "No se pudo acceder a las conversaciones guardadas. Inténtalo más tarde."


@dataclass(frozen=True)
class ConversationUseCases:
    """Puertos de entrada que usa este adaptador; la composición decide quién los implementa."""

    start: StartConversation
    send: SendMessage
    cancel: CancelResponse
    list_models: ListModels
    list_conversations: ListConversations
    get_conversation: GetConversation
    rename: RenameConversation
    delete: DeleteConversation


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message

    @classmethod
    def from_request_error(cls, error: ConversationError) -> ApiError:
        status, message = REQUEST_ERRORS[error.code]
        return cls(status, error.code, message)


class SendMessageRequest(BaseModel):
    model: str
    text: str


class RenameRequest(BaseModel):
    title: str | None  # Obligatorio; null vuelve al título derivado del primer mensaje.


def iso(moment: datetime | None) -> str | None:
    """UTC ISO 8601 con Z y milisegundos."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_sse(event: ChatEvent) -> ServerSentEvent:
    if isinstance(event, MessageAccepted):
        return ServerSentEvent(event="message.accepted", data={
            "message_id": event.message_id, "response_id": event.response_id,
            "created_at": iso(event.created_at), "model_requested": event.model_requested,
            "provider": event.provider,
        })
    if isinstance(event, ResponseDelta):
        return ServerSentEvent(event="response.delta", data={"response_id": event.response_id, "text": event.text})
    if isinstance(event, ResponseCompleted):
        return ServerSentEvent(event="response.completed", data={
            "response_id": event.response_id, "finished_at": iso(event.finished_at),
            "model_reported": event.model_reported,
        })
    if isinstance(event, ResponseFailed):
        return ServerSentEvent(event="response.failed", data={
            "response_id": event.response_id, "status": event.status, "finished_at": iso(event.finished_at),
            "model_reported": event.model_reported,
            "error": {"code": event.code, "message": event.message, "retryable": event.retryable},
        })
    if isinstance(event, ResponseCancelled):
        return ServerSentEvent(event="response.cancelled", data={
            "response_id": event.response_id, "finished_at": iso(event.finished_at),
        })
    raise TypeError(f"Evento desconocido: {type(event).__name__}")


def summary_json(summary: ConversationSummary) -> dict:
    return {
        "conversation_id": summary.conversation_id, "title": summary.title,
        "title_is_custom": summary.title_is_custom, "created_at": iso(summary.created_at),
        "updated_at": iso(summary.updated_at),
    }


def message_json(item) -> dict:
    if isinstance(item, AssistantResponseView):
        error = None if item.error_code is None else {"code": item.error_code, "message": item.error_message}
        return {
            "role": "assistant", "response_id": item.response_id, "in_reply_to": item.in_reply_to,
            "text": item.text, "status": item.status, "error": error,
            "model_requested": item.model_requested, "model_reported": item.model_reported,
            "provider": item.provider, "created_at": iso(item.created_at), "finished_at": iso(item.finished_at),
        }
    return {"role": "user", "message_id": item.message_id, "text": item.text, "created_at": iso(item.created_at)}


def detail_json(detail: ConversationDetail) -> dict:
    return {**summary_json(detail.summary), "persisted": detail.persisted,
            "messages": [message_json(item) for item in detail.messages]}


def get_use_cases(request: Request) -> ConversationUseCases:
    return request.app.state.use_cases


async def accepted_stream(conversation_id: str, body: SendMessageRequest,
                          use_cases: ConversationUseCases = Depends(get_use_cases)
                          ) -> AsyncIterator[ResponseStream]:
    """Reserva el envío antes de abrir el stream, para que sus errores sean respuestas HTTP.

    Al terminar la petición se cierra el envío: si el stream no llegó a consumirse, la conversación
    no queda bloqueada (I9).
    """
    try:
        stream = await use_cases.send.send(conversation_id, body.model, body.text)
    except ConversationError as error:
        raise ApiError.from_request_error(error) from error
    try:
        yield stream
    finally:
        stream.close()


router = APIRouter(prefix="/v1")


@router.post("/conversations", status_code=201)
async def create_conversation(use_cases: ConversationUseCases = Depends(get_use_cases)) -> dict:
    started = await use_cases.start.start_conversation()
    return {"conversation_id": started.conversation_id, "created_at": iso(started.created_at),
            "persisted": started.persisted}


@router.get("/conversations")
async def list_conversations(use_cases: ConversationUseCases = Depends(get_use_cases)) -> dict:
    summaries = await use_cases.list_conversations.list_conversations()
    return {"conversations": [summary_json(summary) for summary in summaries]}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, use_cases: ConversationUseCases = Depends(get_use_cases)) -> dict:
    try:
        return detail_json(await use_cases.get_conversation.get_conversation(conversation_id))
    except ConversationError as error:
        raise ApiError.from_request_error(error) from error


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, body: RenameRequest,
                              use_cases: ConversationUseCases = Depends(get_use_cases)) -> dict:
    try:
        return summary_json(await use_cases.rename.rename_conversation(conversation_id, body.title))
    except ConversationError as error:
        raise ApiError.from_request_error(error) from error


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str,
                              use_cases: ConversationUseCases = Depends(get_use_cases)) -> Response:
    try:
        await use_cases.delete.delete_conversation(conversation_id)
    except ConversationError as error:
        raise ApiError.from_request_error(error) from error
    return Response(status_code=204)


@router.get("/models")
async def list_models(use_cases: ConversationUseCases = Depends(get_use_cases)) -> dict:
    try:
        models = await use_cases.list_models.list_models()
    except ModelsUnavailable as error:
        raise ApiError(CATALOG_STATUS.get(error.code, 502), error.code, error.message) from error
    return {"models": [{"id": m.id, "display_name": m.display_name, "access": m.access} for m in models]}


@router.post("/conversations/{conversation_id}/messages", response_class=EventSourceResponse)
async def send_message(stream: ResponseStream = Depends(accepted_stream)) -> AsyncIterator[ServerSentEvent]:
    async for event in stream.events():
        yield to_sse(event)


@router.post("/conversations/{conversation_id}/responses/{response_id}/cancel", status_code=204)
async def cancel_response(conversation_id: str, response_id: str,
                          use_cases: ConversationUseCases = Depends(get_use_cases)) -> Response:
    try:
        await use_cases.cancel.cancel(conversation_id, response_id)
    except ConversationError as error:
        raise ApiError.from_request_error(error) from error
    return Response(status_code=204)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def install(app: FastAPI, use_cases: ConversationUseCases) -> None:
    """Registra los casos de uso, el router y el formato de error del contrato."""
    app.state.use_cases = use_cases
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(_error_body(error.code, error.message), status_code=error.status)

    @app.exception_handler(StorageUnavailable)
    async def storage_unavailable(request: Request, error: StorageUnavailable) -> JSONResponse:
        _log.error("Almacenamiento no disponible en %s %s (%s).", request.method, request.url.path, error)
        return JSONResponse(_error_body(StorageUnavailable.code, STORAGE_UNAVAILABLE_MESSAGE), status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Sin detalles de validación: podrían repetir el texto enviado.
        status, message = REQUEST_ERRORS[InvalidRequest.code]
        return JSONResponse(_error_body(InvalidRequest.code, message), status_code=status)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        # Solo el tipo y las líneas de código, como en el caso de uso.
        _log.error("Fallo interno en %s %s (%s):\n%s", request.method, request.url.path,
                   type(error).__name__, "".join(traceback.format_tb(error.__traceback__)))
        return JSONResponse(_error_body(codes.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE), status_code=500)
