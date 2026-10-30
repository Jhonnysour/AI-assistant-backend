"""Caso de uso: listar los modelos ofrecidos y si su acceso está comprobado."""

from __future__ import annotations

from ai_assistant.application.ports.inbound.conversations import ModelOption, ModelsUnavailable
from ai_assistant.application.ports.outbound.model_catalog import CatalogUnavailable, ModelCatalog
from ai_assistant.application.use_cases.conversations.messages import user_message


class ListModelsService:
    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    async def list_models(self) -> list[ModelOption]:
        try:
            return await self._catalog.list_models()
        except CatalogUnavailable as error:
            raise ModelsUnavailable(error.code, user_message(error.code)) from error
