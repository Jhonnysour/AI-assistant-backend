# Desarrollo local

Estado: verificado en Windows 11 el 2026-10-29 para la prueba de viabilidad del proveedor y el
servidor HTTP con el proveedor simulado ([SPEC-001](specs/001-chat-texto.md)). No hay despliegue.

## Requisitos

- Windows con Python 3.12 (`py -3.12`). Las dependencias se fijan en [pyproject.toml](../pyproject.toml).
- Proxy local hacia el VPS escuchando en `http://127.0.0.1:17890` (túnel SSH ya existente).
- Un perfil de Chrome que use ese proxy.

## Red y proxy

Desde este equipo, la conexión directa a `auth.openai.com` respondió 403 y la conexión a través
del proxy respondió 200 (2026-10-29). Por eso las llamadas salientes al proveedor usan el proxy.

El backend **no confía** en que el cliente HTTP herede el entorno: [config.py](../src/ai_assistant/infrastructure/config.py)
lee las variables y [infrastructure/http_client.py](../src/ai_assistant/infrastructure/http_client.py) crea un cliente
httpx con `trust_env=False` y rutas explícitas. El dominio no conoce estos ajustes.

| Variable | Valor en desarrollo | Uso |
| --- | --- | --- |
| `HTTPS_PROXY` | `http://127.0.0.1:17890` | Proxy para `auth.openai.com` y `api.openai.com`. |
| `HTTP_PROXY` | `http://127.0.0.1:17890` | Proxy para URLs `http://` salientes (no hay ninguna por ahora). |
| `NO_PROXY` | `localhost,127.0.0.1,::1` | Destinos que van directos. |
| `AI_ASSISTANT_BROWSER` | opcional | Comando para abrir el perfil de Chrome correcto (ver abajo). |
| `AI_ASSISTANT_DATA_DIR` | opcional | Datos locales; por defecto `%LOCALAPPDATA%\ai-assistant`. |
| `AI_ASSISTANT_OAUTH_PORT` | opcional | Puerto del callback; por defecto `1455`. |
| `AI_ASSISTANT_PROVIDER` | `fake` (por defecto) o `chatgpt_plan` | Proveedor del servidor HTTP. `fake` no consume el plan. |
| `AI_ASSISTANT_API_PORT` | opcional | Puerto del servidor HTTP; por defecto `8000`. |

El callback OAuth (`http://127.0.0.1:1455/auth/callback`) es una conexión **entrante** a un servidor
local de un solo uso: no pasa por el proxy del backend. En Chrome, las direcciones loopback se omiten
del proxy por defecto (reglas implícitas desde Chrome 72), salvo que el perfil use
`--proxy-bypass-list="<-loopback>"`; en ese caso el callback fallaría.
Referencia: [Chromium proxy docs](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md).

Comprobar las variables en la terminal de PowerShell que se vaya a usar (no están definidas a nivel
de usuario en Windows; las aporta el entorno con el que se lanzó VS Code):

```powershell
Get-ChildItem Env:*PROXY*
```

Si faltan, definirlas solo para esa sesión:

```powershell
$env:HTTP_PROXY  = 'http://127.0.0.1:17890'
$env:HTTPS_PROXY = 'http://127.0.0.1:17890'
$env:NO_PROXY    = 'localhost,127.0.0.1,::1'
```

## Instalación

Desde PowerShell, en la carpeta del repositorio. Se invoca el Python del entorno virtual
directamente, sin `Activate.ps1`, para no depender de la política de ejecución de scripts.

```powershell
cd "D:\Proyecto Jarvis\AI-assistant-backend"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --proxy http://127.0.0.1:17890 -e ".[dev]"
```

## Pruebas automáticas (sin red)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Usan transportes simulados de httpx, una clave RSA generada para firmar ID tokens de prueba, un
listener loopback efímero y un servidor uvicorn en un puerto local libre (cancelación y desconexión
reales a mitad del stream). Pasan también con el proxy apuntando a un puerto inexistente.
Las pruebas de DPAPI y de la CLI se omiten fuera de Windows.

## Prueba real con la cuenta (manual)

Ejecutarla **en tu propia terminal**: la URL de autorización y el navegador son tuyos. La CLI nunca
imprime tokens; la URL solo se muestra con `--print-url` o si no hay navegador configurado.

### 1. Elegir cómo abrir el perfil de Chrome con proxy

En ese perfil de Chrome, abrir `chrome://version` y anotar la **Ruta del perfil** y la
**Línea de comandos**. Si el perfil se lanza con un acceso directo con `--user-data-dir` o
`--proxy-server`, copiar el ejecutable y esas opciones (sin URLs). Ejemplos:

```powershell
$env:AI_ASSISTANT_BROWSER = '"C:\Program Files\Google\Chrome\Application\chrome.exe" --profile-directory="Profile 1"'
# o, si el perfil usa un directorio de datos propio:
$env:AI_ASSISTANT_BROWSER = '"C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\ruta\perfil" --proxy-server="http://127.0.0.1:17890"'
```

Si Chrome ya está abierto con ese perfil, la URL se abre en una pestaña nueva.

