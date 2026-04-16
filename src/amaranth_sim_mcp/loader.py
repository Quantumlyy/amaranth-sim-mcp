"""Definitions-mode loader for user Amaranth modules."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path
from typing import Iterator

from amaranth.hdl import Elaboratable


def load_definitions_module(file_path: str | Path) -> types.ModuleType:
    """Load a Python module after stripping top-level executable statements."""

    path = Path(file_path).expanduser().resolve(strict=True)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    kept_body: list[ast.stmt] = []
    for node in tree.body:
        if _keep_top_level_statement(node):
            kept_body.append(node)

    stripped = ast.Module(body=kept_body, type_ignores=tree.type_ignores)
    ast.fix_missing_locations(stripped)

    module_name, package_name, extra_paths = _derive_module_context(path)
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = package_name
    module.__spec__ = importlib.util.spec_from_loader(module_name, loader=None)

    code = compile(stripped, str(path), "exec")

    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        with _temporary_sys_path(extra_paths):
            exec(code, module.__dict__)
    finally:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module

    return module


def find_elaboratable_classes(module: types.ModuleType) -> dict[str, type[Elaboratable]]:
    """Return Elaboratable subclasses defined directly in the loaded module."""

    classes: dict[str, type[Elaboratable]] = {}
    for name, value in module.__dict__.items():
        if not inspect.isclass(value):
            continue
        if value is Elaboratable:
            continue
        if not issubclass(value, Elaboratable):
            continue
        if value.__module__ != module.__name__:
            continue
        classes[name] = value
    return classes


def select_elaboratable_class(
    module: types.ModuleType,
    class_name: str | None,
) -> type[Elaboratable]:
    """Select a user-visible Elaboratable class from a loaded module."""

    classes = find_elaboratable_classes(module)
    available = ", ".join(sorted(classes)) or "none"

    if class_name is not None:
        selected = classes.get(class_name)
        if selected is None:
            raise ValueError(
                f"Elaboratable class '{class_name}' was not found. "
                f"Available Elaboratable subclasses: {available}."
            )
        return selected

    if len(classes) == 1:
        return next(iter(classes.values()))

    if not classes:
        raise ValueError(
            "No Elaboratable subclasses were found in the module."
        )

    raise ValueError(
        "Multiple Elaboratable subclasses were found; pass class_name explicitly. "
        f"Available Elaboratable subclasses: {available}."
    )


def _keep_top_level_statement(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return True
    if isinstance(node, ast.Assign):
        # Known limitation: assignments like WIDTH = calc() are stripped because
        # top-level calls are treated as side effects in definitions mode.
        return not _contains_runtime_effect(node.value)
    if isinstance(node, ast.AnnAssign):
        return node.value is None or not _contains_runtime_effect(node.value)
    if isinstance(node, ast.If):
        return _is_name_main_guard(node.test)
    return False


def _contains_runtime_effect(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
            return True
    return False


def _is_name_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not isinstance(node.ops[0], ast.Eq):
        return False
    if not isinstance(node.left, ast.Name) or node.left.id != "__name__":
        return False
    comparator = node.comparators[0]
    return isinstance(comparator, ast.Constant) and comparator.value == "__main__"


def _derive_module_context(path: Path) -> tuple[str, str | None, list[str]]:
    package_dirs: list[Path] = []
    cursor = path.parent
    while (cursor / "__init__.py").is_file():
        package_dirs.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent

    if package_dirs:
        top_package = package_dirs[-1]
        package_root = top_package.parent
        relative = path.relative_to(package_root).with_suffix("")
        module_name = ".".join(relative.parts)
        package_name = ".".join(relative.parts[:-1]) or None
        extra_paths = [str(path.parent), str(package_root)]
        return module_name, package_name, _dedupe_paths(extra_paths)

    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    module_name = f"_amaranth_sim_mcp_loaded_{path.stem}_{digest}"
    return module_name, None, [str(path.parent)]


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in paths:
        if entry in seen:
            continue
        seen.add(entry)
        ordered.append(entry)
    return ordered


@contextlib.contextmanager
def _temporary_sys_path(entries: list[str]) -> Iterator[None]:
    original = list(sys.path)
    sys.path[:0] = entries
    try:
        yield
    finally:
        sys.path[:] = original
