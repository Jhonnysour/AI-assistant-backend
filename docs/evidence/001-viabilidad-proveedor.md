# Evidencia: viabilidad del proveedor (SPEC-001, paso previo)

Especificación usada: [SPEC-001](../specs/001-chat-texto.md) en la revisión `308a18c`, más los cambios
de este incremento. No registrar aquí tokens, URLs de autorización, correo ni identificadores de cuenta.

## Decisiones

| Fecha | Decisión |
| --- | --- |
| 2026-10-29 | Proveedor inicial: OpenAI mediante Sign in with ChatGPT (uso del plan), sujeto a comprobar la elegibilidad real de la cuenta. |
| 2026-10-29 | Sin API de pago como alternativa automática. Tampoco se usan las credenciales de extensiones ni de Codex. |
| 2026-10-29 | Python 3.12 con venv y pip para la prueba. Resultados en `docs/evidence/`. |
| 2026-10-29 | Desarrollo en Windows; despliegue posterior en el VPS Ubuntu mediante Coolify. |

Cuenta: suscripción de ChatGPT de 100 USD. **El nombre del plan y su elegibilidad no se deducen del
precio**: la documentación cita Plus y Pro como elegibles. La comprobación real es que `login`
conceda `chatgpt.tokens.use.direct` y que una petición termine en `response.completed`.

## Fuentes oficiales (consultadas el 2026-10-29)

- [ChatGPT plan usage – open source](https://developers.openai.com/siwc/token-sharing-open-source):
  "open-source projects, personal projects that run locally, and selected private apps"; para una app
  "paid or remotely hosted" se pide acceso mediante una lista de espera
  ([cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)).
- [Sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
  [sesiones](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions),
  [tokens](https://developers.openai.com/siwc/token-sharing-open-source/token-reference),
  [modelos e inferencia](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
  [errores](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery),
  [limitaciones](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
  [VMs autoalojadas](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms).
- La facturación de ChatGPT y la de la API son independientes; la suscripción no incluye crédito de API
  ([ayuda de OpenAI](https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform)).

## Resultados

### 2026-10-29 · Windows 11 Pro, Python 3.12.10, httpx 0.28.1, PyJWT 2.15.1

| Comprobación | Resultado |
| --- | --- |
| Pruebas automáticas sin red (`pytest -q`) | 43 pasan; también con el proxy apuntando a un puerto inexistente. |
| Discovery OIDC y JWKS con el cliente del proyecto vía proxy | Correcto (issuer `https://auth.openai.com`). |
| `auth.openai.com` sin proxy desde este equipo | 403 (causa no determinada). |
| `GET api.openai.com/v1/models` vía proxy, sin token | 401: alcanzable. |
| CLI sin sesión (`status`, `models`) | Indica que falta `login`; salida 2. |
### 2026-10-29 · prueba real con la cuenta (ejecutada por el usuario en su terminal)

| Campo | Valor |
| --- | --- |
| Entorno | Windows 11 Pro, PowerShell, Python 3.12.10, salida vía proxy local `127.0.0.1:17890`. |
| `login` | Completado en el perfil de Chrome con proxy y callback loopback correcto. Pantalla de consentimiento "Conecta tu AI Assistant a ChatGPT". |
| Uso del plan (`chatgpt.tokens.use.direct`) | Concedido. |
| Modelos ofrecidos por el catálogo (8) | `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` |
| Petición con el nombre visible `GPT-6-Luna` | 400 `model_not_found`: hay que usar el slug. Detalle del proveedor: "not supported when using Codex with a ChatGPT account", lo que sugiere que la vía comparte infraestructura con Codex (no verificado). |
| Petición `ask --model gpt-6-luna "Responde exactamente: Hola, mundo"` | Respuesta `Hola, mundo` en streaming; **estado `completed`** (`response.completed`). |
| Modelos con acceso comprobado | `gpt-6-luna`. El resto del catálogo sigue sin comprobar. |
| Credenciales | Guardadas cifradas con DPAPI fuera del repositorio; ningún token en la salida ni en Git. |

**Decisión:** la viabilidad del proveedor queda confirmada para desarrollo local con esta cuenta.
Se continúa con OpenAI mediante Sign in with ChatGPT como adaptador real. AC-03 de SPEC-001 se
cerrará cuando la respuesta completa llegue de extremo a extremo (desktop → backend → proveedor).
Siguen abiertos los límites de uso semanales y por app (no observados todavía) y las condiciones del
despliegue indicadas más abajo.

## Despliegue en el VPS: pendiente

La guía de [VMs autoalojadas](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
describe el caso "an open-source app on a remote virtual machine". No permite concluir ni que nuestro
caso esté autorizado ni que esté bloqueado. Condiciones y pendientes:

1. **Encaje en el programa.** La guía trata apps open-source. Este repositorio todavía no tiene
   licencia ni una visibilidad decidida. Si no se publica como open-source, el caso sería una app
   privada alojada remotamente, que la documentación remite a la lista de espera. Hay que decidirlo o
   consultarlo con OpenAI antes de desplegar.
2. **Procedimiento.** El OAuth se completa en el PC, porque el callback `127.0.0.1` llega a la máquina
   del navegador. Después se transfiere el archivo de credenciales protegido al VPS por un canal seguro
   (SSH), con `client_id`, tokens e ID token. El VPS crea y conserva su propio `ext_agent_host_id`
   **antes** de importar y gestiona sus propias renovaciones.
3. **Formato del almacén.** El archivo DPAPI de Windows no es portable por diseño. Falta definir una
   exportación protegida y un almacén en Linux (volumen persistente en Coolify, permisos `0600` y
   cifrado con una clave guardada como secreto de Coolify, o similar).
4. **Un solo consumidor por sesión.** El refresh token rota: si el PC y el VPS usan el mismo
   conjunto de tokens, uno invalidará al otro (`refresh_token_reused`). Tras transferir, el PC no debe
   seguir usándolo.
5. **Limitaciones declaradas.** La atribución de uso por host y la revocación del acceso de una sesión
   transferida "are not yet available".
6. **Región de salida.** La salida a OpenAI desde el VPS debe ser admitida (un 403 de admisión puede
   deberse a la región). Antes de desplegar, comprobar desde el VPS el endpoint público de discovery.
   Es probable que funcione, porque el proxy actual sale por ese VPS, pero no se ha comprobado.
7. **API propia autenticada** antes de exponer el backend (ya indicado en la arquitectura).

Estos puntos no bloquean la prueba local, que cumple "personal projects that run locally".
