"""AC-09: el núcleo se carga y se prueba sin FastAPI, httpx, JWT ni SDKs de proveedores."""

import subprocess
import sys

CORE_MODULES = [
    "ai_assistant.conversations.domain.model",
    "ai_assistant.conversations.application.ports",
    "ai_assistant.conversations.application.chat",
]
FORBIDDEN = ["fastapi", "starlette", "httpx", "jwt", "openai", "sqlalchemy"]


def test_core_does_not_import_infrastructure():
    code = (
        "import sys\n"
        + "".join(f"import {m}\n" for m in CORE_MODULES)
        + f"print(','.join(sorted(n for n in {FORBIDDEN!r} if n in sys.modules)))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""
