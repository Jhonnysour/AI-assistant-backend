# AI Assistant Backend

Backend propuesto en Python y FastAPI, con arquitectura hexagonal y PostgreSQL.
Estado: la prueba de viabilidad del proveedor se superó el 2026-10-29. El núcleo de chat ya existe
(dominio, caso de uso asíncrono, proveedor simulado y proveedor real), el modelo del chat y el
contrato SSE están acordados y la API HTTP (FastAPI) los expone en local. No hay base de datos ni
despliegue.

## Lectura inicial

1. [Visión y alcance](docs/product/vision.md)
2. [Arquitectura](docs/architecture.md)
3. [Primera especificación](docs/specs/001-chat-texto.md)
4. [Instrucciones de trabajo](AGENTS.md)
5. [Desarrollo local, proxy y comandos](docs/development.md)
6. [Evidencia de la prueba del proveedor](docs/evidence/001-viabilidad-proveedor.md)
7. [Contrato de chat v1 (SSE)](docs/api/chat-v1.md)
8. [Modelo del chat de SPEC-001](docs/domain/conversations.md)

Este repositorio alberga también la documentación compartida del producto.
La primera entrega se desarrolla localmente; Coolify se incorpora cuando exista un flujo verificable.
Los comandos de ejecución y pruebas están en [docs/development.md](docs/development.md).
