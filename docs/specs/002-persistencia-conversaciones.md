# SPEC-002: persistencia de conversaciones

Estado: **en implementación**. Fecha: 2026-10-30. Componente: backend (desktop la consume después).
Etapa 2 de la [visión](../product/vision.md). Sustituye la regla de SPEC-001 de que el historial solo
existe durante la sesión.

## Objetivo

Las conversaciones, sus mensajes y sus respuestas se guardan en PostgreSQL y sobreviven a reinicios
del backend. El cliente puede listarlas, leer su historial, renombrarlas y borrarlas.

## Decisiones del usuario (2026-10-30)

| # | Decisión |
| --- | --- |
| P1 | PostgreSQL en local con Docker Compose (`postgres:18.6`), solo en `127.0.0.1` y con un volumen local. Es el mismo tipo de despliegue que usará Coolify. |
| P2 | Puntos de control durante el streaming: se guarda al aceptar el envío, como mucho una vez por segundo mientras llega texto, y siempre al terminar. |
| P3 | Endpoints nuevos: listar, ver historial, renombrar y borrar conversaciones. |
| P4 | Sin cifrado propio en v1. La base de datos solo escucha en `127.0.0.1`, la contraseña va en `.env` (fuera de Git) y la protección en reposo depende del disco (BitLocker). El cifrado propio se decide antes del VPS. |

## Propuestas del agente incluidas (revisables)

| # | Propuesta |
| --- | --- |
| Q1 | El título se deriva del primer mensaje (espacios colapsados, como mucho 60 caracteres, con `…` si se recorta), sin llamar al LLM. Renombrar fija un título propio de 1 a 120 caracteres, sin espacios en los extremos. Renombrar con `null` vuelve al título derivado. |
| Q2 | La lista se ordena por última actividad (`updated_at`: creación, envío de un mensaje o cambio de título), de la más reciente a la más antigua. Sin paginación en v1. |
| Q3 | Borrar es definitivo y elimina mensajes y respuestas. Si hay una respuesta en curso, responde `409 send_in_progress`: primero hay que cancelarla. |
| Q4 | Al arrancar el backend, las respuestas que hubieran quedado en `streaming` (caída del proceso) pasan a `interrupted`, o a `failed` si no tenían texto, con el código `interrupted`. Esto supone un único proceso del backend. |
| Q5 | Si la base de datos no responde, las peticiones fuera del stream reciben `503 storage_unavailable`. Si falla un punto de control durante el streaming, se registra en el log y el stream continúa. |
| Q6 | Sin `AI_ASSISTANT_DATABASE_URL`, el backend sigue funcionando en memoria y lo indica con `persisted: false` y en el mensaje de arranque. Las pruebas del núcleo no necesitan base de datos. |

## Comportamiento

- `POST /v1/conversations` devuelve `persisted: true` cuando hay base de datos.
- La garantía de un único envío activo por conversación pasa al almacenamiento: un índice único
  parcial impide dos respuestas en `streaming` en la misma conversación.
- El historial que se reenvía al proveedor no cambia: los intercambios `completed` y el mensaje
  actual. Sigue sin límite de longitud ni compactación.
- Leer una conversación con una respuesta en curso devuelve el texto del último punto de control.
- `cancel` responde `204` cuando el estado `cancelled` ya está guardado, para que un envío nuevo no
  choque con la respuesta anterior.

## Criterios de aceptación

| ID | Comprobación |
| --- | --- |
| PC-01 | Tras reiniciar el backend, una conversación con un intercambio completado se lista y su historial es idéntico. |
| PC-02 | Dos envíos simultáneos a la misma conversación: uno se acepta y el otro recibe `409`, también con dos copias distintas de la conversación cargadas desde la base de datos. |
| PC-03 | Una respuesta que quedó en `streaming` al caer el proceso aparece como `interrupted` (o `failed`) tras arrancar, y la conversación admite envíos. |
| PC-04 | Los puntos de control guardan el texto parcial durante el streaming; el texto final y el estado se guardan siempre. |
| PC-05 | Renombrar, volver al título derivado y borrar funcionan; borrar con una respuesta en curso da `409`; los recursos inexistentes, `404`. |
| PC-06 | El repositorio en memoria y el de PostgreSQL pasan las mismas pruebas de contrato del puerto. |
| PC-07 | Ni Git ni los logs contienen la contraseña de la base de datos ni el texto de las conversaciones. |
| PC-08 | Las reglas de arquitectura siguen cumpliéndose: dominio y aplicación no importan SQLAlchemy ni asyncpg. |

## Fuera de esta especificación

Identidad del propietario y de los dispositivos, búsqueda, paginación, exportación, retención
automática, idempotencia de envíos repetidos (`client_message_id`), cifrado propio y despliegue.

## Tareas

- [x] Modelo de datos y migración inicial ([esquema](../data/conversations-schema.md)).
- [x] Puerto de repositorio asíncrono, adaptador PostgreSQL y pruebas de contrato compartidas.
- [x] Casos de uso de historial, renombrar y borrar; recuperación al arrancar.
- [x] Endpoints en chat-v1 y pruebas HTTP.
- [x] Docker Compose, comandos de migración y documentación de desarrollo verificados.
- [x] Registrar qué criterios pasan y las limitaciones (abajo).
- [ ] Revisión del usuario de las propuestas Q1–Q6.

## Resultado (2026-10-30)

Entorno: Windows 11, Python 3.12, Docker 29.1.3, PostgreSQL 18.6, SQLAlchemy 2.1.6, asyncpg 0.32.0,
Alembic 1.20.1. Suite completa: 136 pruebas con PostgreSQL; sin `AI_ASSISTANT_TEST_DATABASE_URL`,
127 y 9 omitidas. Las pruebas asíncronas y de base de datos, repetidas 3 veces con el mismo resultado.

| ID | Resultado | Cómo |
| --- | --- | --- |
| PC-01 | Pasa | `test_http_postgres.py`: dos instancias del servidor sobre la misma base de datos; también a mano con `python -m …bootstrap.server`, curl y reinicio. |
| PC-02 | Pasa | `test_conversation_repository.py`: dos copias cargadas y dos `add_turn` simultáneos; uno recibe `SendInProgress`. |
| PC-03 | Pasa | Respuesta guardada en `streaming` → `interrupted` al arrancar el servidor, y la conversación admite envíos. |
| PC-04 | Pasa | Escrituras observadas con un repositorio espía: puntos de control en `streaming` y el final siempre. |
| PC-05 | Pasa | Pruebas HTTP de listar, leer, renombrar, volver al derivado, borrar, `409` y `404`. |
| PC-06 | Pasa | Las pruebas de contrato del repositorio se ejecutan sobre memoria y PostgreSQL. |
| PC-07 | Pasa | `.env` ignorado por Git; contraseña oculta en el arranque y en `migrate`; ausente de los logs de una ejecución manual. Los logs registran solo tipos de error. |
| PC-08 | Pasa | `test_architecture.py`: dominio y aplicación sin SQLAlchemy ni asyncpg. |

Limitaciones conocidas:

- Un único proceso del backend: la recuperación al arrancar cierra cualquier respuesta en `streaming`.
- Si falla la escritura final de una respuesta, la fila queda en `streaming` y la conversación
  rechaza envíos (`409`) hasta el siguiente arranque. Queda registrado en el log.
- Tras un corte del cliente sin cancelar, el estado `interrupted` se guarda en segundo plano; un envío
  inmediato a la misma conversación puede recibir `409` durante unos milisegundos.
- Sin paginación: la lista devuelve todas las conversaciones.
