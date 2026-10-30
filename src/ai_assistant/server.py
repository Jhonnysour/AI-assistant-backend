"""Raíz de composición del servidor HTTP (contrato chat-v1).

Uso: python -m ai_assistant.server
Escucha solo en 127.0.0.1: la API no tiene autenticación todavía y no debe exponerse.
AI_ASSISTANT_PROVIDER elige el proveedor: "fake" (por defecto, no consume el plan) o "chatgpt_plan".
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import uvicorn
from fastapi import FastAPI

from ai_assistant.config import Settings, load_settings
from ai_assistant.conversations.adapters.inbound import http as http_api
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.credentials import LocalState
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.provider import (
    ChatGptPlanCatalog,
    ChatGptPlanProvider,
)
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.session import ChatGptPlanSession
from ai_assistant.conversations.adapters.outbound.fake_provider import FakeChatProvider, FakeModelCatalog
from ai_assistant.conversations.adapters.outbound.in_memory_repository import (
    InMemoryConversationRepository,
)
from ai_assistant.conversations.application.chat import ChatService
from ai_assistant.conversations.application.ports import ModelCatalog
from ai_assistant.infrastructure.http import build_async_http_client, build_http_client, redact_url

LOOPBACK_HOST = "127.0.0.1"
FAKE_CHUNK_DELAY_SECONDS = 0.05  # Para que el streaming simulado se vea en los clientes.

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(chat: ChatService, catalog: ModelCatalog, lifespan: Lifespan | None = None) -> FastAPI:
    app = FastAPI(title="AI Assistant", version="chat-v1", lifespan=lifespan)
    app.state.chat = chat
    app.state.catalog = catalog
    http_api.install(app)
    return app


def app_from_settings(settings: Settings) -> FastAPI:
    conversations = InMemoryConversationRepository()
    if settings.chat_provider == "fake":
        provider = FakeChatProvider(delay_seconds=FAKE_CHUNK_DELAY_SECONDS)
        return create_app(ChatService(conversations, provider), FakeModelCatalog())

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
    return create_app(ChatService(conversations, provider), catalog, lifespan)


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
