"""Spec validation engine — automated consistency checks without an LLM."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.spec.parser import RepoSpec, parse_spec

log = structlog.get_logger()


@dataclass
class CouplingMetrics:
    """Coupling metrics for a single module."""

    afferent: int = 0
    efferent: int = 0
    instability: float = 0.0


@dataclass
class ValidationResult:
    """Result of spec validation."""

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, CouplingMetrics] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def _check_paths(spec: RepoSpec, project_path: Path) -> tuple[list[str], list[str]]:
    """Verify each module's declared path exists on disk."""
    errors: list[str] = []
    warnings: list[str] = []

    for mod in spec.modules:
        if not mod.path:
            warnings.append(f"Module '{mod.name}' has no path declared")
            continue

        full_path = project_path / mod.path
        if not full_path.exists():
            errors.append(f"Module '{mod.name}': path '{mod.path}' does not exist")

    return errors, warnings


def _extract_python_imports(file_path: Path) -> set[str]:
    """Extract imported module names from a Python file using ast.parse."""
    try:
        source = file_path.read_text()
        tree = ast.parse(source)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return set()

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    return imports


def _extract_non_python_imports(file_path: Path) -> set[str]:
    """Best-effort regex import extraction for non-Python files."""
    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError):
        return set()

    imports: set[str] = set()

    ts_import = re.compile(r"""(?:import|from)\s+['"]([^'"]+)['"]""")
    for m in ts_import.finditer(content):
        path = m.group(1)
        if path.startswith("."):
            parts = path.strip("./").split("/")
            if parts:
                imports.add(parts[0])

    go_import = re.compile(r'"([^"]+)"')
    if file_path.suffix == ".go":
        for m in go_import.finditer(content):
            pkg = m.group(1).split("/")[-1]
            imports.add(pkg)

    return imports


def _check_imports(spec: RepoSpec, project_path: Path) -> tuple[list[str], list[str]]:
    """Cross-reference declared dependency edges against actual imports."""
    errors: list[str] = []
    warnings: list[str] = []

    module_paths: dict[str, Path] = {}
    for mod in spec.modules:
        if mod.path:
            module_paths[mod.name] = project_path / mod.path

    for mod in spec.modules:
        if not mod.path or not mod.depends_on:
            continue

        mod_path = project_path / mod.path
        if not mod_path.exists():
            continue

        python_files: list[Path] = []
        if mod_path.is_dir():
            python_files = list(mod_path.rglob("*.py"))
        elif mod_path.is_file() and mod_path.suffix == ".py":
            python_files = [mod_path]

        is_python = len(python_files) > 0

        if is_python:
            all_imports: set[str] = set()
            for py_file in python_files:
                all_imports |= _extract_python_imports(py_file)

            dep_paths: dict[str, str] = {}
            for dep_name in mod.depends_on:
                dep_mod = spec.get_module(dep_name)
                if dep_mod and dep_mod.path:
                    parts = dep_mod.path.replace("/", ".").replace("\\", ".")
                    top_pkg = parts.split(".")[0]
                    dep_paths[dep_name] = top_pkg

            for dep_name, top_pkg in dep_paths.items():
                if top_pkg not in all_imports:
                    warnings.append(
                        f"Module '{mod.name}' declares dependency on '{dep_name}' "
                        f"but no import of '{top_pkg}' found in source"
                    )
        else:
            non_python_files: list[Path] = []
            if mod_path.is_dir():
                non_python_files = [f for f in mod_path.rglob("*") if f.is_file()]
            elif mod_path.is_file():
                non_python_files = [mod_path]

            all_imports_np: set[str] = set()
            for np_file in non_python_files:
                all_imports_np |= _extract_non_python_imports(np_file)

            if all_imports_np:
                for dep_name in mod.depends_on:
                    dep_mod = spec.get_module(dep_name)
                    dep_path_str = dep_mod.path if dep_mod else dep_name
                    dep_parts = dep_path_str.split("/")
                    if not any(p in all_imports_np for p in dep_parts):
                        warnings.append(
                            f"Module '{mod.name}' declares dependency on '{dep_name}' "
                            f"but no matching import found (approximate, non-Python)"
                        )

    return errors, warnings


