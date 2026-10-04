from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROMA_ROOT = PROJECT_ROOT / "roma"

DOMAIN_FORBIDDEN_PREFIXES = (
    "roma.api",
    "roma.providers",
    "roma.realtime",
    "roma.repositories",
    "roma.services",
    "roma.workers",
    "asyncpg",
    "dramatiq",
    "fastapi",
    "openai",
    "pipecat",
    "redis",
    "sqlalchemy",
    "twilio",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _violates(module: str, forbidden_prefix: str) -> bool:
    return module == forbidden_prefix or module.startswith(f"{forbidden_prefix}.")


def test_domain_layer_does_not_depend_on_outer_adapters_or_sdks():
    violations: list[str] = []
    for path in sorted((ROMA_ROOT / "domain").rglob("*.py")):
        for imported in sorted(_imports(path)):
            if any(_violates(imported, prefix) for prefix in DOMAIN_FORBIDDEN_PREFIXES):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)} imports {imported}"
                )

    assert violations == []
