# SPEC-003: proveedor OpenRouter (DeepSeek V4.1 Flash)

Estado: **implementado** el 2026-10-30. Componente: backend. No cambia el contrato chat-v1 salvo dos
códigos de error nuevos.

## Objetivo

Usar modelos de OpenRouter con la API key del usuario, empezando por
`deepseek/deepseek-v4.1-flash-1017`, como un proveedor más que se elige en el `.env`.

## Decisiones

| # | Decisión | Origen |
| --- | --- | --- |
| O1 | OpenRouter es un adaptador de salida más (`openrouter`), elegido con `AI_ASSISTANT_PROVIDER`. Es de pago por uso: lo activa el usuario explícitamente; nunca es una alternativa automática. | Usuario |
| O2 | Modelo inicial `deepseek/deepseek-v4.1-flash-1017`. El selector solo muestra los modelos de `AI_ASSISTANT_OPENROUTER_MODELS` que OpenRouter ofrece (su catálogo tiene cientos). | Usuario (modelo); agente (lista) |
| O3 | La API key se guarda **cifrada con DPAPI** en `%LOCALAPPDATA%\ai-assistant\openrouter\api-key.dpapi` mediante `tools.openrouter set-key`; nunca en el `.env`, en Git, en logs ni en la salida de la CLI. En el VPS irá como secreto de Coolify (pendiente, igual que las credenciales de ChatGPT). | Agente, coherente con "fuera de Git no equivale a cifrado" |
| O4 | Chat Completions con streaming. La respuesta se confirma con el primer `finish_reason: stop`; el fragmento posterior con `usage` es contabilidad. Un error dentro de un stream `200` es un fallo, no una respuesta completa. | Documentación de OpenRouter |
| O5 | Códigos nuevos: `insufficient_credits` (402, no reintentable) y `rate_limited` (429, reintentable a mano). El resto se traduce a los existentes. | Agente |
| O6 | Las llamadas usan la misma configuración de proxy que el resto (`HTTPS_PROXY`). | Agente |
| O7 | El consumo de tokens y el coste que informa OpenRouter no se guardan todavía: llegan con la especificación de presupuesto (etapa 5 de la visión). | Agente |

Traducción de errores (adaptador `openrouter/provider.py`):

| OpenRouter | Código | Reintentable |
| --- | --- | --- |
| 401 key inválida o desactivada | `auth_required` | no |
| 402 sin créditos | `insufficient_credits` | no |
| 403 límite de gasto, key bloqueada o moderación | `not_eligible` | no |
| 404 modelo desconocido o sin proveedor | `model_unavailable` | no |
| 408, 5xx (502 no se cobra; 503 sin proveedor disponible) | `provider_unavailable` | sí |
| 429 | `rate_limited` | sí |
| 400 y otros | `provider_error` | no |
| `finish_reason: length` | `incomplete` | sí |
| `content_filter`, `tool_calls`, `error` sin objeto de error | `provider_error` | no |
| Fin del stream o corte de red sin `finish_reason` | `interrupted` (con texto) o `provider_unavailable` | sí |

## Limitaciones

- **Cancelar no siempre detiene el cobro.** Según OpenRouter, cortar el stream detiene el
  procesamiento y la facturación solo con algunos proveedores (OpenAI, Azure, Anthropic, entre
  otros); con otros (Bedrock, Groq, Google…) se cobra la respuesta completa. No se ha comprobado
  para el proveedor que sirve DeepSeek V4.1 Flash.
- El razonamiento que el modelo pueda emitir (`delta.reasoning`) no se muestra ni se guarda.
- Un único proveedor por proceso: cambiarlo requiere reiniciar el backend.

## Resultado (2026-10-30)

- 164 pruebas automáticas, 24 de ellas del adaptador con transporte simulado (sin red ni créditos):
  streaming, fragmento de contabilidad, errores HTTP, error dentro de un stream `200`, otros
  `finish_reason`, cortes, cierre de la conexión al cancelar, catálogo, key cifrada y CLI que no
  la imprime.
- Prueba real a través del servidor (proxy local, PostgreSQL): `GET /v1/models` mostró el modelo;
  un mensaje terminó en `response.completed` con `model_reported:
  deepseek/deepseek-v4.1-flash-1017`, guardado con `provider: openrouter`, y el modelo quedó con
  acceso comprobado. La key no apareció en el log. Se borró la conversación de prueba.