def _detect_orphans(spec: RepoSpec) -> list[str]:
    """Flag modules with zero consumers (no incoming dependency edges)."""
    warnings: list[str] = []
    all_names = {m.name for m in spec.modules}
    consumed: set[str] = set()

    for mod in spec.modules:
        for dep in mod.depends_on:
            if dep in all_names:
                consumed.add(dep)

    for edge in spec.dependency_edges:
        if edge.target in all_names:
            consumed.add(edge.target)

    for mod in spec.modules:
        if mod.name not in consumed and mod.name in all_names:
            warnings.append(f"Orphan module: '{mod.name}' has zero consumers")

    return warnings


def _detect_hubs(spec: RepoSpec) -> list[str]:
    """Flag modules with >=5 dependents as high-impact change targets."""
    warnings: list[str] = []
    all_names = {m.name for m in spec.modules}
    dependent_count: dict[str, int] = {m.name: 0 for m in spec.modules}

    for mod in spec.modules:
        for dep in mod.depends_on:
            if dep in dependent_count:
                dependent_count[dep] += 1

    for edge in spec.dependency_edges:
        if edge.target in all_names and edge.source in all_names:
            dependent_count.setdefault(edge.target, 0)

    for name, count in dependent_count.items():
        if count >= 5:
            warnings.append(
                f"Hub module: '{name}' has {count} dependents (high-impact change target)"
            )

    return warnings


def _compute_coupling(spec: RepoSpec) -> dict[str, CouplingMetrics]:
    """Compute afferent (Ca) and efferent (Ce) coupling per module."""
    all_names = {m.name for m in spec.modules}
    metrics: dict[str, CouplingMetrics] = {m.name: CouplingMetrics() for m in spec.modules}

    for mod in spec.modules:
        metrics[mod.name].efferent = len([d for d in mod.depends_on if d in all_names])

    for mod in spec.modules:
        for dep in mod.depends_on:
            if dep in metrics:
                metrics[dep].afferent += 1

    for name, m in metrics.items():
        total = m.afferent + m.efferent
        m.instability = m.efferent / total if total > 0 else 0.0

    return metrics


def _format_validation_report(result: ValidationResult, spec: RepoSpec) -> str:
    """Format validation results as human-readable Markdown."""
    lines: list[str] = ["# Spec Validation Report", ""]

    lines.append("## Summary")
    lines.append("")
    status = "PASS" if result.passed else "FAIL"
    lines.append(f"**Status:** {status}")
    lines.append(f"**Errors:** {len(result.errors)}")
    lines.append(f"**Warnings:** {len(result.warnings)}")
    lines.append(f"**Modules:** {len(spec.modules)}")
    lines.append("")

    if result.errors:
        lines.append("## Errors")
        lines.append("")
        for err in result.errors:
            lines.append(f"- {err}")
        lines.append("")

    if result.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warn in result.warnings:
            lines.append(f"- {warn}")
        lines.append("")

    if result.metrics:
        lines.append("## Coupling Metrics")
        lines.append("")
        lines.append("| Module | Ca (afferent) | Ce (efferent) | I (instability) |")
        lines.append("|--------|--------------|--------------|-----------------|")
        for name in sorted(result.metrics):
            m = result.metrics[name]
            lines.append(f"| {name} | {m.afferent} | {m.efferent} | {m.instability:.2f} |")
        lines.append("")

    return "\n".join(lines)


async def validate_spec(project_path: Path) -> ValidationResult:
    """Validate .factory/GRAPH-SPEC.md against the actual project.

    Runs path existence checks, import cross-referencing, orphan/hub detection,
    and coupling metric computation.

    Writes results to .factory/spec_validation.md.
    """
    spec_path = project_path / ".factory" / "GRAPH-SPEC.md"
    spec = parse_spec(spec_path)

    result = ValidationResult()

    path_errors, path_warnings = _check_paths(spec, project_path)
    result.errors.extend(path_errors)
    result.warnings.extend(path_warnings)

    import_errors, import_warnings = _check_imports(spec, project_path)
    result.errors.extend(import_errors)
    result.warnings.extend(import_warnings)

    result.warnings.extend(_detect_orphans(spec))
    result.warnings.extend(_detect_hubs(spec))

    result.metrics = _compute_coupling(spec)

    report = _format_validation_report(result, spec)
    output_path = project_path / ".factory" / "spec_validation.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    log.info(
        "spec.validate.complete",
        errors=len(result.errors),
        warnings=len(result.warnings),
        modules=len(spec.modules),
        output=str(output_path),
    )

    return result
