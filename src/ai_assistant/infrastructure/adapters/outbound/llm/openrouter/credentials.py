"""API key de OpenRouter, cifrada en reposo, y registro de modelos comprobados.

La key nunca se escribe en el .env, en logs ni en la salida de la CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_assistant.infrastructure.model_access_log import ModelAccessLog
from ai_assistant.infrastructure.secret_store import ProtectedFile, default_protector

API_KEY_ENTROPY = b"ai-assistant/openrouter/api-key/v1"
KEY_PREFIX = "sk-or-"
MODEL_LOG_SCOPE = "openrouter"  # La key identifica la cuenta; el acceso a un modelo es de la cuenta.


class InvalidApiKey(ValueError):
    pass


class ApiKeyMissing(RuntimeError):
    pass


class ApiKeyStore:
    def __init__(self, file: ProtectedFile) -> None:
        self._file = file

    def load(self) -> str | None:
        raw = self._file.read()
        return raw.decode("utf-8") if raw is not None else None

    def require(self) -> str:
        key = self.load()
        if key is None:
            raise ApiKeyMissing("Falta la API key de OpenRouter: python -m ai_assistant.infrastructure.tools.openrouter set-key")
        return key

    def save(self, key: str) -> None:
        key = key.strip()
        if not key.startswith(KEY_PREFIX) or any(c.isspace() for c in key) or len(key) < 20:
            raise InvalidApiKey(f"No parece una API key de OpenRouter (empiezan por {KEY_PREFIX}).")
        self._file.write(key.encode("utf-8"))

    def delete(self) -> None:
        self._file.delete()


@dataclass(frozen=True)
class OpenRouterState:
    """Estado local del adaptador dentro de AI_ASSISTANT_DATA_DIR."""

    api_key: ApiKeyStore
    model_log: ModelAccessLog

    @classmethod
    def open(cls, data_dir: Path) -> OpenRouterState:
        base = data_dir / "openrouter"
        return cls(
            api_key=ApiKeyStore(ProtectedFile(base / "api-key.dpapi", default_protector(API_KEY_ENTROPY))),
            model_log=ModelAccessLog(base / "verified-models.json"),
        )
