"""Prueba de viabilidad del proveedor (SPEC-001): login, catálogo y una petición en streaming.

Uso: python -m ai_assistant.probe {login,status,models,ask,logout}
Es la raíz de composición temporal de esta prueba; no forma parte del dominio.
Nunca imprime tokens. La URL de autorización solo se muestra con --print-url o si no hay
navegador configurado, y nunca se escribe en archivos.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import subprocess
import sys
import time

from ai_assistant.config import Settings, load_settings
from ai_assistant.conversations.adapters.outbound.chatgpt_plan import oauth, responses
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.credentials import (
    CREDENTIALS_ENTROPY,
    CredentialRecord,
    CredentialStore,
    ModelAccessLog,
    load_or_create_host_id,
)
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.session import (
    ChatGptPlanSession,
    SessionUnavailable,
)
from ai_assistant.infrastructure.http import build_async_http_client, build_http_client, redact_url
from ai_assistant.infrastructure.secret_store import (
    ProtectedFile,
    SecretStoreCorrupted,
    SecretStoreUnavailable,
    default_protector,
)

EXIT_OK, EXIT_PROVIDER, EXIT_ACTION_NEEDED, EXIT_CANCELLED = 0, 1, 2, 130

ERROR_EXPLANATIONS = {
    "subscription_sharing_user_not_eligible":
        "La cuenta, el workspace o la política no permiten usar el plan en esta app. No se reintenta.",
    "subscription_sharing_usage_limit_exceeded":
        "Se alcanzó el límite de uso del plan para esta app. Revisa ChatGPT > Settings > Usage.",
    "subscription_sharing_usage_unavailable":
        "No se pudo comprobar el uso del plan. Reintenta más tarde.",
    "subscription_sharing_unsupported_capability":
        "La petición incluye una opción no admitida en esta vía (modelo, herramienta o parámetro).",
    "subscription_sharing_route_not_supported": "Ruta o método no admitido en esta vía.",
    "subscription_sharing_invalid_user":
        "No se pudo validar la identidad del suscriptor. Ejecuta 'status' y, si persiste, 'login'.",
    "subscription_sharing_user_unavailable":
        "Información de usuario o workspace no disponible temporalmente. Reintenta más tarde.",
}


class Context:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        base = settings.data_dir / "chatgpt-plan"
        self.host_id_path = settings.data_dir / "host-id"
        self.credentials = CredentialStore(
            ProtectedFile(base / "credentials.dpapi", default_protector(CREDENTIALS_ENTROPY))
        )
        self.model_log = ModelAccessLog(base / "verified-models.json")

    def http(self):
        return build_http_client(self.settings)

    def async_http(self):
        return build_async_http_client(self.settings)


def _print_network(settings: Settings) -> None:
    print(f"Red: HTTPS vía {redact_url(settings.https_proxy)}; NO_PROXY={','.join(settings.no_proxy) or '(vacío)'}")
    print(f"Callback OAuth: http://127.0.0.1:{settings.oauth_callback_port}{oauth.CALLBACK_PATH} (local, sin proxy)")


def _mask(client_id: str) -> str:
    return f"{client_id[:7]}…{client_id[-4:]}" if len(client_id) > 12 else "…"


def _explain_provider_error(error: responses.ProviderError) -> None:
    print(f"Error del proveedor: {error}", file=sys.stderr)
    if error.code in ERROR_EXPLANATIONS:
        print(ERROR_EXPLANATIONS[error.code], file=sys.stderr)
    elif error.code == "model_not_found":
        print("Usa el slug de la primera columna de 'models' (p. ej. gpt-6-luna), no el nombre visible.",
              file=sys.stderr)
    elif error.detail:
        print(f"Detalle: {error.detail}", file=sys.stderr)


def _url_opener(settings: Settings, print_url: bool):
    if print_url or not settings.browser_command:
        def show(url: str) -> None:
            print("\nAbre esta URL en el perfil de Chrome que usa el proxy.")
            print("Es de un solo uso; no la guardes, no la compartas y no la pegues en chats:\n")
            print(url, flush=True)
            print("\nEsperando la autorización (máx. 5 minutos)...", flush=True)
        return show

    def launch(url: str) -> None:
        # Sin shell: la URL se pasa como argumento y no se registra.
        if os.name == "nt":
            subprocess.Popen(f'{settings.browser_command} "{url}"')
        else:
            subprocess.Popen([*shlex.split(settings.browser_command), url])
        print("Se abrió el navegador configurado en AI_ASSISTANT_BROWSER. Esperando la autorización...", flush=True)
    return launch


def _clear_tokens(ctx: Context, record: CredentialRecord) -> None:
    # Se conserva el client_id emitido y la identidad para reautorizar con el mismo registro.
    record.access_token = record.refresh_token = ""
    record.expires_at = 0
    ctx.credentials.save(record)


def _active_record(ctx: Context, http) -> CredentialRecord | None:
    try:
        return ChatGptPlanSession(ctx.credentials).current(http)
    except SessionUnavailable as error:
        print(error, file=sys.stderr)
        if error.reason == "plan_usage_not_granted":
            print("Repite 'login' y concede el uso del plan. No se activa ninguna API de pago.", file=sys.stderr)
        return None


def cmd_login(ctx: Context, args) -> int:
    _print_network(ctx.settings)
    host_id = load_or_create_host_id(ctx.host_id_path)
    existing = ctx.credentials.load()
    with ctx.http() as http:
        record = oauth.login(
            http, host_id, ctx.settings.oauth_callback_port,
            _url_opener(ctx.settings, args.print_url), existing,
        )
    ctx.credentials.save(record)
    print("Sesión iniciada y guardada cifrada (DPAPI).")
    if record.plan_usage_granted:
        print("Uso del plan: concedido (chatgpt.tokens.use.direct).")
        return EXIT_OK
    print("Uso del plan: NO concedido. Repite 'login' y acepta el uso del plan para continuar.")
    return EXIT_ACTION_NEEDED


def cmd_status(ctx: Context, args) -> int:
    record = ctx.credentials.load()
    print(f"Datos locales: {ctx.settings.data_dir}")
    if record is None:
        print("Registro: ninguno.")
        return EXIT_ACTION_NEEDED
    print(f"Registro: client_id {_mask(record.client_id)}")
    print(f"Uso del plan concedido: {'sí' if record.plan_usage_granted else 'no'}")
    if record.refresh_token:
        minutes = int((record.expires_at - time.time()) // 60)
        print(f"Access token: {'caduca en %d min' % minutes if minutes > 0 else 'caducado (se renovará)'}")
    else:
        print("Tokens: ninguno (ejecuta 'login').")
    verified = ctx.model_log.verified(record.client_id)
    print("Modelos con acceso comprobado: " + (", ".join(sorted(verified)) or "ninguno todavía"))
    return EXIT_OK


def cmd_models(ctx: Context, args) -> int:
    _print_network(ctx.settings)
    with ctx.http() as http:
        record = _active_record(ctx, http)
        if record is None:
            return EXIT_ACTION_NEEDED
        try:
            catalog = responses.list_models(http, record.access_token)
        except responses.ProviderError as error:
            _explain_provider_error(error)
            return EXIT_PROVIDER
    verified = ctx.model_log.verified(record.client_id)
    print(f"Catálogo ofrecido ({len(catalog)}). Ofrecido no implica acceso comprobado:")
    for model in catalog:
        mark = f"acceso comprobado {verified[model.slug]}" if model.slug in verified else "sin comprobar"
        print(f"  {model.slug:<28} {model.display_name:<28} [{mark}]")
    return EXIT_OK


def cmd_ask(ctx: Context, args) -> int:
    if not args.text.strip():
        print("Mensaje vacío: no se llama al proveedor.", file=sys.stderr)
        return EXIT_ACTION_NEEDED
    _print_network(ctx.settings)
    with ctx.http() as http:
        record = _active_record(ctx, http)
    if record is None:
        return EXIT_ACTION_NEEDED
    print(f"Modelo: {args.model}\n--- respuesta ---", flush=True)
    progress = {"received": False}
    try:
        return asyncio.run(_ask(ctx, record, args.model, args.text, progress))
    except KeyboardInterrupt:
        print("\n--- cancelado por el usuario ---", file=sys.stderr)
        if progress["received"]:
            print("El texto mostrado es parcial (cancelado).", file=sys.stderr)
        return EXIT_CANCELLED


async def _ask(ctx: Context, record: CredentialRecord, model: str, text: str, progress: dict) -> int:
    async with ctx.async_http() as http:
        events = responses.stream_response(http, record.access_token, model, [{"role": "user", "content": text}])
        try:
            async for event in events:
                if isinstance(event, responses.TextDelta):
                    progress["received"] = progress["received"] or bool(event.text)
                    print(event.text, end="", flush=True)
                    continue
                print("\n--- fin ---")
                if event.model:
                    print(f"Modelo informado por el proveedor: {event.model}")
                if event.completed:
                    ctx.model_log.record_completed(record.client_id, model)
                    print(f"Estado: completed. Acceso comprobado para '{model}'.")
                    return EXIT_OK
                print(f"Estado: {event.status} ({event.code}).", file=sys.stderr)
                if event.code in ERROR_EXPLANATIONS:
                    print(ERROR_EXPLANATIONS[event.code], file=sys.stderr)
                if progress["received"]:
                    print("El texto mostrado es parcial y NO es una respuesta completa.", file=sys.stderr)
                return EXIT_PROVIDER
        except responses.ProviderError as error:
            _explain_provider_error(error)
            return EXIT_PROVIDER
        finally:
            await events.aclose()  # Cierra la conexión del stream en cualquier salida.
    return EXIT_PROVIDER


def cmd_logout(ctx: Context, args) -> int:
    record = ctx.credentials.load()
    if record is None:
        print("No hay registro guardado.")
        return EXIT_OK
    if record.refresh_token:
        with ctx.http() as http:
            confirmed = oauth.revoke(http, record)
        print("Revocación remota confirmada." if confirmed else
              "No se confirmó la revocación remota; puedes desconectar la app en ChatGPT > Settings.")
    if args.forget:
        ctx.credentials.delete()
        print("Registro local eliminado.")
    else:
        _clear_tokens(ctx, record)
        print("Tokens locales eliminados; se conserva el registro (client_id) para un próximo login.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ai_assistant.probe", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="Iniciar sesión con ChatGPT y conceder el uso del plan.")
    login.add_argument("--print-url", action="store_true", help="Mostrar la URL en vez de abrir el navegador.")
    sub.add_parser("status", help="Estado local, sin red.")
    sub.add_parser("models", help="Catálogo de modelos ofrecidos.")
    ask = sub.add_parser("ask", help="Enviar un mensaje y recibir la respuesta en streaming.")
    ask.add_argument("--model", required=True, help="slug del catálogo")
    ask.add_argument("text")
    logout = sub.add_parser("logout", help="Revocar la sesión y borrar tokens locales.")
    logout.add_argument("--forget", action="store_true", help="Eliminar también el registro local.")
    return parser


COMMANDS = {"login": cmd_login, "status": cmd_status, "models": cmd_models, "ask": cmd_ask, "logout": cmd_logout}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        ctx = Context(load_settings())
        return COMMANDS[args.command](ctx, args)
    except (SecretStoreUnavailable, SecretStoreCorrupted) as error:
        print(error, file=sys.stderr)
        return EXIT_ACTION_NEEDED
    except oauth.OAuthError as error:
        print(f"Autenticación: {error}", file=sys.stderr)
        return EXIT_ACTION_NEEDED
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as error:  # Red, proxy, etc.: tipo de error sin volcar detalles internos.
        print(f"Error inesperado: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_PROVIDER


if __name__ == "__main__":
    raise SystemExit(main())
