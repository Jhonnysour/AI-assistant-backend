# SPEC-001: primera conversación desde escritorio

Estado: propuesta para revisión. Fecha: 2026-10-29.
Componentes: backend y desktop. Mobile no participa en esta implementación.

## Objetivo

El usuario escribe un mensaje en Electron y recibe una respuesta progresiva de un proveedor
validado a través del backend. La aplicación distingue actividad, finalización, cancelación y error.

## Paso previo: prueba de viabilidad

Consultar la documentación oficial vigente del proveedor antes de implementar autenticación.
Probar el flujo autorizado para usar la cuenta del usuario, consultar el catálogo y obtener una respuesta.
No asumir que todos los modelos del producto web están expuestos a la integración.
No reutilizar cookies ni extraer credenciales de extensiones.
Registrar el resultado y los límites encontrados en la
[evidencia](../evidence/001-viabilidad-proveedor.md). La prueba está implementada
([desarrollo](../development.md#prueba-real-con-la-cuenta-manual)) y se ejecutó con éxito el 2026-10-29.
Si la cuenta no es elegible, documentar el bloqueo y acordar la alternativa de facturación:
no cambiar automáticamente a una API de pago. El desarrollo con un proveedor simulado puede continuar.

## Comportamiento

- Una conversación temporal y un envío activo por conversación.
- El selector muestra los modelos que ofrece el catálogo y distingue los de acceso comprobado (una
  petición terminada en `response.completed`). El catálogo por sí solo no garantiza acceso.
- Se rechazan mensajes vacíos sin llamar al proveedor.
- Tras enviar: el usuario ve su mensaje y un indicador de actividad.
- Los fragmentos recibidos actualizan la respuesta sin bloquear la interfaz.
- Solo el evento de finalización confirma una respuesta completa.
- Un error conserva el texto del usuario y presenta una explicación sin secretos técnicos.
- Cancelar detiene el consumo local del flujo y solicita cancelar el trabajo upstream si es compatible.
  No se promete que la cancelación elimine todo consumo ya generado.
- Si queda texto parcial, se marca como interrumpido o cancelado.
- Sin reintento automático de una generación parcialmente recibida.
- El historial solo existe durante la sesión; la UI debe comunicar que no se persiste todavía.
  **Sustituido por [SPEC-002](002-persistencia-conversaciones.md) (2026-10-30)**: las conversaciones se
  guardan en PostgreSQL. La UI solo avisa si el backend responde `persisted: false`.

## Criterios de aceptación

| ID | Comprobación |
| --- | --- |
| AC-01 | Entrada vacía: no se invoca el proveedor y se muestra validación. |
| AC-02 | Flujo simulado: se renderizan fragmentos y después se muestra estado completado. |
| AC-03 | Flujo real: una cuenta y modelo autorizados completan una respuesta; evidencia sin secretos. |
| AC-04 | Proveedor no disponible: termina la actividad y se muestra un error recuperable. |
| AC-05 | Corte a mitad de respuesta: se conserva el parcial y no se presenta como completo. |
| AC-06 | Cancelación: vuelve a permitirse una nueva interacción y el parcial queda identificado. |
| AC-07 | Un envío activo: doble clic no inicia dos solicitudes. |
| AC-08 | Renderer, Git y logs no contienen credenciales del proveedor. |
| AC-09 | Núcleo del backend puede probarse sin FastAPI ni el SDK real. |
| AC-10 | La interfaz no expone acceso general a Node.js ni ejecución arbitraria de comandos. |

## Plan propuesto y tareas

- [x] Revisar la especificación y registrar decisiones pendientes relevantes (2026-10-29, en la evidencia).
- [x] Ejecutar la prueba de autenticación y catálogo; registrar resultado (2026-10-29: `completed`
  con `gpt-6-luna`; ver la evidencia).
- [x] Definir contrato de chat, streaming, error y cancelación; elegir transporte y formato
  ([chat-v1](../api/chat-v1.md), SSE, 2026-10-29).
- [x] Crear esqueleto hexagonal y proveedor simulado.
- [x] Implementar el caso de uso y pruebas de AC-01, AC-04 a AC-07 y AC-09. Cubierto en el núcleo del
  backend; la parte visible de esos criterios se comprueba con desktop.
- [x] Acordar el [modelo del chat](../domain/conversations.md) y alinear núcleo, contrato y pruebas
  (2026-10-29): núcleo asíncrono, cancelación explícita inmediata, fechas y modelos por respuesta.
- [x] Exponer el contrato con FastAPI (SSE) usando el proveedor simulado o el real según configuración
  (2026-10-29, FastAPI 0.162.0 y uvicorn 0.54.0; probado con el proveedor simulado).
- [ ] Crear Electron + React con vista, ViewModel y cliente de API.
- [ ] Integrar streaming simulado y comprobar estados visibles.
- [ ] Añadir el proveedor validado y ejecutar AC-03.
- [ ] Revisar aislamiento y credenciales; documentar comandos reales de desarrollo y pruebas.
  Backend (2026-10-30): validación de `Host` contra *DNS rebinding* y CORS configurable, desactivado
  por defecto. Falta la parte de desktop.
- [ ] Registrar qué criterios pasan, cuáles no y cualquier limitación.

## Evidencia de cierre

Anotar comandos ejecutados, entorno, resultado de cada criterio y referencia a commits/PRs de ambos repos.
No marcar esta entrega completa con pruebas simuladas únicamente si falta AC-03.
No exige publicar el backend ni empaquetar un instalador de producción.
