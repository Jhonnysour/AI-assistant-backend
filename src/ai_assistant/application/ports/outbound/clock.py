"""Puerto de salida: hora actual, para que las fechas sean deterministas en las pruebas."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def __call__(self) -> datetime:
        """Momento actual en UTC (con zona horaria)."""
        ...
