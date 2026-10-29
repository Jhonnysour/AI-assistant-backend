"""Sesión activa del plan: carga las credenciales cifradas y las renueva cuando hace falta."""

from __future__ import annotations

import threading
from typing import Literal

import httpx

from . import oauth
from .credentials import CredentialRecord, CredentialStore

Reason = Literal["no_session", "plan_usage_not_granted", "reauthorization_required"]


class SessionUnavailable(RuntimeError):
    def __init__(self, reason: Reason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ChatGptPlanSession:
    def __init__(self, store: CredentialStore) -> None:
        self._store = store
        # El refresh token rota: las renovaciones de este proceso se serializan.
        self._lock = threading.Lock()

    def current(self, http: httpx.Client) -> CredentialRecord:
        with self._lock:
            record = self._store.load()
            if record is None or not record.refresh_token:
                raise SessionUnavailable("no_session", "No hay sesión activa; ejecuta 'login'.")
            if not record.plan_usage_granted:
                raise SessionUnavailable(
                    "plan_usage_not_granted",
                    "La sesión no tiene permiso para usar el plan (falta chatgpt.tokens.use.direct).",
                )
            if oauth.needs_refresh(record):
                try:
                    record = oauth.refresh(http, record)
                except oauth.ReauthorizationRequired as error:
                    # Se conserva el client_id emitido para reautorizar con el mismo registro.
                    record.access_token = record.refresh_token = ""
                    record.expires_at = 0
                    self._store.save(record)
                    raise SessionUnavailable("reauthorization_required", f"{error} Ejecuta 'login'.") from error
                self._store.save(record)  # Se guarda antes de usar el nuevo token.
            return record
