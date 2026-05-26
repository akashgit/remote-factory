"""AST-based codebase parser — extracts project structure for Miro board generation."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()

# Directories to skip during source file discovery (mirrors factory/study.py)
_SKIP_DIRS = {
    "tests", "test", ".venv", "venv", "node_modules", "__pycache__",
    ".git", ".factory", "eval", "dist", "build", ".mypy_cache",
    ".tox", ".eggs", ".ruff_cache",
}


@dataclass
class FunctionInfo:
    """A function or method extracted from source."""

    name: str
    lineno: int
    is_async: bool = False
    is_method: bool = False


@dataclass
class ClassInfo:
    """A class extracted from source."""

    name: str
    lineno: int
    bases: list[str] = field(default_factory=list)
    methods: list[FunctionInfo] = field(default_factory=list)


@dataclass
class Dependency:
    """An import dependency between modules."""

    source: str
    target: str
    is_relative: bool = False


@dataclass
class ModuleInfo:
    """Parsed information about a single Python module."""

    path: str
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    imports: list[Dependency] = field(default_factory=list)


@dataclass
class ProjectStructure:
    """Full parsed project structure."""

    root: str
    modules: list[ModuleInfo] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)


def _find_source_files(project_path: Path) -> list[Path]:
    """Find Python source files, skipping tests, venvs, and generated code."""
    sources: list[Path] = []
    for f in project_path.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in f.relative_to(project_path).parts):
            continue
        sources.append(f)
    return sorted(sources)


def _parse_module(path: Path, project_root: Path) -> ModuleInfo:
    """Parse a single Python file into a ModuleInfo."""
    rel_path = str(path.relative_to(project_root))
    module = ModuleInfo(path=rel_path)

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        log.debug("ast_parse_failed", path=rel_path)
        return module

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(ast.unparse(base))

            methods: list[FunctionInfo] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(FunctionInfo(
                        name=item.name,
                        lineno=item.lineno,
                        is_async=isinstance(item, ast.AsyncFunctionDef),
                        is_method=True,
                    ))

            module.classes.append(ClassInfo(
                name=node.name,
                lineno=node.lineno,
                bases=bases,
                methods=methods,
            ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip methods — they're captured under their class
            if isinstance(getattr(node, '_parent', None), ast.ClassDef):
                continue
            # Top-level check: parent is Module
            module.functions.append(FunctionInfo(
                name=node.name,
                lineno=node.lineno,
                is_async=isinstance(node, ast.AsyncFunctionDef),
            ))

        elif isinstance(node, ast.Import):
            for alias in node.names:
                module.imports.append(Dependency(
                    source=rel_path,
                    target=alias.name,
                ))

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module.imports.append(Dependency(
                    source=rel_path,
                    target=node.module,
                    is_relative=node.level > 0,
                ))

    return module


def _list_non_python_files(project_path: Path) -> list[ModuleInfo]:
    """Fallback: list directory structure for non-Python projects."""
    modules: list[ModuleInfo] = []
    for f in sorted(project_path.rglob("*")):
        if not f.is_file():
            continue
        if any(part in _SKIP_DIRS for part in f.relative_to(project_path).parts):
            continue
        if f.suffix in {".pyc", ".pyo", ".so", ".o", ".class"}:
            continue
        rel_path = str(f.relative_to(project_path))
        modules.append(ModuleInfo(path=rel_path))
    return modules


def analyze(project_path: Path) -> ProjectStructure:
    """Analyze a project directory and return its parsed structure.

    Uses AST parsing for Python projects. Falls back to directory listing
    for non-Python projects.
    """
    project_path = project_path.resolve()
    structure = ProjectStructure(root=str(project_path))

    python_files = _find_source_files(project_path)

    if not python_files:
        log.info("no_python_files", path=str(project_path), fallback="directory_listing")
        structure.modules = _list_non_python_files(project_path)
        return structure

    log.info("analyzing_project", path=str(project_path), file_count=len(python_files))

    all_deps: list[Dependency] = []
    for py_file in python_files:
        module = _parse_module(py_file, project_path)
        structure.modules.append(module)
        all_deps.extend(module.imports)

    structure.dependencies = all_deps
    return structure
