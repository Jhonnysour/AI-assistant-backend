"""Registro local de modelos con acceso comprobado, compartido por los adaptadores de proveedores."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ModelAccessLog:
    """Modelos con acceso comprobado (una respuesta completada), agrupados por ámbito.

    El ámbito es la identidad con la que se comprobó: el client_id emitido en chatgpt_plan, o la
    cuenta en OpenRouter. El catálogo indica qué se ofrece; solo esta lista indica qué se ha
    comprobado. No contiene secretos. Se lee del disco en cada uso porque la CLI y el servidor
    pueden escribirla.
    """

    path: Path

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def verified(self, scope: str) -> dict[str, str]:
        return dict(self._load().get(scope, {}))

    def record_completed(self, scope: str, model: str) -> None:
        data = self._load()
        data.setdefault(scope, {})[model] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
