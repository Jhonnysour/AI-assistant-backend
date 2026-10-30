"""Contrato del puerto ConversationRepository: en memoria y PostgreSQL (PC-02, PC-06 de SPEC-002)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from ai_assistant.application import error_codes as codes
from ai_assistant.application.errors import StorageUnavailable
from ai_assistant.domain.conversations.model import Conversation, ResponseStatus, SendInProgress
from ai_assistant.infrastructure.adapters.outbound.persistence.in_memory import InMemoryConversationRepository
from ai_assistant.infrastructure.adapters.outbound.persistence.postgres.repository import (
    PostgresConversationRepository,
    create_engine,
)

START = datetime(2026, 10, 30, 9, 0, 0, 123456, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


@pytest.fixture(params=["memoria", "postgres"])
def repository_factory(request):
    if request.param == "memoria":
        @asynccontextmanager
        async def memory():
            yield InMemoryConversationRepository()
        return memory

    url = request.getfixturevalue("clean_postgres")

    @asynccontextmanager
    async def postgres():
        engine = create_engine(url)
        try:
            yield PostgresConversationRepository(engine)
        finally:
            await engine.dispose()
    return postgres


def run(factory, scenario):
    async def main():
        async with factory() as repository:
            await scenario(repository)
    asyncio.run(main())


def test_new_conversation_round_trip(repository_factory):
    async def scenario(repo):
        conversation = Conversation.start(at(0))
        await repo.add(conversation)
        loaded = await repo.get(conversation.id)
        assert loaded == conversation
        assert loaded.updated_at == at(0) and loaded.title is None and loaded.messages == []
        assert await repo.get("0" * 32) is None
        assert await repo.get("no-es-un-uuid") is None
    run(repository_factory, scenario)


def test_turns_are_stored_in_order_with_their_responses(repository_factory):
    async def scenario(repo):
        conversation = Conversation.start(at(0))
        await repo.add(conversation)
        user, reply = conversation.begin_turn("  primera  pregunta ", "gpt-6-luna", at(1))
        await repo.add_turn(conversation, user, reply)

        loaded = await repo.get(conversation.id)
        assert loaded.active_response_id == reply.id
        assert loaded.title == "primera pregunta" and loaded.updated_at == at(1)
        assert loaded.messages[0].text == "  primera  pregunta "  # Sin recortar (R11).

        conversation.append_text(reply.id, "Hola")
        await repo.save_response(conversation.id, reply)  # Punto de control.
        assert (await repo.get(conversation.id)).response(reply.id).text == "Hola"

        conversation.complete(reply.id, at(2), "gpt-6-luna-x")
        await repo.save_response(conversation.id, reply)
        user2, reply2 = conversation.begin_turn("segunda", "m", at(3))
        await repo.add_turn(conversation, user2, reply2)
        conversation.fail(reply2.id, codes.USAGE_LIMIT, at(4))
        await repo.save_response(conversation.id, reply2)

        loaded = await repo.get(conversation.id)
        assert loaded == conversation
        assert [type(m).__name__ for m in loaded.messages] == [
            "UserMessage", "AssistantResponse", "UserMessage", "AssistantResponse"]
        first, second = loaded.response(reply.id), loaded.response(reply2.id)
        assert (first.status, first.model_reported, first.finished_at) == (ResponseStatus.COMPLETED, "gpt-6-luna-x", at(2))
        assert (second.status, second.error_code) == (ResponseStatus.FAILED, codes.USAGE_LIMIT)
        assert loaded.active_response_id is None
    run(repository_factory, scenario)


def test_list_details_delete_and_active_responses(repository_factory):
    async def scenario(repo):
        old, recent, busy = (Conversation.start(at(i)) for i in range(3))
        for conversation in (old, recent, busy):
            await repo.add(conversation)
        recent.rename("Mi título", at(10))
        await repo.save_details(recent)
        user, reply = busy.begin_turn("en curso", "m", at(5))
        await repo.add_turn(busy, user, reply)

        headers = await repo.list_recent()
        assert [h.id for h in headers] == [recent.id, busy.id, old.id]
        assert (headers[0].title, headers[0].title_is_custom, headers[0].updated_at) == ("Mi título", True, at(10))

        assert [c.id for c in await repo.with_active_response()] == [busy.id]

        await repo.delete(busy.id)
        assert await repo.get(busy.id) is None
        assert await repo.with_active_response() == []
        assert [h.id for h in await repo.list_recent()] == [recent.id, old.id]
    run(repository_factory, scenario)


# --- Solo PostgreSQL ---

def postgres_run(url, scenario):
    async def main():
        engine = create_engine(url)
        try:
            await scenario(PostgresConversationRepository(engine))
        finally:
            await engine.dispose()
    asyncio.run(main())


def test_pc02_concurrent_sends_from_two_copies_only_one_is_accepted(clean_postgres):
    async def scenario(repo):
        conversation = Conversation.start(at(0))
        await repo.add(conversation)
        first, second = await repo.get(conversation.id), await repo.get(conversation.id)
        assert first is not second
        turn_a = first.begin_turn("uno", "m", at(1))  # Cada copia cree que no hay nada en curso.
        turn_b = second.begin_turn("dos", "m", at(1))
        results = await asyncio.gather(repo.add_turn(first, *turn_a), repo.add_turn(second, *turn_b),
                                       return_exceptions=True)
        assert sorted(type(r).__name__ for r in results) == ["NoneType", "SendInProgress"]
        loaded = await repo.get(conversation.id)
        assert len(loaded.messages) == 2 and loaded.active_response_id is not None

        # Una vez terminada la respuesta, el siguiente envío se acepta.
        loaded.cancel(loaded.active_response_id, at(2))
        await repo.save_response(loaded.id, loaded.messages[1])
        await repo.add_turn(loaded, *loaded.begin_turn("tres", "m", at(3)))
    postgres_run(clean_postgres, scenario)


def test_stale_copy_cannot_reuse_a_position(clean_postgres):
    async def scenario(repo):
        conversation = Conversation.start(at(0))
        await repo.add(conversation)
        stale = await repo.get(conversation.id)
        user, reply = conversation.begin_turn("uno", "m", at(1))
        await repo.add_turn(conversation, user, reply)
        conversation.complete(reply.id, at(2))
        await repo.save_response(conversation.id, reply)
        with pytest.raises(SendInProgress):  # La copia vieja no sabe que ya hubo un turno.
            await repo.add_turn(stale, *stale.begin_turn("dos", "m", at(3)))
    postgres_run(clean_postgres, scenario)


def test_unreachable_database_is_storage_unavailable_without_details():
    async def scenario():
        engine = create_engine("postgresql://ai_assistant:secreto@127.0.0.1:9/ai_assistant")
        try:
            with pytest.raises(StorageUnavailable) as error:
                await PostgresConversationRepository(engine).get("0" * 32)
            assert "secreto" not in str(error.value) and error.value.__cause__ is None
        finally:
            await engine.dispose()
    asyncio.run(scenario())


def test_migrations_match_table_definitions(postgres_url):
    from alembic import command

    from ai_assistant.infrastructure.adapters.outbound.persistence.postgres import schema

    command.check(schema.alembic_config(postgres_url))  # Falla si el esquema y tables.py difieren.

    async def revision():
        engine = create_engine(postgres_url)
        try:
            await schema.ensure_current(engine)
        finally:
            await engine.dispose()
    asyncio.run(revision())
