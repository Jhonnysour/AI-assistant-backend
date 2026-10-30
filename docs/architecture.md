# Backend: arquitectura hexagonal

Estado: decisión inicial. Stack propuesto: Python, FastAPI y PostgreSQL.

## Responsabilidades

Coordinar conversaciones, proveedores, herramientas, contexto y tareas persistentes.
Las acciones que requieren Windows se delegan a un ejecutor autenticado en desktop.

## Capas y reglas de dependencia

Arquitectura hexagonal (puertos y adaptadores) aplicada de forma estricta, con tres capas globales
(decisión del usuario, 2026-10-30). Dentro de cada capa, el código se agrupa por capacidad:
conversaciones y, cuando lleguen, recordatorios, documentos y dispositivos.

| Capa | Contiene | Puede importar |
| --- | --- | --- |
| `domain/` | Entidades, valores, estados, reglas y errores de negocio. | Solo la biblioteca estándar. |
| `application/ports/inbound/` | Casos de uso que ofrece la aplicación (`Protocol`), con sus datos de entrada y salida y sus errores. | Dominio. |
| `application/ports/outbound/` | Lo que la aplicación necesita del exterior (`Protocol`): proveedor de LLM, catálogo, repositorio, reloj. | Dominio. |
| `application/use_cases/` | Implementación de los puertos de entrada usando los de salida. | Dominio y puertos. |
| `infrastructure/adapters/inbound/` | Traducen peticiones externas (HTTP) a puertos de entrada. | Solo puertos de entrada y `error_codes`. |
| `infrastructure/adapters/outbound/` | Implementan los puertos de salida (OpenAI, memoria, reloj del sistema). | Dominio, puertos de salida y soporte técnico de `infrastructure/`. |
| `infrastructure/bootstrap/` | Raíz de composición: elige adaptadores y los inyecta en los casos de uso. | Todo. Es el único módulo que conoce los casos de uso concretos. |
| `infrastructure/` (resto) | Configuración del entorno, clientes HTTP y almacén cifrado; herramientas operativas (`tools/`). | Infraestructura. |

- Dominio y aplicación no importan FastAPI, Pydantic, httpx, el ORM ni SDKs de proveedores.
- `application/error_codes.py` es vocabulario de la aplicación: los adaptadores de salida traducen a
  esos códigos y los de entrada los exponen.
- Los errores del dominio que un caso de uso puede lanzar se reexportan en su puerto de entrada, para
  que los adaptadores de entrada no dependan del dominio directamente.
- `tools/probe.py` es una herramienta operativa del adaptador `chatgpt_plan` (login, estado, catálogo,
  prueba de envío). Usa ese adaptador directamente y no pasa por los casos de uso, porque lo que
  prueba es el propio adaptador.
- [tests/test_architecture.py](../tests/test_architecture.py) analiza los imports de cada módulo y
  hace fallar las pruebas si se rompe alguna de estas reglas (incluye AC-09).

La ejecución programada puede ser otra raíz de composición del mismo proyecto; no implica
microservicios.

## Primera entrega

