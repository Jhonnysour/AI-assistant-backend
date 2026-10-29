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

## Código actual (prueba de viabilidad)

```
src/ai_assistant/
  config.py                         # composición: lectura del entorno
  infrastructure/http.py            # cliente httpx con proxy explícito
  infrastructure/secret_store.py    # archivo cifrado (DPAPI en Windows)
  conversations/adapters/outbound/chatgpt_plan/
    oauth.py  credentials.py  responses.py
  probe.py                          # CLI temporal: raíz de composición de la prueba
```

Todavía no hay dominio, puerto ni caso de uso. Se crearán junto con el contrato de streaming y el
proveedor simulado. El adaptador está aislado para implementar ese puerto sin reescribirse.

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
