"""Spec generation orchestration — collect source files, batch for Opus, run pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()

EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        ".factory",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".eggs",
        "*.egg-info",
    }
)

SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".rb",
        ".ex",
        ".exs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".swift",
        ".scala",
        ".clj",
        ".proto",
        ".graphql",
        ".sql",
    }
)

BATCH_TOKEN_LIMIT = 160_000
APPROX_CHARS_PER_TOKEN = 4


def _get_gitignored(paths: list[Path], project_path: Path) -> set[Path]:
    """Return the subset of paths that are gitignored, using a single subprocess."""
    if not paths:
        return set()
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(str(p) for p in paths),
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        return set()
    return {Path(line) for line in result.stdout.splitlines() if line}


def _is_excluded_dir(part: str) -> bool:
    """Check if a directory component matches an exclusion pattern."""
    for excluded in EXCLUDED_DIRS:
        if excluded.startswith("*"):
            if part.endswith(excluded[1:]):
                return True
        elif part == excluded:
            return True
    return False


def collect_source_files(project_path: Path) -> list[Path]:
    """Collect source files from a project, respecting .gitignore and exclusions.

    Returns paths relative to project_path, sorted for deterministic output.
    """
    has_git = (project_path / ".git").is_dir()
    candidates: list[Path] = []

    for path in sorted(project_path.rglob("*")):
        if not path.is_file():
            continue

        rel = path.relative_to(project_path)

        if any(_is_excluded_dir(part) for part in rel.parts):
            continue

        if path.suffix not in SOURCE_EXTENSIONS:
            continue

        candidates.append(rel)

    if has_git and candidates:
        ignored = _get_gitignored([project_path / c for c in candidates], project_path)
        candidates = [c for c in candidates if (project_path / c) not in ignored]

    log.info("spec.collect_source_files", count=len(candidates), project=str(project_path))
    return candidates


def group_into_batches(
    files: list[Path],
    project_path: Path,
    token_limit: int = BATCH_TOKEN_LIMIT,
) -> list[list[Path]]:
    """Group source files into batches that fit within a token limit.

    Each batch contains files whose combined content fits within the limit.
    Files larger than the limit are placed in their own batch.
    """
    char_limit = token_limit * APPROX_CHARS_PER_TOKEN
    batches: list[list[Path]] = []
    current_batch: list[Path] = []
    current_chars = 0

    for rel_path in files:
        full_path = project_path / rel_path
        try:
            file_chars = full_path.stat().st_size
        except OSError:
            continue

        if current_batch and current_chars + file_chars > char_limit:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(rel_path)
        current_chars += file_chars

    if current_batch:
        batches.append(current_batch)

    log.info(
        "spec.group_into_batches",
        total_files=len(files),
        batches=len(batches),
        token_limit=token_limit,
    )
    return batches


async def generate_spec(project_path: Path) -> Path:
    """Generate a repo spec for a project.

    Runs the extraction → annotation pipeline:
    1. Collect source files and batch them
    2. Run Opus extraction agent to produce spec_raw.md
    3. Run Researcher annotation agent to produce GRAPH-SPEC.md

    Returns the path to the generated GRAPH-SPEC.md.
    """
    from factory.agents.runner import invoke_agent

    factory_dir = project_path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)

    source_files = collect_source_files(project_path)
    if not source_files:
        raise ValueError(f"No source files found in {project_path}")

    batches = group_into_batches(source_files, project_path)
    log.info("spec.generate", files=len(source_files), batches=len(batches))

    file_listing = "\n".join(f"- {f}" for f in source_files)
    batch_info = "\n".join(
        f"Batch {i + 1}: {len(b)} files ({', '.join(str(f) for f in b[:5])}{'...' if len(b) > 5 else ''})"
        for i, b in enumerate(batches)
    )

    extract_task = (
        f"Extract a structural module map from this project at {project_path}.\n\n"
        f"## Source Files ({len(source_files)} total, {len(batches)} batch(es))\n\n"
        f"{file_listing}\n\n"
        f"## Batches\n\n{batch_info}\n\n"
        f"Read these source files and produce the spec_raw.md output.\n"
        f"Write the output to {factory_dir / 'spec_raw.md'}."
    )

    result, code = await invoke_agent(
        "researcher",
        extract_task,
        project_path,
        timeout=600.0,
        dangerously_skip_permissions=True,
        model="opus",
    )
    if code != 0:
        raise RuntimeError(f"Spec extraction failed (exit {code}): {result[:500]}")

    spec_raw = factory_dir / "spec_raw.md"
    if not spec_raw.exists():
        raise FileNotFoundError(
            f"Extraction agent did not produce {spec_raw}. Agent output: {result[:500]}"
        )

    annotate_task = (
        f"Annotate and enrich the raw spec at {spec_raw} for the project at {project_path}.\n\n"
        f"Read {spec_raw} and key source files.\n"
        f"Write the annotated repo spec to {factory_dir / 'GRAPH-SPEC.md'}."
    )

    result, code = await invoke_agent(
        "researcher",
        annotate_task,
        project_path,
        timeout=600.0,
        dangerously_skip_permissions=True,
    )
    if code != 0:
        raise RuntimeError(f"Spec annotation failed (exit {code}): {result[:500]}")

    repo_spec = factory_dir / "GRAPH-SPEC.md"
    if not repo_spec.exists():
        raise FileNotFoundError(
            f"Annotation agent did not produce {repo_spec}. Agent output: {result[:500]}"
        )

    log.info("spec.generate.complete", output=str(repo_spec))
    return repo_spec
