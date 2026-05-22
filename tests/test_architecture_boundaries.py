from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "hermitcrab"

FORBIDDEN_PREFIXES: dict[str, tuple[str, ...]] = {
    "hermitcrab.config": (
        "hermitcrab.agent",
        "hermitcrab.channels",
        "hermitcrab.cli",
        "hermitcrab.cron",
        "hermitcrab.heartbeat",
        "hermitcrab.reminders",
        "hermitcrab.session",
    ),
    "hermitcrab.providers": (
        "hermitcrab.channels",
        "hermitcrab.cli",
    ),
    "hermitcrab.agent.tools": (
        "hermitcrab.channels.telegram",
        "hermitcrab.channels.nostr",
        "hermitcrab.channels.email",
        "hermitcrab.cli",
    ),
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(PROJECT_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imports: list[tuple[int, str]] = []
    source_module = _module_name(path)
    source_package = source_module if path.name == "__init__.py" else source_module.rsplit(".", 1)[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_base = _resolve_import_from_base(source_package, node)
            if imported_base:
                imports.append((node.lineno, imported_base))
            imports.extend(
                (node.lineno, f"{imported_base}.{alias.name}" if imported_base else alias.name)
                for alias in node.names
            )
    return imports


def _resolve_import_from_base(source_package: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = source_package.split(".")
    if node.level > 1:
        package_parts = package_parts[: -(node.level - 1)]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _is_same_or_child(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def test_forbidden_architecture_imports_are_absent() -> None:
    violations: list[str] = []

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source_module = _module_name(path)
        for guarded_prefix, forbidden_prefixes in FORBIDDEN_PREFIXES.items():
            if not _is_same_or_child(source_module, guarded_prefix):
                continue
            for line_number, imported in _imported_modules(path):
                for forbidden_prefix in forbidden_prefixes:
                    if _is_same_or_child(imported, forbidden_prefix):
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{line_number}: "
                            f"{source_module} imports forbidden {imported}"
                        )

    assert not violations, "Forbidden architecture imports found:\n" + "\n".join(violations)
