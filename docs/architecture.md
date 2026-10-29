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
Streaming con eventos de progreso, finalización y error. Credenciales fuera de la UI y de los logs.
El contrato HTTP se define antes de integrar desktop y se versiona en este repositorio.
No hace falta una base de datos hasta implementar persistencia; la sesión inicial puede ser temporal.

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