Un caso de uso para conversar, un puerto de proveedor y un adaptador inicialmente validado.
Proveedor inicial: OpenAI mediante Sign in with ChatGPT (uso del plan). Su viabilidad se comprobó el
2026-10-29 ([evidencia](evidence/001-viabilidad-proveedor.md)).
Streaming con eventos de progreso, finalización y error. Credenciales fuera de la UI y de los logs.
El contrato HTTP se define antes de integrar desktop y se versiona en este repositorio.
Persistencia en PostgreSQL desde [SPEC-002](specs/002-persistencia-conversaciones.md), con el
[esquema](data/conversations-schema.md) gestionado por migraciones (Alembic).

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
  domain/conversations/model.py              # Conversation, mensajes, respuestas, ModelOption, estados y reglas
  application/
    error_codes.py                           # códigos de error independientes del proveedor
    ports/inbound/conversations.py           # StartConversation, SendMessage, CancelResponse, ListModels,
                                             # ResponseStream, eventos del flujo y errores
    ports/outbound/chat_provider.py          # ChatProvider (async) y eventos del proveedor
    ports/outbound/model_catalog.py          # ModelCatalog, CatalogUnavailable
    ports/outbound/conversation_repository.py  # async; escrituras por partes; ConversationHeader
    ports/outbound/clock.py                  # Clock
    use_cases/conversations/chat.py          # ChatService/Turn: crear, enviar, flujo de eventos y cancelación
    use_cases/conversations/list_models.py   # ListModelsService
    use_cases/conversations/history.py       # listar, leer, renombrar y borrar conversaciones
    use_cases/conversations/recovery.py      # al arrancar, cierra respuestas que quedaron en curso
    use_cases/conversations/messages.py      # mensajes al usuario por código de error
    errors.py                                # StorageUnavailable
  infrastructure/
    config.py                                # lectura del entorno
    http_client.py                           # clientes httpx (síncrono y asíncrono) con proxy explícito
    secret_store.py                          # archivo cifrado (DPAPI en Windows)
    adapters/inbound/http/chat_v1.py         # FastAPI: endpoints chat-v1, SSE y formato de error
    adapters/outbound/llm/fake.py            # proveedor y catálogo simulados
    adapters/outbound/llm/chatgpt_plan/      # proveedor real: oauth, credentials, session, responses, provider
    adapters/outbound/persistence/in_memory.py  # sin base de datos (pruebas, uso sin PostgreSQL)
    adapters/outbound/persistence/postgres/  # tables.py (SQLAlchemy Core), repository.py, schema.py, migrations/
    adapters/outbound/system_clock.py        # reloj del sistema (UTC)
    bootstrap/server.py                      # composición del servidor HTTP, uvicorn en 127.0.0.1
    tools/probe.py                           # CLI de la prueba de viabilidad del proveedor
    tools/migrate.py                         # aplica las migraciones de PostgreSQL
```

- El contrato hacia los clientes está en [api/chat-v1.md](api/chat-v1.md) y usa SSE.
- El modelo del chat (entidades, estados e invariantes) está acordado en
  [domain/conversations.md](domain/conversations.md).
- El núcleo es **asíncrono** (asyncio, de la biblioteca estándar). El caso de uso consume el proveedor
  en una tarea propia por turno; cancelar marca `cancelled` y libera la conversación en el acto,
  interrumpe la espera de red y descarta lo que llegue después
  ([sección 8 del modelo](domain/conversations.md#8-cancelación)).
- El caso de uso no reintenta. "Un envío activo" se comprueba y reserva en el dominio sin ningún
  `await` en medio; entre peticiones con copias distintas lo garantiza el repositorio.
- El adaptador real hace el streaming con `httpx.AsyncClient`. La renovación de tokens reutiliza el
  flujo OAuth síncrono de la CLI en un hilo aparte (`asyncio.to_thread`).
- Los fallos propios del backend se registran con `logging` (solo tipo y líneas de código, sin
  mensajes, tokens ni texto del usuario) y se comunican como `internal_error`.
- El adaptador HTTP usa el SSE propio de FastAPI (`EventSourceResponse`). El envío se reserva en una
  dependencia, de modo que `400`/`404`/`409` son respuestas HTTP normales, y esa dependencia cierra
  el turno al terminar la petición para no dejar la conversación bloqueada. Recibe los casos de uso
  como puertos de entrada (`ConversationUseCases`) y no conoce `ChatService`.
- `ChatService` implementa `StartConversation`, `SendMessage` y `CancelResponse`; `ListModelsService`
  implementa `ListModels`. El proveedor por defecto es el simulado; `AI_ASSISTANT_PROVIDER=chatgpt_plan`
  activa el real. Ambas decisiones se toman en `bootstrap/server.py`.
- `ConversationRepository` es asíncrono y escribe por partes: aceptar un envío, guardar el estado de
  una respuesta (puntos de control como mucho una vez por segundo y siempre el final) y guardar el
  título. Cada escritura de la respuesta va en una tarea propia, ordenada por un cerrojo, para que
  cancelar el consumo del proveedor no corte una escritura a medias.
- Con PostgreSQL cada petición trabaja con su propia copia de la conversación, y la garantía de un
  envío activo (I1) la da un índice único parcial. Se supone un único proceso del backend: al
  arrancar, las respuestas en `streaming` se cierran como interrumpidas.

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
