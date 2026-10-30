"""Reglas de dependencia de la arquitectura hexagonal (docs/architecture.md) y AC-09.

Se analizan los imports de cada módulo de src/ai_assistant sin ejecutarlos.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src"
PACKAGE = "ai_assistant"


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


MODULES = {module_name(p): p for p in (ROOT / PACKAGE).rglob("*.py")}


def imports_of(module: str) -> set[str]:
    """Módulos que importa, con los relativos resueltos y `from paquete import módulo` expandido."""
    path = MODULES[module]
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(anchor + ([base] if base else []))
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                found.add(candidate if candidate in MODULES else base)
    return found


def in_layer(module: str, layer: str) -> bool:
    return module == layer or module.startswith(layer + ".")


def own(module: str) -> bool:
    return in_layer(module, PACKAGE)


DOMAIN = f"{PACKAGE}.domain"
APPLICATION = f"{PACKAGE}.application"
INBOUND_PORTS = f"{APPLICATION}.ports.inbound"
OUTBOUND_PORTS = f"{APPLICATION}.ports.outbound"
ERROR_CODES = f"{APPLICATION}.error_codes"
USE_CASES = f"{APPLICATION}.use_cases"
INFRASTRUCTURE = f"{PACKAGE}.infrastructure"
INBOUND_ADAPTERS = f"{INFRASTRUCTURE}.adapters.inbound"
OUTBOUND_ADAPTERS = f"{INFRASTRUCTURE}.adapters.outbound"
BOOTSTRAP = f"{INFRASTRUCTURE}.bootstrap"


def violations(source_layer: str, allowed) -> list[str]:
    return sorted(
        f"{module} -> {target}"
        for module in MODULES if in_layer(module, source_layer)
        for target in imports_of(module) if not allowed(module, target)
    )


def stdlib(target: str) -> bool:
    return target.split(".")[0] in sys.stdlib_module_names


def test_domain_depends_only_on_itself_and_stdlib():
    assert violations(DOMAIN, lambda m, t: stdlib(t) or in_layer(t, DOMAIN)) == []


def test_application_depends_only_on_domain_and_stdlib():
    assert violations(APPLICATION, lambda m, t: stdlib(t) or in_layer(t, DOMAIN)
                      or in_layer(t, APPLICATION)) == []


def test_ports_do_not_depend_on_use_cases():
    assert violations(f"{APPLICATION}.ports", lambda m, t: not in_layer(t, USE_CASES)) == []


def test_inbound_adapters_only_use_inbound_ports():
    allowed = (INBOUND_PORTS, ERROR_CODES, INBOUND_ADAPTERS)
    assert violations(INBOUND_ADAPTERS, lambda m, t: not own(t)
                      or any(in_layer(t, layer) for layer in allowed)) == []


def test_outbound_adapters_only_use_outbound_ports_and_domain():
    forbidden = (USE_CASES, INBOUND_PORTS, INBOUND_ADAPTERS, BOOTSTRAP)
    assert violations(OUTBOUND_ADAPTERS, lambda m, t: not any(in_layer(t, layer) for layer in forbidden)) == []


def test_only_bootstrap_wires_use_cases():
    users = {module for module in MODULES if not in_layer(module, USE_CASES)
             and any(in_layer(t, USE_CASES) for t in imports_of(module))}
    assert all(in_layer(module, BOOTSTRAP) for module in users), users


@pytest.mark.parametrize("layer", [DOMAIN, APPLICATION])
def test_core_does_not_load_frameworks_at_runtime(layer):
    """AC-09: el núcleo se carga y se prueba sin FastAPI, httpx, JWT ni SDKs de proveedores."""
    forbidden = ["fastapi", "starlette", "pydantic", "httpx", "jwt", "openai", "sqlalchemy", "uvicorn"]
    core = sorted(module for module in MODULES if in_layer(module, layer))
    code = (
        "import sys\n"
        + "".join(f"import {m}\n" for m in core)
        + f"print(','.join(sorted(n for n in {forbidden!r} if n in sys.modules)))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""
