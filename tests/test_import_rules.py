"""Check the package's import rules by reading every module's imports (nothing is run).

- The library never imports the entrypoints: grip.cli, grip.apps, or the run wiring in grip.pipelines.
- The optimiser never imports technique, evaluation, calibration or existing tools (PROJECT.md invariants).
"""

import ast
from pathlib import Path

import pytest

from grip.core.paths import repo_root

PACKAGE_ROOT = repo_root() / "src/grip"
ENTRYPOINT_PACKAGES = ("grip.cli", "grip.apps", "grip.pipelines")
OPTIMISER_FORBIDDEN = ("grip.technique", "grip.evaluation", "grip.calibration", "grip.existing_tools")


def module_name(path: Path) -> str:
    """grip.<subpackage>.<module> for a file under src/grip/."""
    parts = path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_grip_modules(path: Path) -> set[str]:
    """Every grip.* module a file imports (import x / from x import y)."""
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {name for name in names if name == "grip" or name.startswith("grip.")}


def is_inside(module: str, packages: tuple[str, ...]) -> bool:
    return any(module == package or module.startswith(package + ".") for package in packages)


MODULES = {module_name(path): imported_grip_modules(path) for path in sorted(PACKAGE_ROOT.rglob("*.py"))}


@pytest.mark.parametrize("module", [name for name in MODULES if not is_inside(name, ENTRYPOINT_PACKAGES)])
def test_library_never_imports_entrypoints(module: str):
    assert not {name for name in MODULES[module] if is_inside(name, ENTRYPOINT_PACKAGES)}


@pytest.mark.parametrize("module", [name for name in MODULES if is_inside(name, ("grip.optimiser",))])
def test_optimiser_imports_stay_inside_the_rules(module: str):
    assert not {name for name in MODULES[module] if is_inside(name, OPTIMISER_FORBIDDEN)}


def test_pipelines_never_import_entrypoints():
    """Pipelines are shared wiring: the CLI and the apps import them, never the other way round."""
    for module, imports in MODULES.items():
        if is_inside(module, ("grip.pipelines",)):
            assert not {name for name in imports if is_inside(name, ("grip.cli", "grip.apps"))}, module
