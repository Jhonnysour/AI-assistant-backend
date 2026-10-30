"""Cliente HTTP saliente con proxy explícito.

No se confía en que httpx herede el entorno (trust_env=False): las rutas de proxy se
construyen a partir de Settings para que sean visibles y comprobables.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from ai_assistant.infrastructure.config import Settings

DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)


def _no_proxy_pattern(entry: str) -> str:
    host = entry.strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6, p. ej. ::1
    if host.startswith("."):
        host = f"*{host}"
    return f"all://{host}"


def proxy_routes(settings: Settings) -> dict[str, str | None]:
    """Patrón de URL de httpx -> proxy a usar (None = conexión directa)."""
    if "*" in settings.no_proxy:
        return {"all://": None}
    routes: dict[str, str | None] = {
        "http://": settings.http_proxy,
        "https://": settings.https_proxy,
    }
    for entry in settings.no_proxy:
        routes[_no_proxy_pattern(entry)] = None
    return routes


def redact_url(url: str | None) -> str:
    """Oculta usuario y contraseña de una URL de proxy para mostrarla."""
    if not url:
        return "(conexión directa)"
    parts = urlsplit(url)
    if parts.username or parts.password:
        netloc = f"***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


def build_http_client(settings: Settings, **kwargs) -> httpx.Client:
    mounts = {
        pattern: httpx.HTTPTransport(proxy=proxy) if proxy else httpx.HTTPTransport()
        for pattern, proxy in proxy_routes(settings).items()
    }
    return httpx.Client(trust_env=False, mounts=mounts, timeout=DEFAULT_TIMEOUT, **kwargs)


def build_async_http_client(settings: Settings, **kwargs) -> httpx.AsyncClient:
    """Mismas rutas de proxy que build_http_client, para el streaming del chat."""
    mounts = {
        pattern: httpx.AsyncHTTPTransport(proxy=proxy) if proxy else httpx.AsyncHTTPTransport()
        for pattern, proxy in proxy_routes(settings).items()
    }
    return httpx.AsyncClient(trust_env=False, mounts=mounts, timeout=DEFAULT_TIMEOUT, **kwargs)
