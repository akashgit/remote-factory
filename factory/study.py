"""Study prior interaction logs to inform factory hypotheses."""

from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------- Observability coverage analysis ----------


def _find_source_files(project_path: Path, language: str) -> list[Path]:
    """Find source files (excluding tests, venvs, generated code)."""
    skip_dirs = {
        "tests", "test", ".venv", "venv", "node_modules", "__pycache__",
        ".git", ".factory", "eval", "dist", "build", ".mypy_cache",
    }
    ext = {
        "python": ".py",
        "typescript": ".ts",
        "go": ".go",
        "rust": ".rs",
    }.get(language, ".py")

    sources: list[Path] = []
    for f in project_path.rglob(f"*{ext}"):
        if any(part in skip_dirs for part in f.relative_to(project_path).parts):
            continue
        sources.append(f)
    return sources


def _count_functions_python(source: str) -> int:
    """Count function and method definitions in Python source using AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip dunder methods and private helpers under 3 lines
            if not node.name.startswith("__"):
                count += 1
    return count


def _count_functions_generic(source: str) -> int:
    """Count function definitions using regex (for non-Python languages)."""
    # Match common function patterns: def, fn, func, function, async fn, etc.
    patterns = [
        r"\bdef\s+\w+",
        r"\bfn\s+\w+",
        r"\bfunc\s+\w+",
        r"\bfunction\s+\w+",
        r"\basync\s+function\s+\w+",
        r"(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s+)?\(",
    ]
    total = 0
    for p in patterns:
        total += len(re.findall(p, source))
    return total


def _analyze_file_observability(path: Path, language: str) -> dict:
    """Analyze a single source file for observability patterns.

    Returns dict with: functions, logged_functions, has_structured_logging,
    has_request_tracing, log_statements, patterns_found.
    """
    try:
        source = path.read_text(errors="replace")
    except OSError:
        return {"functions": 0, "logged_functions": 0, "log_statements": 0, "patterns_found": []}

    # Count functions
    if language == "python":
        func_count = _count_functions_python(source)
    else:
        func_count = _count_functions_generic(source)

    # Count log statements
    log_patterns = [
        r"\blogger\.\w+\(",           # logger.info(), logger.error(), etc.
        r"\blogging\.\w+\(",          # logging.info(), etc.
        r"\blog\.\w+\(",              # log.info(), etc.
        r"\bconsole\.\w+\(",          # console.log(), etc. (JS/TS)
        r"\bprint\(",                 # print() as logging (weak signal)
        r"\bslog\.\w+\(",            # Go slog
        r"\btracing::\w+!",           # Rust tracing
    ]
    log_stmt_count = 0
    for p in log_patterns:
        log_stmt_count += len(re.findall(p, source))

    # Detect structured logging
    structured_patterns = {
        "structlog": r"\bstructlog\b",
        "json_logger": r"\bjson.logger\b|python.json.logger",
        "structured_fields": r"logger\.\w+\([^)]*\w+=\w+",  # logger.info("event", key=val)
        "slog": r"\bslog\.\w+\(",
        "pino": r"\bpino\b",
        "winston": r"\bwinston\b",
        "tracing_crate": r"\btracing::",
    }
    patterns_found: list[str] = []
    for name, pattern in structured_patterns.items():
        if re.search(pattern, source):
            patterns_found.append(name)

    has_structured = bool(patterns_found)

    # Detect request tracing
    tracing_patterns = {
        "request_id": r"request.id|req.id|trace.id|correlation.id|x.request.id",
        "contextvars": r"\bcontextvars\b|ContextVar",
        "opentelemetry": r"\bopentelemetry\b|from opentelemetry",
        "trace_context": r"trace.context|TraceContext|span",
    }
    tracing_found: list[str] = []
    for name, pattern in tracing_patterns.items():
        if re.search(pattern, source, re.IGNORECASE):
            tracing_found.append(name)

    # Estimate "logged functions" — functions near a log statement
    # Simple heuristic: split source into function blocks, check for log calls
    logged_functions = 0
    if language == "python":
        try:
            tree = ast.parse(source)
            lines = source.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("__"):
                        continue
                    func_start = node.lineno - 1
                    func_end = node.end_lineno or func_start + 1
                    func_body = "\n".join(lines[func_start:func_end])
                    for p in log_patterns:
                        if re.search(p, func_body):
                            logged_functions += 1
                            break
        except SyntaxError:
            pass
    else:
        # Rough heuristic for non-Python: assume 50% of log statements are in functions
        logged_functions = min(func_count, log_stmt_count // 2) if log_stmt_count else 0

    return {
        "functions": func_count,
        "logged_functions": logged_functions,
        "log_statements": log_stmt_count,
        "has_structured_logging": has_structured,
        "has_request_tracing": bool(tracing_found),
        "patterns_found": patterns_found + tracing_found,
    }


def _analyze_observability(project_path: Path, language: str = "python") -> dict:
    """Analyze observability coverage across all source files.

    Returns a summary dict with:
    - observability_score: 0.0-1.0 composite
    - function_coverage: fraction of functions with logging
    - total_functions, logged_functions, total_log_statements
    - has_structured_logging, has_request_tracing
    - logging_framework: detected framework name or None
    - gaps: list of files with low observability
    - recommendations: prioritized list of improvements
    """
    if language == "unknown":
        # Try to detect from project files
        if (project_path / "pyproject.toml").exists():
            language = "python"
        elif (project_path / "package.json").exists():
            language = "typescript"

    sources = _find_source_files(project_path, language)
    if not sources:
        return {
            "observability_score": 0.0,
            "function_coverage": 0.0,
            "total_functions": 0,
            "logged_functions": 0,
            "total_log_statements": 0,
            "has_structured_logging": False,
            "has_request_tracing": False,
            "logging_framework": None,
            "gaps": [],
            "recommendations": ["No source files found to analyze."],
        }

    total_functions = 0
    total_logged = 0
    total_log_stmts = 0
    all_patterns: list[str] = []
    has_structured = False
    has_tracing = False
    gaps: list[str] = []

    for src in sources:
        analysis = _analyze_file_observability(src, language)
        total_functions += analysis["functions"]
        total_logged += analysis["logged_functions"]
        total_log_stmts += analysis["log_statements"]
        all_patterns.extend(analysis["patterns_found"])
        if analysis["has_structured_logging"]:
            has_structured = True
        if analysis["has_request_tracing"]:
            has_tracing = True

        # Flag files with functions but no logging
        if analysis["functions"] > 0 and analysis["log_statements"] == 0:
            rel = str(src.relative_to(project_path))
            gaps.append(f"{rel} ({analysis['functions']} functions, 0 log statements)")

    # Detect logging framework
    framework = None
    pattern_counts: dict[str, int] = {}
    for p in all_patterns:
        pattern_counts[p] = pattern_counts.get(p, 0) + 1
    if "structlog" in pattern_counts:
        framework = "structlog"
    elif "json_logger" in pattern_counts:
        framework = "python-json-logger"
    elif "pino" in pattern_counts:
        framework = "pino"
    elif "winston" in pattern_counts:
        framework = "winston"
    elif "slog" in pattern_counts:
        framework = "slog"
    elif "tracing_crate" in pattern_counts:
        framework = "tracing"

    # Compute scores
    func_coverage = total_logged / total_functions if total_functions > 0 else 0.0
    log_density = min(1.0, total_log_stmts / max(total_functions, 1))

    # Composite: 40% function coverage, 25% structured logging, 20% request tracing,
    # 15% log density
    observability_score = (
        0.40 * func_coverage
        + 0.25 * (1.0 if has_structured else 0.0)
        + 0.20 * (1.0 if has_tracing else 0.0)
        + 0.15 * log_density
    )

    # Generate recommendations
    recommendations: list[str] = []
    if not has_structured:
        recommendations.append(
            "Add structured logging (structlog for Python, pino for Node.js) "
            "for machine-parseable log output"
        )
    if not has_tracing:
        recommendations.append(
            "Add request ID tracing (contextvars + unique ID per request) "
            "for end-to-end request correlation"
        )
    if func_coverage < 0.5:
        recommendations.append(
            f"Improve logging coverage: only {total_logged}/{total_functions} functions "
            f"({func_coverage:.0%}) have log statements"
        )
    if gaps:
        top_gaps = gaps[:5]
        recommendations.append(
            f"Add logging to uninstrumented files: {', '.join(top_gaps)}"
        )
    if not recommendations:
        recommendations.append("Observability looks good — all key patterns present")

    return {
        "observability_score": round(observability_score, 3),
        "function_coverage": round(func_coverage, 3),
        "total_functions": total_functions,
        "logged_functions": total_logged,
        "total_log_statements": total_log_stmts,
        "has_structured_logging": has_structured,
        "has_request_tracing": has_tracing,
        "logging_framework": framework,
        "gaps": gaps,
        "recommendations": recommendations,
    }


def _path_to_slug(project_path: Path) -> str:
    """Convert a project path to Claude's directory slug format.

    Claude replaces all non-alphanumeric chars (except -) with -.
    e.g. /home/dev/projects/my-app
      -> -home-dev-projects-my-app
    """
    return "".join(c if c.isalnum() or c == "-" else "-" for c in str(project_path))


def _find_log_files(project_path: Path) -> list[Path]:
    """Find Claude conversation logs matching this project."""
    claude_projects = Path.home() / ".claude" / "projects"
    slug = _path_to_slug(project_path.resolve())

    project_dir = claude_projects / slug
    if not project_dir.exists():
        logger.warning("No Claude project directory found at %s", project_dir)
        return []

    return sorted(project_dir.glob("*.jsonl"))


def _extract_messages(log_file: Path) -> list[dict]:
    """Extract user messages and errors from a JSONL log file."""
    messages: list[dict] = []
    with open(log_file) as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            content = msg.get("message", {}).get("content", "")

            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block["text"]

            text = text.strip()
            if not text or len(text) > 2000:
                continue

            # Skip system prompts and skill loads
            if text.startswith("Base directory") or text.startswith("<task-notification"):
                continue

            if msg_type == "user":
                messages.append({"role": "user", "text": text[:500]})
            elif msg_type == "assistant":
                # Extract error mentions
                error_keywords = ["error", "failed", "bug", "fix", "broken"]
                if any(kw in text.lower() for kw in error_keywords):
                    error_lines = [
                        line.strip()
                        for line in text.split("\n")
                        if any(kw in line.lower() for kw in error_keywords)
                        and line.strip()
                        and len(line.strip()) < 300
                    ]
                    for el in error_lines[:3]:
                        messages.append({"role": "error", "text": el})

    return messages


def _extract_keywords(project_path: Path) -> list[str]:
    """Extract search keywords from a project's README or pyproject.toml."""
    # Try README first
    readme = project_path / "README.md"
    if readme.exists():
        text = readme.read_text(errors="replace")[:2000]
        # Use the first heading and first paragraph as keyword source
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        # Strip markdown heading markers
        lines = [re.sub(r"^#+\s*", "", ln) for ln in lines[:5]]
        text = " ".join(lines)
    else:
        # Fall back to pyproject.toml name + description
        pyproject = project_path / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(errors="replace")
            name_match = re.search(r'name\s*=\s*"([^"]+)"', content)
            desc_match = re.search(r'description\s*=\s*"([^"]+)"', content)
            parts = []
            if name_match:
                parts.append(name_match.group(1).replace("-", " "))
            if desc_match:
                parts.append(desc_match.group(1))
            text = " ".join(parts) if parts else ""
        else:
            text = project_path.name.replace("-", " ").replace("_", " ")

    if not text:
        return []

    # Remove common stop words and short tokens, keep meaningful words
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "and",
        "but", "or", "nor", "not", "so", "yet", "both", "either", "neither",
        "this", "that", "these", "those", "it", "its", "my", "your", "his",
        "her", "our", "their", "what", "which", "who", "whom", "how",
    }
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    keywords = [w for w in words if w not in stop_words]
    # Deduplicate while preserving order, return up to 5
    seen: set[str] = set()
    unique: list[str] = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
        if len(unique) >= 5:
            break
    return unique


