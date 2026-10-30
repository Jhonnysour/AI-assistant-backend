"""Puerto de salida: catálogo de modelos del proveedor."""

from __future__ import annotations

from typing import Protocol

from ai_assistant.domain.conversations.model import ModelOption


class CatalogUnavailable(Exception):
    """El catálogo no se pudo obtener. code es uno de application.error_codes."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelCatalog(Protocol):
    async def list_models(self) -> list[ModelOption]:
        """Modelos ofrecidos, en el orden del proveedor. Lanza CatalogUnavailable."""
        ...
