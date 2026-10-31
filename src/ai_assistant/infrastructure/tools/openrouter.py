"""Gestión de la API key de OpenRouter (cifrada con DPAPI en AI_ASSISTANT_DATA_DIR).

Uso: python -m ai_assistant.infrastructure.tools.openrouter {set-key,status,forget}
set-key lee la key sin mostrarla (o desde la entrada estándar si no es una terminal). Nunca la imprime.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from ai_assistant.infrastructure.adapters.outbound.llm.openrouter.credentials import (
    InvalidApiKey,
    OpenRouterState,
)
from ai_assistant.infrastructure.config import load_settings


def _read_key() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("API key de OpenRouter (no se muestra): ")
    return sys.stdin.readline()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m ai_assistant.infrastructure.tools.openrouter",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["set-key", "status", "forget"])
    args = parser.parse_args(argv)
    state = OpenRouterState.open(load_settings().data_dir)

    if args.command == "set-key":
        try:
            state.api_key.save(_read_key())
        except InvalidApiKey as error:
            print(error, file=sys.stderr)
            return 2
        print("API key guardada cifrada.")
    elif args.command == "status":
        print("API key: guardada (cifrada)." if state.api_key.load() else "API key: no guardada.")
        verified = state.model_log.verified("openrouter")
        print("Modelos con acceso comprobado: " + (", ".join(sorted(verified)) or "ninguno"))
    else:
        state.api_key.delete()
        print("API key borrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
