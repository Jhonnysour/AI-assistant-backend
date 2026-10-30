"""Recorrido HTTP con PostgreSQL y reinicio del backend (PC-01, PC-03 de SPEC-002)."""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ai_assistant.domain.conversations.model import Conversation
from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.responses import parse_sse
from ai_assistant.infrastructure.adapters.outbound.persistence.postgres import schema
from ai_assistant.infrastructure.adapters.outbound.persistence.postgres.repository import (
    PostgresConversationRepository,
    create_engine,
)
from ai_assistant.infrastructure.bootstrap.server import app_from_settings
from ai_assistant.infrastructure.config import load_settings


def settings_for(url):
    return load_settings({"AI_ASSISTANT_DATABASE_URL": url, "AI_ASSISTANT_DATA_DIR": "unused"})


def stream(client, conversation_id, text):
    with client.stream("POST", f"/v1/conversations/{conversation_id}/messages",
                       json={"model": "fake-model", "text": text}) as response:
        assert response.status_code == 200
        return [(name, json.loads(data)) for name, data in parse_sse(response.iter_lines())]


def test_pc01_conversation_survives_a_restart(clean_postgres):
    with TestClient(app_from_settings(settings_for(clean_postgres))) as client:
        created = client.post("/v1/conversations").json()
        assert created["persisted"] is True
        conversation_id = created["conversation_id"]
        assert stream(client, conversation_id, "Recuérdame esto")[-1][0] == "response.completed"
        client.patch(f"/v1/conversations/{conversation_id}", json={"title": "Recordatorio"})
        before = client.get(f"/v1/conversations/{conversation_id}").json()

    with TestClient(app_from_settings(settings_for(clean_postgres))) as client:  # Proceso nuevo.
        after = client.get(f"/v1/conversations/{conversation_id}").json()
        assert after == before
        assert after["persisted"] is True and after["title"] == "Recordatorio"
        assert [m["role"] for m in after["messages"]] == ["user", "assistant"]
        assert after["messages"][1]["text"] == "Respuesta simulada a: Recuérdame esto"
        listed = client.get("/v1/conversations").json()["conversations"]
        assert [c["conversation_id"] for c in listed] == [conversation_id]
        # La conversación recuperada admite el turno siguiente.
        assert stream(client, conversation_id, "¿Y ahora?")[-1][0] == "response.completed"


def test_pc03_response_left_streaming_is_interrupted_on_startup(clean_postgres):
    async def crash_mid_response():
        engine = create_engine(clean_postgres)
        try:
            repository = PostgresConversationRepository(engine)
            conversation = Conversation.start(datetime.now(timezone.utc))
            await repository.add(conversation)
            user, reply = conversation.begin_turn("hola", "m", datetime.now(timezone.utc))
            await repository.add_turn(conversation, user, reply)
            conversation.append_text(reply.id, "parcial")
            await repository.save_response(conversation.id, reply)  # Último punto de control.
            return conversation.id
        finally:
            await engine.dispose()

    conversation_id = asyncio.run(crash_mid_response())
    with TestClient(app_from_settings(settings_for(clean_postgres))) as client:
        reply = client.get(f"/v1/conversations/{conversation_id}").json()["messages"][1]
        assert (reply["status"], reply["text"], reply["error"]["code"]) == ("interrupted", "parcial", "interrupted")
        assert reply["finished_at"]
        assert stream(client, conversation_id, "otra vez")[-1][0] == "response.completed"


def test_server_refuses_to_start_with_outdated_schema(clean_postgres):
    from alembic import command

    command.downgrade(schema.alembic_config(clean_postgres), "base")
    try:
        with pytest.raises(schema.SchemaOutdated):
            with TestClient(app_from_settings(settings_for(clean_postgres))):
                pass
    finally:
        schema.upgrade(clean_postgres)
