"""Mensajes al usuario para cada código de error (docs/api/chat-v1.md)."""

from ai_assistant.application import error_codes as codes

ERROR_MESSAGES = {
    codes.AUTH_REQUIRED: "El backend necesita volver a iniciar sesión con el proveedor.",
    codes.NOT_ELIGIBLE: "La cuenta del proveedor no permite esta operación.",
    codes.USAGE_LIMIT: "Se alcanzó el límite de uso del proveedor. Inténtalo más tarde.",
    codes.INSUFFICIENT_CREDITS: "La cuenta del proveedor no tiene saldo suficiente.",
    codes.RATE_LIMITED: "El proveedor está limitando las peticiones. Puedes reintentar en unos segundos.",
    codes.MODEL_UNAVAILABLE: "El modelo elegido no está disponible para esta cuenta.",
    codes.PROVIDER_UNAVAILABLE: "No se pudo contactar con el proveedor. Puedes reintentar.",
    codes.INCOMPLETE: "El proveedor terminó la respuesta sin completarla. Puedes reintentar.",
    codes.INTERRUPTED: "La respuesta se interrumpió antes de completarse. Puedes reintentar.",
    codes.PROVIDER_ERROR: "El proveedor devolvió un error.",
    codes.INTERNAL_ERROR: "Error interno del asistente.",
}


def user_message(code: str) -> str:
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES[codes.PROVIDER_ERROR])
