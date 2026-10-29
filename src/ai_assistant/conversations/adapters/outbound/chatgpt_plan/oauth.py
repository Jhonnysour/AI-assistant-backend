"""Sign in with ChatGPT: autorización con PKCE y callback en loopback.

Referencias (consultadas el 2026-10-29):
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from .credentials import CredentialRecord

ISSUER = "https://auth.openai.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "AI Assistant"
CALLBACK_PATH = "/auth/callback"
REFRESH_MARGIN_SECONDS = 300

TERMINAL_REFRESH_ERRORS = {
    "invalid_grant", "invalid_refresh_token", "token_expired",
    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused",
}


class OAuthError(RuntimeError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ReauthorizationRequired(OAuthError):
    pass


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str

    @classmethod
    def generate(cls) -> Pkce:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return cls(verifier, challenge)


@dataclass(frozen=True)
class AuthorizationRequest:
    client_id: str
    host_id: str
    redirect_uri: str
    state: str
    nonce: str
    pkce: Pkce

    @property
    def registering(self) -> bool:
        return self.client_id == DYNAMIC_CLIENT_ID

    @classmethod
    def new(cls, client_id: str | None, host_id: str, redirect_uri: str) -> AuthorizationRequest:
        return cls(
            client_id=client_id or DYNAMIC_CLIENT_ID,
            host_id=host_id,
            redirect_uri=redirect_uri,
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            pkce=Pkce.generate(),
        )

    def url(self) -> str:
        params = {
            "client_id": self.client_id,
            "ext_agent_host_id": self.host_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge_method": "S256",
            "code_challenge": self.pkce.challenge,
        }
        if self.registering:
            params["agent_name_hint"] = AGENT_NAME
        return f"{AUTHORIZE_URL}?{urlencode(params)}"


def parse_callback(query: dict[str, str], request: AuthorizationRequest) -> tuple[str, str]:
    """Valida el callback y devuelve (code, client_id emitido)."""
    if not secrets.compare_digest(query.get("state", ""), request.state):
        raise OAuthError("El parámetro state del callback no coincide; se descarta.", "state_mismatch")
    if "error" in query:
        if query["error"] == "access_denied":
            raise OAuthError("El usuario canceló o denegó la autorización.", "access_denied")
        raise OAuthError("La autorización devolvió un error.", query["error"])
    code = query.get("code")
    if not code:
        raise OAuthError("El callback no incluye código de autorización.", "missing_code")
    returned_client = query.get("client_id")
    if request.registering:
        if not returned_client or returned_client == DYNAMIC_CLIENT_ID:
            raise OAuthError("El registro no devolvió un client_id emitido.", "missing_client_id")
        return code, returned_client
    if returned_client and returned_client != request.client_id:
        raise OAuthError("El callback devolvió un client_id distinto del guardado.", "client_id_mismatch")
    return code, request.client_id


class CallbackListener:
    """Servidor HTTP de un solo uso en 127.0.0.1. No pasa por el proxy: es una conexión entrante."""

    def __init__(self, port: int) -> None:
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por http.server)
                parts = urlsplit(self.path)
                if parts.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                listener.query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                body = "Autorización recibida. Puedes cerrar esta pestaña y volver al terminal."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, format, *args) -> None:
                pass  # La URL del callback contiene el código: no se registra.

        self.query: dict[str, str] | None = None
        self._server = HTTPServer(("127.0.0.1", port), Handler)
        self._server.timeout = 0.5

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}{CALLBACK_PATH}"

    def wait(self, timeout_seconds: float) -> dict[str, str]:
        deadline = time.monotonic() + timeout_seconds
        while self.query is None:
            if time.monotonic() > deadline:
                raise OAuthError("Tiempo de espera agotado esperando el callback.", "callback_timeout")
            self._server.handle_request()
        return self.query

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> CallbackListener:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return error.get("code") or error.get("type")
    return error if isinstance(error, str) else None


def _token_request(http: httpx.Client, form: dict[str, str]) -> dict:
    response = http.post(TOKEN_URL, data=form, headers={"Accept": "application/json"})
    if response.status_code != 200:
        code = _error_code(response)
        message = f"El endpoint de tokens respondió {response.status_code} ({code or 'sin código'})."
        if code in TERMINAL_REFRESH_ERRORS:
            raise ReauthorizationRequired(message, code)
        raise OAuthError(message, code)
    return response.json()


def exchange_code(http: httpx.Client, request: AuthorizationRequest, code: str, client_id: str) -> dict:
    return _token_request(http, {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": request.pkce.verifier,
        "client_id": client_id,
        "redirect_uri": request.redirect_uri,
        "resource": RESOURCE,
    })


def fetch_jwks(http: httpx.Client) -> dict:
    discovery = http.get(DISCOVERY_URL)
    discovery.raise_for_status()
    config = discovery.json()
    if config.get("issuer") != ISSUER:
        raise OAuthError("El issuer publicado no coincide con el esperado.", "issuer_mismatch")
    keys = http.get(config["jwks_uri"])
    keys.raise_for_status()
    return keys.json()


def validate_id_token(id_token: str, jwks: dict, client_id: str, nonce: str | None) -> dict:
    """Verifica firma (JWKS), issuer, audiencia, expiración y nonce."""
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
        key_set = jwt.PyJWKSet.from_dict(jwks)
        key = next((k for k in key_set.keys if k.key_id == kid), None)
        if key is None:
            raise OAuthError("La clave de firma del ID token no está en el JWKS.", "unknown_kid")
        claims = jwt.decode(
            id_token, key=key, algorithms=["RS256"], audience=client_id,
            issuer=ISSUER, leeway=60, options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise OAuthError(f"ID token no válido: {type(error).__name__}.", "invalid_id_token") from error
    if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OAuthError("El nonce del ID token no coincide.", "nonce_mismatch")
    return claims


def record_from_tokens(
    tokens: dict, claims: dict, client_id: str, host_id: str, previous: CredentialRecord | None = None,
) -> CredentialRecord:
    scope = tokens.get("scope")
    refresh_token = tokens.get("refresh_token") or (previous.refresh_token if previous else None)
    if not refresh_token:
        raise OAuthError("La respuesta de tokens no incluye refresh_token.", "missing_refresh_token")
    return CredentialRecord(
        issuer=ISSUER,
        subject=claims["sub"],
        client_id=client_id,
        ext_agent_host_id=host_id,
        id_token=tokens.get("id_token") or (previous.id_token if previous else ""),
        access_token=tokens["access_token"],
        refresh_token=refresh_token,
        token_type=tokens.get("token_type", "Bearer"),
        expires_at=time.time() + float(tokens.get("expires_in", 3600)),
        scopes=scope.split() if scope else (list(previous.scopes) if previous else []),
    )


def login(
    http: httpx.Client,
    host_id: str,
    port: int,
    open_url: Callable[[str], None],
    existing: CredentialRecord | None = None,
    timeout_seconds: float = 300,
) -> CredentialRecord:
    """Ejecuta el flujo completo y devuelve credenciales validadas (no las guarda)."""
    jwks = fetch_jwks(http)
    with CallbackListener(port) as listener:  # El listener escucha antes de abrir el navegador.
        request = AuthorizationRequest.new(
            existing.client_id if existing else None, host_id, listener.redirect_uri,
        )
        open_url(request.url())
        query = listener.wait(timeout_seconds)
    code, client_id = parse_callback(query, request)
    tokens = exchange_code(http, request, code, client_id)
    claims = validate_id_token(tokens["id_token"], jwks, client_id, request.nonce)
    if existing and claims["sub"] != existing.subject:
        raise OAuthError(
            "La cuenta elegida no es la registrada; ejecuta 'logout --forget' antes de usar otra.",
            "account_mismatch",
        )
    return record_from_tokens(tokens, claims, client_id, host_id)


def needs_refresh(record: CredentialRecord, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return record.expires_at - now < REFRESH_MARGIN_SECONDS


def refresh(http: httpx.Client, record: CredentialRecord) -> CredentialRecord:
    """Renueva el access token. El refresh token rota: hay que guardar el resultado de inmediato."""
    tokens = _token_request(http, {
        "grant_type": "refresh_token",
        "client_id": record.client_id,
        "refresh_token": record.refresh_token,
        "resource": RESOURCE,
    })
    claims = {"sub": record.subject}
    if tokens.get("id_token"):
        claims = validate_id_token(tokens["id_token"], fetch_jwks(http), record.client_id, nonce=None)
        if claims["sub"] != record.subject:
            raise OAuthError("La renovación devolvió otra identidad.", "account_mismatch")
    return record_from_tokens(tokens, claims, record.client_id, record.ext_agent_host_id, record)


def revoke(http: httpx.Client, record: CredentialRecord) -> bool:
    """Revoca el refresh token. Devuelve False si no se pudo confirmar."""
    try:
        config = http.get(DISCOVERY_URL).json()
        response = http.post(config["revocation_endpoint"], data={
            "token": record.refresh_token,
            "token_type_hint": "refresh_token",
            "client_id": record.client_id,
        })
    except (httpx.HTTPError, KeyError, ValueError):
        return False
    return response.status_code == 200
