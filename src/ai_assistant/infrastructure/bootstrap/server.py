"""Raíz de composición del servidor HTTP (contrato chat-v1).

Uso: python -m ai_assistant.infrastructure.bootstrap.server
Escucha solo en 127.0.0.1: la API no tiene autenticación todavía y no debe exponerse.
AI_ASSISTANT_PROVIDER elige el proveedor: "fake" (por defecto, no consume el plan) o "chatgpt_plan".

Es el único módulo que conoce a la vez los casos de uso y los adaptadores concretos.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import uvicorn
from fastapi import FastAPI

from ai_assistant.application.ports.outbound.chat_provider import ChatProvider
from ai_assistant.application.ports.outbound.clock import Clock
from ai_assistant.application.ports.outbound.conversation_repository import ConversationRepository
from ai_assistant.application.ports.outbound.model_catalog import ModelCatalog
from ai_assistant.application.use_cases.conversations.chat import ChatService
from ai_assistant.application.use_cases.conversations.list_models import ListModelsService
from ai_assistant.infrastructure.adapters.inbound.http import chat_v1
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.credentials import LocalState
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.provider import (
    ChatGptPlanCatalog,
    ChatGptPlanProvider,
)
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.session import ChatGptPlanSession
from ai_assistant.infrastructure.adapters.outbound.llm.fake import FakeChatProvider, FakeModelCatalog
from ai_assistant.infrastructure.adapters.outbound.persistence.in_memory import InMemoryConversationRepository
from ai_assistant.infrastructure.adapters.outbound.system_clock import SystemClock
from ai_assistant.infrastructure.config import Settings, load_settings
from ai_assistant.infrastructure.http_client import build_async_http_client, build_http_client, redact_url

LOOPBACK_HOST = "127.0.0.1"
FAKE_CHUNK_DELAY_SECONDS = 0.05  # Para que el streaming simulado se vea en los clientes.

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def build_use_cases(conversations: ConversationRepository, provider: ChatProvider, catalog: ModelCatalog,
                    clock: Clock | None = None) -> chat_v1.ConversationUseCases:
    chat = ChatService(conversations, provider, clock or SystemClock())
    return chat_v1.ConversationUseCases(start=chat, send=chat, cancel=chat,
                                        list_models=ListModelsService(catalog))


def create_app(use_cases: chat_v1.ConversationUseCases, lifespan: Lifespan | None = None) -> FastAPI:
    app = FastAPI(title="AI Assistant", version="chat-v1", lifespan=lifespan)
    chat_v1.install(app, use_cases)
    return app


def app_from_settings(settings: Settings) -> FastAPI:
    conversations = InMemoryConversationRepository()
    if settings.chat_provider == "fake":
        provider = FakeChatProvider(delay_seconds=FAKE_CHUNK_DELAY_SECONDS)
        return create_app(build_use_cases(conversations, provider, FakeModelCatalog()))

    state = LocalState.open(settings.data_dir)
    session = ChatGptPlanSession(state.credentials)
    stream_http = build_async_http_client(settings)
    refresh_http = build_http_client(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await stream_http.aclose()
            refresh_http.close()

    provider = ChatGptPlanProvider(stream_http, session, refresh_http, state.model_log)
    catalog = ChatGptPlanCatalog(stream_http, session, refresh_http, state.model_log)
    return create_app(build_use_cases(conversations, provider, catalog), lifespan)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    settings = load_settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(f"Proveedor: {settings.chat_provider}")
    if settings.chat_provider != "fake":
        print(f"Red: HTTPS vía {redact_url(settings.https_proxy)}; NO_PROXY={','.join(settings.no_proxy) or '(vacío)'}")
    print(f"API: http://{LOOPBACK_HOST}:{settings.api_port}/v1 (solo local, sin autenticación)", flush=True)
    uvicorn.run(app_from_settings(settings), host=LOOPBACK_HOST, port=settings.api_port)


if __name__ == "__main__":
    main()