def _search_similar_projects(project_path: Path) -> list[dict]:
    """Search GitHub for similar projects using `gh search repos`.

    Returns top 5 results as dicts with keys: name, url, description, stars.
    Gracefully returns empty list if gh is not available or search fails.
    """
    keywords = _extract_keywords(project_path)
    if not keywords:
        return []

    query = " ".join(keywords)
    try:
        result = subprocess.run(
            [
                "gh", "search", "repos", query,
                "--limit", "5",
                "--json", "fullName,url,description,stargazersCount",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("gh CLI not available or search timed out")
        return []

    if result.returncode != 0:
        logger.debug("gh search repos failed: %s", result.stderr)
        return []

    try:
        repos = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    return [
        {
            "name": r.get("fullName", ""),
            "url": r.get("url", ""),
            "description": (r.get("description") or "")[:200],
            "stars": r.get("stargazersCount", 0),
        }
        for r in repos[:5]
    ]


def _fetch_open_issues(project_path: Path) -> list[dict]:
    """Fetch open GitHub issues for the project's repo.

    Returns a list of dicts with keys: number, title, labels, body (truncated).
    Gracefully returns empty list if gh is unavailable, not a GitHub repo, or fetch fails.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--state", "open",
                "--limit", "20",
                "--json", "number,title,labels,body",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=project_path,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("gh CLI not available or issue list timed out")
        return []

    if result.returncode != 0:
        logger.debug("gh issue list failed: %s", result.stderr)
        return []

    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    return [
        {
            "number": i.get("number", 0),
            "title": i.get("title", ""),
            "labels": [lb.get("name", "") for lb in (i.get("labels") or [])],
            "body": (i.get("body") or "")[:300],
        }
        for i in issues
    ]


def _read_obsidian_notes(project_name: str) -> list[str]:
    """Read Obsidian vault notes for this project.

    Returns a list of note summaries (first 200 chars of each note).
    Gracefully returns empty list if vault doesn't exist.
    """
    from factory.obsidian.notes import (
        _get_vault_path,
        _KNOWLEDGE_DIR,
        _PROJECTS_DIR,
        obsidian_search_vault,
    )

    # Try obsidian-cli search first
    search_result = obsidian_search_vault(project_name)
    if search_result and "no matches" not in search_result.lower():
        summaries: list[str] = []
        for line in search_result.strip().split("\n"):
            line = line.strip()
            if line and len(line) > 5:
                summaries.append(line[:200])
        if summaries:
            return summaries

    # Fall back to direct file reading
    vault = _get_vault_path()
    if vault is None or not vault.exists():
        return []

    file_summaries: list[str] = []

    # Project-specific notes
    project_dir = vault / _PROJECTS_DIR / project_name
    for subdir in ["Experiments", "Strategies"]:
        notes_dir = project_dir / subdir
        if not notes_dir.exists():
            continue
        for note_path in sorted(notes_dir.glob(f"{project_name}*.md")):
            try:
                content = note_path.read_text(errors="replace")
                # Skip frontmatter
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end != -1:
                        content = content[end + 3:].strip()
                summary = content[:200].strip()
                if summary:
                    file_summaries.append(summary)
            except OSError:
                continue

    # Project dashboard
    dashboard = project_dir / f"{project_name}.md"
    if dashboard.exists():
        try:
            content = dashboard.read_text(errors="replace")
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    content = content[end + 3:].strip()
            summary = content[:200].strip()
            if summary:
                file_summaries.append(summary)
        except OSError:
            pass

    # Cross-project knowledge
    knowledge_dir = vault / _KNOWLEDGE_DIR / "Concepts"
    if knowledge_dir.exists():
        for note_path in sorted(knowledge_dir.glob("*.md")):
            try:
                content = note_path.read_text(errors="replace")
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end != -1:
                        content = content[end + 3:].strip()
                summary = content[:200].strip()
                if summary:
                    file_summaries.append(summary)
            except OSError:
                continue

    return file_summaries


def _detect_self_improvement(project_path: Path) -> bool:
    """Return True if the target project is the factory itself."""
    return (
        (project_path / "factory" / "cli.py").exists()
        and (project_path / "factory" / "insights.py").exists()
    )


def _load_cross_project_insights(
    project_path: Path,
    projects_dir: Path,
) -> str:
    """Load and format cross-project insights. Writes insights.md as side effect."""
    from factory.insights import (
        analyze,
        discover_projects,
        format_insights,
        load_all_histories,
    )

    project_paths = discover_projects(projects_dir)
    if not project_paths:
        return ""

    histories = load_all_histories(project_paths)
    if not histories:
        return ""

    insights = analyze(histories)
    report = format_insights(insights)

    # Write insights.md as side effect
    out_path = project_path / ".factory" / "strategy" / "insights.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)

    # Return a summary for inclusion in observations
    total_exp = sum(p.experiment_count for p in insights.projects)
    total_kept = sum(p.keep_count for p in insights.projects)
    overall_rate = total_kept / total_exp if total_exp > 0 else 0.0
    project_names = [p.name for p in insights.projects]

    summary_lines = [
        "## Cross-Project Insights",
        "",
        f"Analyzed {len(insights.projects)} projects ({', '.join(project_names)}), "
        f"{total_exp} experiments, {overall_rate:.0%} overall keep rate.",
        "",
    ]

    if insights.winning_categories:
        summary_lines.append(
            f"**Winning categories:** {', '.join(insights.winning_categories)}"
        )
    if insights.losing_categories:
        summary_lines.append(
            f"**Risky categories:** {', '.join(insights.losing_categories)}"
        )
    if insights.patterns:
        summary_lines.append("")
        summary_lines.append("**Patterns:**")
        for p in insights.patterns[:5]:
            summary_lines.append(f"- {p.name}: {p.description}")

    summary_lines.append("")
    summary_lines.append(f"Full report: {out_path}")
    return "\n".join(summary_lines)


def study_project_local(project_path: Path, **kwargs: object) -> str:
    """Read interaction logs and produce an observations summary (local only)."""
    log_files = _find_log_files(project_path)

    all_messages: list[dict] = []
    for lf in log_files:
        all_messages.extend(_extract_messages(lf))

    # Categorize
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    errors = [m for m in all_messages if m["role"] == "error"]

    lines = [
        f"# Interaction Study — {project_path.name}",
        "",
    ]

    if log_files:
        lines.append(
            f"Analyzed {len(log_files)} conversation log(s), "
            f"{len(all_messages)} relevant messages."
        )
        lines.append("")
        lines.append(f"## User Messages ({len(user_msgs)})")
        for m in user_msgs:
            lines.append(f"- {m['text'][:200]}")

        lines.extend([
            "",
            f"## Errors and Issues ({len(errors)})",
        ])
        for m in errors:
            lines.append(f"- {m['text'][:200]}")
    else:
        lines.append("No interaction logs found.")

    # Similar projects from GitHub
    similar = _search_similar_projects(project_path)
    lines.extend(["", "## Similar Projects"])
    if similar:
        for proj in similar:
            stars = proj.get("stars", 0)
            desc = proj.get("description", "")
            desc_part = f" — {desc}" if desc else ""
            lines.append(f"- [{proj['name']}]({proj['url']}) ({stars} stars){desc_part}")
    else:
        lines.append("No similar projects found.")

    # Open GitHub issues
    open_issues = _fetch_open_issues(project_path)
    lines.extend(["", "## Open GitHub Issues"])
    if open_issues:
        lines.append(f"{len(open_issues)} open issue(s):")
        lines.append("")
        for issue in open_issues:
            label_str = ""
            if issue["labels"]:
                label_str = f" [{', '.join(issue['labels'])}]"
            lines.append(f"- **#{issue['number']}** {issue['title']}{label_str}")
            if issue["body"]:
                body_preview = issue["body"].replace("\n", " ").strip()
                if body_preview:
                    lines.append(f"  > {body_preview}")
    else:
        lines.append("No open issues found (or not a GitHub repo).")

    # Observability coverage analysis
    from factory.discovery.introspect import _detect_language
    language = _detect_language(project_path)
    obs = _analyze_observability(project_path, language)

    lines.extend(["", "## Observability Coverage"])
    lines.append(f"- **Score:** {obs['observability_score']:.1%}")
    lines.append(
        f"- **Function coverage:** {obs['logged_functions']}/{obs['total_functions']} "
        f"functions have logging ({obs['function_coverage']:.0%})"
    )
    lines.append(f"- **Total log statements:** {obs['total_log_statements']}")
    lines.append(f"- **Structured logging:** {'Yes' if obs['has_structured_logging'] else 'No'}")
    if obs["logging_framework"]:
        lines.append(f"- **Framework:** {obs['logging_framework']}")
    lines.append(f"- **Request tracing:** {'Yes' if obs['has_request_tracing'] else 'No'}")

    if obs["gaps"]:
        lines.extend(["", "### Uninstrumented Files"])
        for gap in obs["gaps"][:10]:
            lines.append(f"- {gap}")

    if obs["recommendations"]:
        lines.extend(["", "### Observability Recommendations"])
        for rec in obs["recommendations"]:
            lines.append(f"- {rec}")

    # Prior knowledge from Obsidian vault
    project_name = project_path.name
    notes = _read_obsidian_notes(project_name)
    lines.extend(["", "## Prior Knowledge (Obsidian)"])
    if notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("No prior notes found.")

    # Cross-project insights
    projects_dir = kwargs.get("projects_dir")
    if projects_dir:
        insights_text = _load_cross_project_insights(project_path, Path(str(projects_dir)))
        if insights_text:
            lines.extend(["", insights_text])

    # Self-improvement context
    if _detect_self_improvement(project_path):
        lines.extend([
            "",
            "## Self-Improvement Context",
            "",
            "This project IS the factory. The Strategist should explore the full design space:",
            "",
            "| Dimension | Description |",
            "|---|---|",
            "| Features | New user-facing capabilities |",
            "| Bug fixes | Crash fixes, error handling |",
            "| Instrumentation | Logging, tracing, telemetry |",
            "| Flow changes | Architectural refactors |",
            "| New agents | Adding or splitting agent roles |",
            "| Prompt engineering | Agent prompt rewrites |",
            "| Eval improvements | Scoring refinements, new dimensions |",
            "| Knowledge management | Vault structure, archival quality |",
            "| Infrastructure | CI/CD, tmux, scheduling |",
            "| Self-evolution | Meta-learning, self-analysis |",
            "",
            "Prioritize: Self-evolution, Prompt engineering, Knowledge management.",
        ])

    # Hypothesis budget recommendation
    base_budget = 3
    issue_bonus = len(open_issues) // 3  # +1 per 3 open issues
    budget = min(base_budget + issue_bonus, 5)
    lines.extend([
        "",
        "## Hypothesis Budget",
        "",
        f"**Recommended hypotheses: {budget}**",
        "",
        "| Factor | Count | Effect |",
        "|--------|-------|--------|",
        f"| Base budget | — | {base_budget} hypotheses |",
    ])
    if open_issues:
        lines.append(
            f"| Open GitHub issues | {len(open_issues)} | +{issue_bonus} "
            f"(+1 per 3 open issues) |"
        )
    lines.extend([
        f"| **Total (capped at 5)** | | **{budget}** |",
        "",
    ])
    if open_issues:
        lines.append(
            "The Strategist SHOULD address open GitHub issues as FIX hypotheses "
            "in addition to improvement hypotheses. Issues represent known user-reported "
            "problems and feature requests — they are high-signal input."
        )

    return "\n".join(lines)


def study_project(project_path: Path, **kwargs: object) -> str:
    """Study a project — local analysis. Deep research available via researcher subagent."""
    return study_project_local(project_path, **kwargs)
