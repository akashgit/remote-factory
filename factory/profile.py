"""User profiling — evidence collection, LLM synthesis, and prompt injection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()

_PROFILE_PATH = Path.home() / ".factory" / "profile.md"
_MAX_SECTION_CHARS = 4000


def _truncate(text: str, limit: int = _MAX_SECTION_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _read_results_tsv(project_path: Path) -> str:
    tsv = project_path / ".factory" / "results.tsv"
    if not tsv.is_file():
        return ""
    try:
        lines = tsv.read_text().splitlines()
        return _truncate("\n".join(lines[-50:]))
    except OSError:
        return ""


def _read_events_jsonl(project_path: Path) -> str:
    events_file = project_path / ".factory" / "events.jsonl"
    if not events_file.is_file():
        return ""
    try:
        lines = events_file.read_text().splitlines()
        return _truncate("\n".join(lines[-100:]))
    except OSError:
        return ""


def _read_auto_memory(project_paths: list[Path] | None = None) -> str:
    memory_base = Path.home() / ".claude" / "projects"
    if not memory_base.is_dir():
        return ""

    allowed_prefixes: set[str] | None = None
    if project_paths is not None:
        allowed_prefixes = set()
        for pp in project_paths:
            encoded = "-" + str(pp.resolve()).replace("/", "-")
            allowed_prefixes.add(encoded)

    chunks: list[str] = []
    total = 0
    try:
        for memory_dir in sorted(memory_base.iterdir()):
            if allowed_prefixes is not None and not any(
                memory_dir.name.startswith(prefix) for prefix in allowed_prefixes
            ):
                continue
            mem_sub = memory_dir / "memory"
            if not mem_sub.is_dir():
                continue
            for md_file in sorted(mem_sub.glob("*.md")):
                try:
                    content = md_file.read_text()
                except OSError:
                    continue
                chunks.append(f"### {md_file.name}\n{content}")
                total += len(content)
                if total > _MAX_SECTION_CHARS:
                    break
            if total > _MAX_SECTION_CHARS:
                break
    except OSError:
        pass
    return _truncate("\n\n".join(chunks))


def _read_strategy_archive(project_path: Path) -> str:
    chunks: list[str] = []
    strategy_dir = project_path / ".factory" / "strategy"
    if strategy_dir.is_dir():
        for md_file in sorted(strategy_dir.glob("*.md")):
            try:
                chunks.append(f"### {md_file.name}\n{md_file.read_text()}")
            except OSError:
                continue
    archive_dir = project_path / ".factory" / "archive"
    if archive_dir.is_dir():
        for md_file in sorted(archive_dir.rglob("*.md"))[:20]:
            try:
                chunks.append(f"### {md_file.name}\n{md_file.read_text()}")
            except OSError:
                continue
    return _truncate("\n\n".join(chunks))


def _read_ace_playbooks() -> str:
    playbooks_dir = Path.home() / ".factory" / "playbooks"
    if not playbooks_dir.is_dir():
        return ""
    chunks: list[str] = []
    for pb in sorted(playbooks_dir.glob("*.md")):
        try:
            chunks.append(f"### {pb.stem}\n{pb.read_text()}")
        except OSError:
            continue
    return _truncate("\n\n".join(chunks))


def collect_evidence(project_paths: list[Path]) -> dict[str, str]:
    """Collect evidence from multiple projects for profile synthesis.

    Returns 5 sections: experiment_history, ceo_verdicts, auto_memory,
    strategy_observations, ace_playbooks.
    """
    experiment_parts: list[str] = []
    verdict_parts: list[str] = []
    strategy_parts: list[str] = []

    for pp in project_paths:
        name = pp.resolve().name
        tsv = _read_results_tsv(pp)
        if tsv:
            experiment_parts.append(f"## {name}\n{tsv}")
        events = _read_events_jsonl(pp)
        if events:
            verdict_parts.append(f"## {name}\n{events}")
        strat = _read_strategy_archive(pp)
        if strat:
            strategy_parts.append(f"## {name}\n{strat}")

    return {
        "experiment_history": _truncate("\n\n".join(experiment_parts)),
        "ceo_verdicts": _truncate("\n\n".join(verdict_parts)),
        "auto_memory": _read_auto_memory(project_paths),
        "strategy_observations": _truncate("\n\n".join(strategy_parts)),
        "ace_playbooks": _read_ace_playbooks(),
    }


def _build_synthesis_task(evidence: dict[str, str]) -> str:
    parts = ["Synthesize a user profile from the following evidence.\n"]
    for section, content in evidence.items():
        if content.strip():
            parts.append(f"## {section}\n\n{content}\n")
        else:
            parts.append(f"## {section}\n\n(no data)\n")
    return "\n".join(parts)


def save_profile(content: str, source_projects: list[str], runner_name: str) -> Path:
    """Write profile to ~/.factory/profile.md with YAML frontmatter."""
    ts = datetime.now(timezone.utc).isoformat()
    signal_count = sum(1 for line in content.splitlines() if line.strip())
    frontmatter = (
        f"---\n"
        f"generated: {ts}\n"
        f"source_projects:\n"
    )
    for p in source_projects:
        frontmatter += f'  - "{p}"\n'
    frontmatter += (
        f"signal_count: {signal_count}\n"
        f"runner: {runner_name}\n"
        f"---\n\n"
    )
    full_content = frontmatter + content
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(full_content)
    log.info("profile_saved", path=str(_PROFILE_PATH))
    return _PROFILE_PATH


async def synthesize_profile(
    evidence: dict[str, str],
    runner_name: str | None = None,
) -> str:
    """Invoke the profiler agent via headless runner to synthesize a profile."""
    from factory.agents.runner import resolve_prompt
    from factory.runners import get_runner

    prompt = resolve_prompt("profiler")
    task = _build_synthesis_task(evidence)

    runner = get_runner(runner_name)
    result, code = await runner.headless(
        prompt=prompt,
        task=task,
        cwd=Path.cwd(),
        timeout=120.0,
        dangerously_skip_permissions=True,
        role="profiler",
    )

    if code != 0:
        log.warning("profile_synthesis_failed", code=code)
        return f"Profile synthesis failed (exit code {code}):\n{result}"

    return result


def load_profile(path: Path | None = None) -> str | None:
    """Read profile.md, strip YAML frontmatter, return body or None."""
    profile_path = path or _PROFILE_PATH
    if not profile_path.is_file():
        return None
    try:
        text = profile_path.read_text()
    except OSError:
        return None
    if not text.strip():
        return None
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].lstrip("\n")
    return text


def inject_profile(prompt: str, profile: str) -> str:
    """Append a User Profile section to an agent prompt."""
    return (
        f"{prompt}\n\n"
        f"---\n\n"
        f"## User Profile (auto-generated from session history)\n\n"
        f"{profile}"
    )
