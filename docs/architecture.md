# Backend: arquitectura hexagonal

Estado: decisión inicial. Stack propuesto: Python, FastAPI y PostgreSQL.

## Responsabilidades

Coordinar conversaciones, proveedores, herramientas, contexto y tareas persistentes.
Las acciones que requieren Windows se delegan a un ejecutor autenticado en desktop.

## Fronteras

- Dominio: entidades, estados y reglas independientes del framework y la infraestructura.
- Aplicación: casos de uso y puertos que expresan las dependencias necesarias.
- Adaptadores de entrada: HTTP, conexión de dispositivos y disparadores de tareas.
- Adaptadores de salida: LLM, persistencia, notificaciones, correo y calendario.
- Composición: configura dependencias e inyecta adaptadores en los casos de uso.

Dominio y aplicación no importan FastAPI, el ORM ni SDKs de proveedores. Los adaptadores
dependen de los contratos del núcleo. Los módulos se agrupan por capacidad: conversaciones,
recordatorios, documentos y dispositivos; se crean conforme a las entregas.
La ejecución programada puede ser otro proceso del mismo proyecto; no implica microservicios.

## Primera entrega

Un caso de uso para conversar, un puerto de proveedor y un adaptador inicialmente validado.
Proveedor inicial: OpenAI mediante Sign in with ChatGPT (uso del plan). Su viabilidad se comprobó el
2026-10-29 ([evidencia](evidence/001-viabilidad-proveedor.md)).
Streaming con eventos de progreso, finalización y error. Credenciales fuera de la UI y de los logs.
El contrato HTTP se define antes de integrar desktop y se versiona en este repositorio.
No hace falta una base de datos hasta implementar persistencia; la sesión inicial puede ser temporal.

## Despliegue y red

- Desktop y mobile se comunican solo con la API de este backend. No llaman al proveedor ni a la base
  de datos, y nunca reciben credenciales del proveedor.
- Una vez desplegado en el VPS (Ubuntu con Coolify), es el backend quien llama al proveedor desde el
  VPS. En desarrollo las llamadas salen desde el PC a través del proxy local.
- Los ajustes de red (proxy, `NO_PROXY`, puerto del callback OAuth) son configuración
  ([config.py](../src/ai_assistant/config.py)) e infraestructura
  ([infrastructure/http.py](../src/ai_assistant/infrastructure/http.py)). Los adaptadores reciben un
  cliente HTTP ya configurado; dominio y aplicación no conocen proxies ni URLs.
- Los secretos del proveedor se guardan cifrados en el host que los usa
  ([desarrollo](development.md#datos-locales-y-credenciales)). Las condiciones pendientes para el VPS
  están en la [evidencia](evidence/001-viabilidad-proveedor.md#despliegue-en-el-vps-pendiente).

## Modelos: ofrecidos frente a comprobados

- **Ofrecido:** aparece en el catálogo del proveedor (`GET /v1/models`, `visibility == "list"`). No
  garantiza acceso.
- **Acceso comprobado:** una petición con esa cuenta y ese modelo terminó en `response.completed`. Se
  registra por registro de cliente y con fecha. `failed`, `incomplete` o un corte no cuentan.

## Código actual

```
src/ai_assistant/
  config.py                          # composición: lectura del entorno
  server.py                          # composición del servidor HTTP: elige proveedor, uvicorn en 127.0.0.1
  infrastructure/http.py             # clientes httpx (síncrono y asíncrono) con proxy explícito
  infrastructure/secret_store.py     # archivo cifrado (DPAPI en Windows)
  conversations/
    domain/model.py                  # Conversation, UserMessage, AssistantResponse, estados y reglas
    application/ports.py             # ChatProvider (async), ConversationRepository, eventos del proveedor
    application/chat.py              # ChatService/Turn: enviar, flujo de eventos y cancelación
    adapters/inbound/http.py         # FastAPI: endpoints chat-v1, SSE y formato de error
    adapters/outbound/
      in_memory_repository.py        # sesión temporal
      fake_provider.py               # proveedor simulado (pruebas y desarrollo de clientes)
      chatgpt_plan/                  # proveedor real: oauth, credentials, session, responses, provider
  probe.py                           # CLI de la prueba de viabilidad
```

- El contrato hacia los clientes está en [api/chat-v1.md](api/chat-v1.md) y usa SSE.
- El modelo del chat (entidades, estados e invariantes) está acordado en
  [domain/conversations.md](domain/conversations.md).
- El núcleo es **asíncrono** (asyncio, de la biblioteca estándar). El caso de uso consume el proveedor
  en una tarea propia por turno; cancelar marca `cancelled` y libera la conversación en el acto,
  interrumpe la espera de red y descarta lo que llegue después
  ([sección 8 del modelo](domain/conversations.md#8-cancelación)).
- El caso de uso no reintenta. "Un envío activo" es atómico porque el servicio corre en un único
  bucle asyncio; con persistencia, esa garantía pasará al repositorio.
- El adaptador real hace el streaming con `httpx.AsyncClient`. La renovación de tokens reutiliza el
  flujo OAuth síncrono de la CLI en un hilo aparte (`asyncio.to_thread`).
- Los fallos propios del backend se registran con `logging` (solo tipo y líneas de código, sin
  mensajes, tokens ni texto del usuario) y se comunican como `internal_error`.
- El adaptador HTTP usa el SSE propio de FastAPI (`EventSourceResponse`). El envío se reserva en una
  dependencia, de modo que `400`/`404`/`409` son respuestas HTTP normales, y esa dependencia cierra
  el turno al terminar la petición para no dejar la conversación bloqueada. El proveedor por defecto
  es el simulado; `AI_ASSISTANT_PROVIDER=chatgpt_plan` activa el real.

## Evolución

Conservar historial normalizado, estado de tareas y políticas fuera del proveedor.
Añadir adaptadores alternativos sin asumir compatibilidad total de modelos o herramientas.
Validar argumentos, autorización y resultado de cada herramienta; límites de pasos y duración.
En tareas con efectos externos: identidad de operación, reintentos limitados y consulta de estado.
No reejecutar un envío por el simple hecho de que una respuesta del LLM se interrumpió.

## Pruebas

Probar casos de uso con proveedores y repositorios falsos; contratos con integración real acotada.
Evaluar modelos con escenarios y criterios de calidad, no comparando toda respuesta libre palabra por palabra.
El API externo de la aplicación necesita autenticación antes de exponerse en el VPS.
