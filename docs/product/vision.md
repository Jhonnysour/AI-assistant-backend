# Visión y alcance

Estado: borrador inicial basado en la conversación. Arquitecturas confirmadas; etapas propuestas.

## Propósito

Asistente personal para Windows y teléfono: conversar, consultar documentos y ejecutar tareas autorizadas.
El objetivo educativo es aprender integración de IA mediante funcionalidades pequeñas y evaluables.
Usuario inicial: el propietario del sistema. No se diseña todavía un SaaS multiusuario.

## Objetivos de aprendizaje

- RAG, embeddings, recuperación con referencias y evaluación de respuestas.
- Function calling, validación de herramientas y guardrails.
- VAD, STT, TTS y gestión de turnos de voz.
- Memoria, selección del contexto y compactación.
- Selección de modelos y fallback entre proveedores con presupuesto explícito.
- Tareas persistentes, notificaciones, permisos e idempotencia.
- Integración local con Windows y viabilidad de Wake-on-LAN.

## Decisiones acordadas

Tres repositorios: backend hexagonal, desktop MVVM con React/Electron y mobile Flutter con Clean Architecture.
Las especificaciones generales viven aquí; cada cliente conserva sus decisiones e instrucciones locales.
El móvil y desktop se comunican con una API. No acceden directamente a la base de datos del servidor.

## Secuencia propuesta

1. Validar acceso al proveedor y completar un chat de texto entre desktop y backend.
2. Persistir conversaciones y añadir recordatorios deterministas.
3. Desplegar backend y proceso de tareas; implementar el cliente móvil mínimo y sus notificaciones.
4. Incorporar documentos, RAG y evaluación de fuentes.
5. Añadir herramientas, estado de dispositivos, contexto y selección de proveedores.
6. Introducir voz y acciones locales; verificar Wake-on-LAN según hardware.

Las pruebas de recuperación, permisos y observabilidad acompañan a cada etapa.
La secuencia no impide adelantar una prueba de viabilidad cuando reduzca una incertidumbre.

## Fuera de la primera entrega

PDFs, audio, memoria duradera, ejecución de comandos, correo, calendario, encendido remoto,
notificaciones móviles, fallback automático de pago y tareas autónomas prolongadas.

## Reglas del producto propuestas

- La aplicación informa un resultado exitoso solo cuando la operación lo confirma.
- El contenido de documentos y resultados externos no concede permisos.
- Una tarea programada se almacena y ejecuta mediante código; no requiere un LLM esperando.
- Cambiar a otro proveedor respeta capacidades, privacidad y presupuesto configurados.
- Enviar una orden al PC no equivale a confirmar su ejecución.
- El usuario puede revisar las acciones, errores y datos persistidos de memoria.

## Incertidumbres abiertas

- Elegibilidad y capacidades reales de la cuenta en el flujo oficial de autenticación elegido.
- Modelos autorizados, límites y funcionamiento de renovación de credenciales.
- Android o iOS para el primer cliente móvil.
- Hardware, red y dispositivo de casa disponibles para Wake-on-LAN.
- Rendimiento local para voz; proveedor y coste si se utiliza un servicio externo.
- Nombre definitivo, biblioteca de estado móvil y versiones de dependencias.

Estas incógnitas no se convierten en garantías. Cada prueba debe registrar fecha, entorno, resultado
y decisión, sin publicar tokens ni datos personales.
