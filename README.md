# AI Assistant Backend

Backend propuesto en Python y FastAPI, con arquitectura hexagonal y PostgreSQL.
Estado: prueba de viabilidad del proveedor superada el 2026-10-29 (login, catálogo y `response.completed`
con la cuenta real). Todavía no hay API HTTP, FastAPI ni base de datos.

## Lectura inicial

1. [Visión y alcance](docs/product/vision.md)
2. [Arquitectura](docs/architecture.md)
3. [Primera especificación](docs/specs/001-chat-texto.md)
4. [Instrucciones de trabajo](AGENTS.md)
5. [Desarrollo local, proxy y comandos](docs/development.md)
6. [Evidencia de la prueba del proveedor](docs/evidence/001-viabilidad-proveedor.md)

Este repositorio alberga también la documentación compartida del producto.
La primera entrega se desarrolla localmente; Coolify se incorpora cuando exista un flujo verificable.
Los comandos de ejecución y pruebas están en [docs/development.md](docs/development.md).