**Alternativa manual:** sin `AI_ASSISTANT_BROWSER`, o con `login --print-url`, la CLI muestra la URL
en la terminal. Copiarla en la barra de direcciones del perfil con proxy. Es de un solo uso
(lleva `state` y el reto PKCE, no tokens); no guardarla, no pegarla en chats ni notas.
Si el navegador muestra un error en `127.0.0.1`, no compartir la dirección de esa página: contiene
el código de autorización. Revisar el apartado de proxy y repetir `login`.

### 2. Ejecutar

```powershell
.\.venv\Scripts\python.exe -m ai_assistant.infrastructure.tools.probe login
.\.venv\Scripts\python.exe -m ai_assistant.infrastructure.tools.probe status
.\.venv\Scripts\python.exe -m ai_assistant.infrastructure.tools.probe models
.\.venv\Scripts\python.exe -m ai_assistant.infrastructure.tools.probe ask --model gpt-6-luna "Responde exactamente: Hola, mundo"
```

`--model` recibe el **slug**, que es la primera columna de `models` (p. ej. `gpt-6-luna`), y no el
nombre visible (`GPT-6-Luna`). En PowerShell no se pueden escribir marcadores con `< >`.

- `login`: en la pantalla de OpenAI elegir la cuenta y **conceder el uso del plan**. Sin el scope
  `chatgpt.tokens.use.direct` la sesión queda sin permiso y no se usa ninguna API de pago.
- `status`: solo estado local (sin red ni datos personales).
- `models`: catálogo **ofrecido**; cada modelo aparece como `sin comprobar` o `acceso comprobado`.
- `ask`: muestra el texto según llega. Solo `Estado: completed` (evento `response.completed`)
  marca el modelo como comprobado. Ctrl+C cancela y el texto parcial se identifica como tal.
- `logout`: revoca el refresh token y borra los tokens; `logout --forget` borra también el registro.

Códigos de salida: `0` correcto, `1` fallo del proveedor o respuesta incompleta,
`2` acción necesaria (login, permiso, configuración), `130` cancelado.

Registrar el resultado en [docs/evidence](evidence/001-viabilidad-proveedor.md) sin tokens, URLs de
autorización, correo ni identificadores de cuenta.

## Servidor HTTP (contrato chat-v1)

Implementa [chat-v1](api/chat-v1.md). Escucha **solo en `127.0.0.1`**: la API no tiene autenticación
y el host no es configurable a propósito. Si se actualiza desde una versión anterior, repetir el
`pip install` de la instalación (añade FastAPI y uvicorn).

Con el proveedor simulado (por defecto; no usa red ni el plan):

```powershell
.\.venv\Scripts\python.exe -m ai_assistant.infrastructure.bootstrap.server
```

Con el proveedor real: requiere `login` previo con la CLI de la prueba y las variables de proxy en
esa misma terminal. Cada mensaje consume el plan.

```powershell
$env:HTTP_PROXY  = 'http://127.0.0.1:17890'
$env:HTTPS_PROXY = 'http://127.0.0.1:17890'
$env:NO_PROXY    = 'localhost,127.0.0.1,::1'
$env:AI_ASSISTANT_PROVIDER = 'chatgpt_plan'
.\.venv\Scripts\python.exe -m ai_assistant.infrastructure.bootstrap.server
```

Se detiene con Ctrl+C. Las conversaciones se pierden al detenerlo.

Probarlo desde otra terminal de PowerShell (`curl.exe`, no el alias `curl`; `--noproxy "*"` evita que
la petición local pase por el proxy):

```powershell
curl.exe -s --noproxy "*" http://127.0.0.1:8000/v1/models
$conv = (curl.exe -s --noproxy "*" -X POST http://127.0.0.1:8000/v1/conversations | ConvertFrom-Json).conversation_id
'{"model":"fake-model","text":"Hola"}' | curl.exe -s -N --noproxy "*" -X POST -H "Content-Type: application/json" --data-binary "@-" "http://127.0.0.1:8000/v1/conversations/$conv/messages"
```

Con el proveedor real, usar un slug de `/v1/models` (p. ej. `gpt-6-luna`) en lugar de `fake-model`.
La documentación interactiva de FastAPI queda en `http://127.0.0.1:8000/docs`.

El log de uvicorn registra método, ruta y código de cada petición (identificadores de conversación,
nunca el texto). Los fallos internos registran solo el tipo de error y las líneas de código.

## Datos locales y credenciales

En `%LOCALAPPDATA%\ai-assistant` (fuera del repositorio):

| Archivo | Contenido | Protección |
| --- | --- | --- |
| `host-id` | `ext_agent_host_id` opaco (`urn:uuid:…`) | No es un secreto ni una credencial. |
| `chatgpt-plan\credentials.dpapi` | client_id emitido, tokens, scopes, caducidad | Cifrado con DPAPI ligado a tu cuenta de Windows y escritura atómica. |
| `chatgpt-plan\verified-models.json` | Modelos con `response.completed` y fecha | Sin secretos. |

Estar fuera del repositorio no basta: el archivo de credenciales está cifrado y no se puede descifrar
desde otra cuenta ni desde otra máquina. Un proceso que se ejecute con tu misma cuenta de Windows sí
podría descifrarlo; es el límite de DPAPI. Fuera de Windows el almacén se rechaza a propósito.

## Despliegue: pendiente

No se despliega todavía. Lo que falta verificar y decidir está en
[evidencia de viabilidad](evidence/001-viabilidad-proveedor.md#despliegue-en-el-vps-pendiente).
