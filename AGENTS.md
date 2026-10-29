# Instrucciones para agentes de programación

## Antes de modificar

Leer [README](README.md), [arquitectura](docs/architecture.md), [visión](docs/product/vision.md)
y la especificación asignada. La entrega activa propuesta es [SPEC-001](docs/specs/001-chat-texto.md).
Mobile no implementa esa entrega; su desarrollo se inicia con una especificación propia.
Verificar que los archivos externos sean accesibles. Para el trabajo local basta con leer los clones
vecinos. Si ya hay commits, anotar en la tarea la revisión de la especificación utilizada;
si todavía no los hay, indicar que se trabaja con un borrador local.
Si falta una fuente necesaria, indicarlo y obtenerla antes de adivinar requisitos.

## Implementación

- Respetar la arquitectura del componente y trabajar en cambios pequeños verificables.
- Distinguir requisitos acordados, propuestas e incertidumbres de integración.
- Consultar documentación oficial vigente antes de seleccionar versiones o implementar autenticación.
- No inventar comandos de ejecución ni afirmar pruebas no realizadas.
- No versionar secretos, tokens, datos personales, entornos locales ni bases de datos.
- La documentación fuente se versiona; no excluir docs mediante gitignore.
- No crear microservicios, gestores de estado o abstracciones adicionales sin una necesidad concreta.
- Actualizar comportamiento documentado y pruebas cuando cambie el alcance acordado.

## Cierre

Explicar cambios, comprobaciones realizadas, criterios pendientes y limitaciones.
No marcar tareas como completadas solo por generar código; contrastar los criterios de aceptación.
