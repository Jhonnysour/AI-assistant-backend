"""Configuración leída del entorno. Pertenece a la composición, no al dominio."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OAUTH_CALLBACK_PORT = 1455
DEFAULT_API_PORT = 8000
# "fake" no llama a ningún proveedor; "chatgpt_plan" usa la sesión de Sign in with ChatGPT.
CHAT_PROVIDERS = ("fake", "chatgpt_plan")


@dataclass(frozen=True)
class Settings:
    http_proxy: str | None
    https_proxy: str | None
    no_proxy: tuple[str, ...]
    data_dir: Path
    oauth_callback_port: int
    browser_command: str | None
    chat_provider: str = "fake"
    api_port: int = DEFAULT_API_PORT


def _env(environ: dict[str, str], name: str) -> str | None:
    # En Windows las variables no distinguen mayúsculas; en Linux se aceptan ambas formas.
    value = environ.get(name) or environ.get(name.lower())
    return value.strip() if value and value.strip() else None


def _default_data_dir(environ: dict[str, str]) -> Path:
    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ai-assistant"
    return Path.home() / ".local" / "share" / "ai-assistant"


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    environ = dict(os.environ) if environ is None else environ
    no_proxy = _env(environ, "NO_PROXY") or ""
    data_dir = _env(environ, "AI_ASSISTANT_DATA_DIR")
    port = _env(environ, "AI_ASSISTANT_OAUTH_PORT")
    api_port = _env(environ, "AI_ASSISTANT_API_PORT")
    provider = _env(environ, "AI_ASSISTANT_PROVIDER") or "fake"
    if provider not in CHAT_PROVIDERS:
        raise ValueError(f"AI_ASSISTANT_PROVIDER debe ser uno de {', '.join(CHAT_PROVIDERS)}.")
    return Settings(
        http_proxy=_env(environ, "HTTP_PROXY"),
        https_proxy=_env(environ, "HTTPS_PROXY"),
        no_proxy=tuple(entry.strip() for entry in no_proxy.split(",") if entry.strip()),
        data_dir=Path(data_dir) if data_dir else _default_data_dir(environ),
        oauth_callback_port=int(port) if port else DEFAULT_OAUTH_CALLBACK_PORT,
        browser_command=_env(environ, "AI_ASSISTANT_BROWSER"),
        chat_provider=provider,
        api_port=int(api_port) if api_port else DEFAULT_API_PORT,
    )
