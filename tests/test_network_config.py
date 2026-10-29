from pathlib import Path

import httpx

from ai_assistant.config import load_settings
from ai_assistant.infrastructure.http import build_http_client, proxy_routes, redact_url

PROXY = "http://127.0.0.1:17890"
ENV = {
    "HTTP_PROXY": PROXY,
    "HTTPS_PROXY": PROXY,
    "NO_PROXY": "localhost,127.0.0.1,::1",
    "LOCALAPPDATA": r"C:\Users\someone\AppData\Local",
}


def test_settings_read_proxy_and_defaults():
    settings = load_settings(ENV)
    assert settings.https_proxy == PROXY
    assert settings.no_proxy == ("localhost", "127.0.0.1", "::1")
    assert settings.oauth_callback_port == 1455
    assert settings.data_dir == Path(r"C:\Users\someone\AppData\Local") / "ai-assistant"
    assert settings.browser_command is None


def test_lowercase_variables_are_accepted():
    settings = load_settings({"https_proxy": PROXY, "no_proxy": "localhost"})
    assert settings.https_proxy == PROXY
    assert settings.no_proxy == ("localhost",)


def test_routes_send_https_to_proxy_and_loopback_direct():
    routes = proxy_routes(load_settings(ENV))
    assert routes["https://"] == PROXY
    assert routes["all://127.0.0.1"] is None
    assert routes["all://localhost"] is None
    assert routes["all://[::1]"] is None


def test_wildcard_no_proxy_disables_proxy():
    assert proxy_routes(load_settings({**ENV, "NO_PROXY": "*"})) == {"all://": None}


def test_client_selects_transport_by_host():
    # Se inspecciona la selección de transporte de httpx (API interna de 0.28) sin abrir conexiones.
    with build_http_client(load_settings(ENV)) as client:
        openai = client._transport_for_url(httpx.URL("https://api.openai.com/v1/models"))
        loopback = client._transport_for_url(httpx.URL("http://127.0.0.1:1455/auth/callback"))
        assert openai._pool._proxy_url is not None
        assert not hasattr(loopback._pool, "_proxy_url")


def test_client_ignores_environment():
    with build_http_client(load_settings({})) as client:
        assert client.trust_env is False


def test_redact_proxy_credentials():
    assert redact_url("http://user:secret@proxy.local:8080") == "http://***@proxy.local:8080"
    assert redact_url(PROXY) == PROXY
    assert redact_url(None) == "(conexión directa)"
