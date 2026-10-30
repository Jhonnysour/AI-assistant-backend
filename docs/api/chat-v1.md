# Contrato de chat v1

Estado: **acordado** el 2026-10-29 para [SPEC-001](../specs/001-chat-texto.md), junto con el
[modelo del chat](../domain/conversations.md). El usuario decidió SSE y revisó el resto, que propuso el
agente. Implementado con FastAPI en
[adapters/inbound/http/chat_v1.py](../../src/ai_assistant/infrastructure/adapters/inbound/http/chat_v1.py); cómo
arrancarlo, en [desarrollo](../development.md#servidor-http-contrato-chat-v1). Los cambios
incompatibles requieren `/v2`.

## Decisiones

- **Transporte: SSE** (Server-Sent Events) sobre la respuesta de un `POST`. Se eligió frente a
  WebSocket porque el flujo es unidireccional, coincide con el del proveedor y Electron lo consume
  con `fetch`.
- **Cancelación explícita** con su propio endpoint, para distinguir "el usuario canceló" de "se cortó
  la conexión".
- La conversación es **temporal**: vive en memoria del proceso y se pierde al reiniciarlo. La UI debe
  indicarlo.
- **Autenticación:** ninguna mientras el backend escuche solo en `127.0.0.1`. Es obligatoria antes de
  exponerlo en el VPS (diseño pendiente).
- Las credenciales del proveedor nunca salen del backend.

## Convenciones

- JSON en UTF-8. Los identificadores son cadenas opacas.
- Las fechas son UTC en ISO 8601 con `Z` (`2026-10-29T14:03:00Z`), asignadas por el backend.
- Los errores fuera del stream responden con `{"error": {"code": "...", "message": "..."}}`. El
  `message` está en español, se puede mostrar al usuario y no contiene detalles técnicos ni secretos.
  Un fallo inesperado en cualquier endpoint responde `500` con `internal_error`.

## Endpoints

### `POST /v1/conversations`

Crea una conversación temporal.
`201` → `{"conversation_id": "…", "created_at": "…", "persisted": false}`

### `GET /v1/models`

Devuelve el catálogo del proveedor y distingue el acceso comprobado.

`200` →
```json
{"models": [
  {"id": "gpt-6-luna", "display_name": "GPT-6-Luna", "access": "verified"},
  {"id": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "access": "unverified"}
]}
```

- `id` es el slug que se envía como `model`; `display_name` es solo para mostrar.
- `access: "verified"` significa que una petición con ese modelo terminó en `response.completed`,
  desde la CLI de prueba o desde el chat. `unverified` significa que se ofrece, pero no está
  comprobado: puede fallar con `model_unavailable`.

Errores:

| HTTP | `code` | Qué hace el cliente |
| --- | --- | --- |
| 401 | `auth_required` | Avisa de que hay que volver a iniciar sesión en el backend. |
| 403 | `not_eligible` | Avisa de que la cuenta no lo permite; reintentar no sirve. |
| 503 | `usage_limit` | Avisa del límite de uso; intentarlo más tarde. |
| 503 | `provider_unavailable` | Avisa de que no se pudo contactar; ofrece reintentar. |
| 502 | `provider_error` | Error genérico. |
| 500 | `internal_error` | Error genérico. |

### `POST /v1/conversations/{conversation_id}/messages`

Petición: `{"model": "gpt-6-luna", "text": "Hola"}`. El texto se guarda y se envía tal cual, sin
recortar espacios. No hay límite de longitud en v1.

Errores **antes** del stream; en todos ellos no se llama al proveedor:

| HTTP | `code` | Caso |
| --- | --- | --- |
| 400 | `empty_message` | Texto vacío o solo espacios (AC-01). |
| 400 | `invalid_request` | Falta `model` o el cuerpo no es válido. |
| 404 | `conversation_not_found` | La conversación no existe (p. ej. tras reiniciar el backend). |
| 409 | `send_in_progress` | Ya hay una respuesta en curso en la conversación (AC-07). |

El modelo no se valida contra el catálogo: si no existe, llega `response.failed` con `model_unavailable`.

Si la petición es aceptada: `200`, `Content-Type: text/event-stream`.

### `POST /v1/conversations/{conversation_id}/responses/{response_id}/cancel`

Cancela una respuesta en curso. Sin cuerpo.

| HTTP | `code` | Caso |
| --- | --- | --- |
| 204 | — | Cancelada. La conversación ya admite un envío nuevo. |
| 404 | `conversation_not_found` / `response_not_found` | No existe. |
| 409 | `response_not_active` | La respuesta ya había terminado; conserva su estado final. |

## Eventos SSE

Formato: `event: <nombre>` y `data: <json>`, seguidos de una línea en blanco. Las líneas que
empiezan por `:` son de mantenimiento de la conexión y el cliente las ignora: el backend envía
`: ping` tras 15 s sin eventos (por ejemplo, mientras espera al proveedor). La respuesta lleva
`Cache-Control: no-cache` y `X-Accel-Buffering: no`.

| Evento | `data` | Significado |
| --- | --- | --- |
| `message.accepted` | `{"message_id", "response_id", "created_at", "model_requested"}` | Primer evento. El mensaje del usuario está registrado. |
| `response.delta` | `{"response_id", "text"}` | Fragmento que se añade a la respuesta. |
| `response.completed` | `{"response_id", "finished_at", "model_reported"}` | **Único evento que confirma una respuesta completa.** |
| `response.failed` | `{"response_id", "status", "finished_at", "model_reported", "error": {"code", "message", "retryable"}}` | Fin con error. `status` vale `failed` (sin texto) o `interrupted` (con texto parcial). |
| `response.cancelled` | `{"response_id", "finished_at"}` | Fin por cancelación del usuario. El parcial recibido se conserva. |

`model_reported` es el modelo que el proveedor declara haber usado, o `null` si no lo informó.

Ejemplo:

```
event: message.accepted
data: {"message_id":"9f…","response_id":"a1…","created_at":"2026-10-29T14:03:00Z","model_requested":"gpt-6-luna"}

event: response.delta
data: {"response_id":"a1…","text":"Hola, "}

event: response.delta
data: {"response_id":"a1…","text":"mundo"}

event: response.completed
data: {"response_id":"a1…","finished_at":"2026-10-29T14:03:02Z","model_reported":"gpt-6-luna"}

```

Reglas para el cliente:

- Tras `message.accepted` llega exactamente un evento final: `response.completed`, `response.failed`
  o `response.cancelled`.
- Si la conexión se cierra sin evento final, el cliente marca la respuesta como interrumpida y conserva
  el parcial (AC-05). El backend la registra igual (`interrupted`, o `failed` si no hubo texto).
- `retryable: true` solo indica que el usuario puede reintentar a mano. **Ni el cliente ni el
  backend reintentan automáticamente.** Reintentar es enviar un mensaje nuevo con el mismo texto.
- Hay un único envío activo por conversación: el cliente deshabilita el envío hasta el evento final
  (AC-07). El backend lo garantiza igualmente con `409`.

## Cancelación

1. El cliente llama a `POST …/responses/{response_id}/cancel`.
2. El backend marca la respuesta `cancelled` y libera la conversación de inmediato, sin esperar al
   proveedor; después cierra la conexión con él. Lo que el proveedor envíe después se descarta.
3. Si el stream SSE sigue abierto, recibe `response.cancelled` y se cierra. El cliente puede también
   abortar la petición del stream después de llamar a cancel.
4. El cliente marca el parcial como cancelado y vuelve a permitir enviar (AC-06).

Abortar el stream **sin** llamar a cancel no es una cancelación: el backend lo registra como corte
(`interrupted`). El consumo que el proveedor ya haya generado puede contabilizarse igualmente.

## Códigos de error del proveedor

Son independientes de OpenAI; el adaptador traduce los códigos propios del proveedor.

| `code` | `retryable` | Mensaje al usuario |
| --- | --- | --- |
| `auth_required` | no | El backend necesita volver a iniciar sesión con el proveedor. |
| `not_eligible` | no | La cuenta del proveedor no permite esta operación. |
| `usage_limit` | no | Se alcanzó el límite de uso del proveedor. Inténtalo más tarde. |
| `model_unavailable` | no | El modelo elegido no está disponible para esta cuenta. |
| `provider_unavailable` | sí | No se pudo contactar con el proveedor. Puedes reintentar. |
| `incomplete` | sí | El proveedor terminó la respuesta sin completarla. Puedes reintentar. |
| `interrupted` | sí | La respuesta se interrumpió antes de completarse. Puedes reintentar. |
| `provider_error` | no | El proveedor devolvió un error. |
| `internal_error` | no | Error interno del asistente. |

Los mensajes viven en [messages.py](../../src/ai_assistant/application/use_cases/conversations/messages.py).

## Contexto enviado al proveedor

El backend envía el historial completo en cada turno, porque el proveedor no guarda estado
(`store: false`). Solo se incluyen los intercambios completados y el mensaje actual. Las respuestas
fallidas, interrumpidas o canceladas, y sus preguntas, no se reenvían.

## Fuera de v1

Persistencia, listado de conversaciones o historial desde el servidor, autenticación de la API,
adjuntos y límite de longitud del mensaje.
