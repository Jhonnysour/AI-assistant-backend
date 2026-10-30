"""Implementación del puerto Clock con el reloj del sistema."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def __call__(self) -> datetime:
        return datetime.now(timezone.utc)
