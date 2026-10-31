"""Estado local del adaptador: identificador de host, credenciales cifradas y modelos comprobados."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ai_assistant.infrastructure.model_access_log import ModelAccessLog
from ai_assistant.infrastructure.secret_store import ProtectedFile, default_protector

PLAN_USAGE_SCOPE = "chatgpt.tokens.use.direct"
CREDENTIALS_ENTROPY = b"ai-assistant/chatgpt-plan/credentials/v1"


@dataclass
class CredentialRecord:
    issuer: str
    subject: str
    client_id: str
    ext_agent_host_id: str
    id_token: str
    access_token: str
    refresh_token: str
    token_type: str
    expires_at: float
    scopes: list[str]
    saved_at: str = ""

    @property
    def plan_usage_granted(self) -> bool:
        return PLAN_USAGE_SCOPE in self.scopes

    def __repr__(self) -> str:  # Evita volcar tokens en trazas o depuración.
        return (
            f"CredentialRecord(client_id={self.client_id!r}, scopes={self.scopes!r}, "
            f"expires_at={self.expires_at!r})"
        )


def load_or_create_host_id(path: Path) -> str:
    """Identificador opaco y estable de este host (no es una credencial)."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    host_id = f"urn:uuid:{uuid.uuid4()}"
    path.write_text(host_id, encoding="utf-8")
    return host_id


class CredentialStore:
    def __init__(self, protected_file: ProtectedFile) -> None:
        self._file = protected_file

    def load(self) -> CredentialRecord | None:
        raw = self._file.read()
        if raw is None:
            return None
        return CredentialRecord(**json.loads(raw.decode("utf-8")))

    def save(self, record: CredentialRecord) -> None:
        record.saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._file.write(json.dumps(asdict(record)).encode("utf-8"))

    def delete(self) -> None:
        self._file.delete()


@dataclass(frozen=True)
class LocalState:
    """Rutas del estado local del adaptador dentro de AI_ASSISTANT_DATA_DIR."""

    host_id_path: Path
    credentials: CredentialStore
    model_log: ModelAccessLog

    @classmethod
    def open(cls, data_dir: Path) -> LocalState:
        base = data_dir / "chatgpt-plan"
        return cls(
            host_id_path=data_dir / "host-id",
            credentials=CredentialStore(
                ProtectedFile(base / "credentials.dpapi", default_protector(CREDENTIALS_ENTROPY))
            ),
            model_log=ModelAccessLog(base / "verified-models.json"),
        )
