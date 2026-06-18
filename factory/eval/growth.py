"""Universal growth eval dimensions applied to every factory-managed project.

These dimensions are injected by the eval runner alongside project-specific
(hygiene) dimensions. They measure capability growth, exploration diversity,
observability, research grounding, and factory effectiveness.

All functions take a project_path and return an EvalResult-compatible dict.
"""

import ast
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path

# Relative weights within the growth category (sum to 1.0).
# The runner normalizes these so that growth gets 50% of the composite.
GROWTH_WEIGHTS = {
    "capability_surface": 0.25,
    "experiment_diversity": 0.20,
    "observability": 0.18,
    "research_grounding": 0.14,
    "factory_effectiveness": 0.13,
    "spec_compliance": 0.10,
}


def eval_capability_surface(project_path: Path) -> dict:
    """Measure breadth of capabilities: modules, public functions, entry points."""
    try:
        # Find Python source directories (skip tests, venvs, hidden dirs)
        src_dirs = _find_src_dirs(project_path)

        modules: list[Path] = []
        for src_dir in src_dirs:
            modules.extend(
                f for f in src_dir.rglob("*.py")
                if f.name != "__init__.py"
                and ".venv" not in f.parts
                and "node_modules" not in f.parts
            )

        # Count public functions via AST
        public_fns = 0
        for mod in modules:
            try:
                tree = ast.parse(mod.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        public_fns += 1

        # Count CLI entry points (argparse add_parser, click commands, etc.)
        entry_points = 0
        for mod in modules:
            try:
                text = mod.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            entry_points += len(re.findall(r'add_parser\("(\w[\w-]*)"', text))
            entry_points += len(re.findall(r"@\w+\.command\(", text))

        surface = len(modules) + public_fns + entry_points
        # Target scales with project size but stays ambitious:
        # modules * 10 means you need ~9 public functions per module to max out
        target = max(100, len(modules) * 10)
        score = min(1.0, surface / target)
        details = (
            f"surface={surface} (modules={len(modules)}, "
            f"public_fns={public_fns}, entry_points={entry_points}), target={target}"
        )

        return {
            "name": "capability_surface",
            "score": round(score, 4),
            "weight": GROWTH_WEIGHTS["capability_surface"],
            "passed": score >= 0.5,
            "details": details,
        }
    except Exception as exc:
        return {
            "name": "capability_surface",
            "score": 0.0,
            "weight": GROWTH_WEIGHTS["capability_surface"],
            "passed": False,
            "details": f"Error: {exc}",
        }


def eval_experiment_diversity(project_path: Path) -> dict:
    """Measure diversity of hypothesis categories in recent experiments."""
    try:
        tsv_path = project_path / ".factory" / "results.tsv"
        if not tsv_path.exists():
            return {
                "name": "experiment_diversity",
                "score": 0.5,
                "weight": GROWTH_WEIGHTS["experiment_diversity"],
                "passed": True,
                "details": "No experiment history yet — neutral score",
            }

        from factory.insights import classify_hypothesis

        with open(tsv_path) as f:
            rows = list(csv.DictReader(f, dialect="excel-tab"))

        if len(rows) < 3:
            return {
                "name": "experiment_diversity",
                "score": 0.5,
                "weight": GROWTH_WEIGHTS["experiment_diversity"],
                "passed": True,
                "details": f"Only {len(rows)} experiments, too few to judge",
            }

        last_n = rows[-10:]
        categories = [classify_hypothesis(r.get("hypothesis", "")) for r in last_n]
        distinct = len(set(categories))

        # Sub-score a: category spread (7+ categories in last 10 = perfect)
        spread = min(1.0, distinct / 7)

        # Sub-score b: anti-repetition (last 3 not all same category)
        last_3_cats = categories[-3:]
        not_stuck = 1.0 if len(set(last_3_cats)) > 1 else 0.0

        # Sub-score c: anti-dominance (penalize if one category dominates)
        cat_counts = Counter(categories)
        most_common_frac = cat_counts.most_common(1)[0][1] / len(last_n)
        anti_dominance = min(1.0, max(0.0, (1.0 - most_common_frac) / 0.7))

        score = 0.4 * spread + 0.3 * not_stuck + 0.3 * anti_dominance
        most_common_cat = cat_counts.most_common(1)[0][0]
        details = (
            f"{distinct} distinct categories in last {len(last_n)} experiments; "
            f"dominant={most_common_cat} ({most_common_frac:.0%}); "
            f"last 3: {last_3_cats}"
        )

        return {
            "name": "experiment_diversity",
            "score": round(score, 4),
            "weight": GROWTH_WEIGHTS["experiment_diversity"],
            "passed": score >= 0.4,
            "details": details,
        }
    except Exception as exc:
        return {
            "name": "experiment_diversity",
            "score": 0.0,
            "weight": GROWTH_WEIGHTS["experiment_diversity"],
            "passed": False,
            "details": f"Error: {exc}",
        }


def eval_observability(project_path: Path) -> dict:
    """Measure logging and observability coverage across project source."""
    try:
        from factory.study import _analyze_observability

        result = _analyze_observability(project_path, "python")
        score = result.get("observability_score", 0.0)
        fn_cov = result.get("function_coverage", 0.0)
        structured = result.get("structured_logging", False)
        details = (
            f"observability_score={score:.2f}, function_coverage={fn_cov:.2f}, "
            f"structured_logging={structured}"
        )

        return {
            "name": "observability",
            "score": round(score, 4),
            "weight": GROWTH_WEIGHTS["observability"],
            "passed": score >= 0.5,
            "details": details,
        }
    except Exception as exc:
        return {
            "name": "observability",
            "score": 0.0,
            "weight": GROWTH_WEIGHTS["observability"],
            "passed": False,
            "details": f"Error: {exc}",
        }


def _collect_archive_sources(archive_dir: Path) -> list[Path]:
    """Collect markdown source files from .factory/archive/ subdirectories."""
    sources: list[Path] = []
    for subdir in ("sources", "research"):
        d = archive_dir / subdir
        if d.exists():
            sources.extend(d.glob("*.md"))
    return sources


def eval_research_grounding(project_path: Path) -> dict:
    """Measure whether improvements are informed by research (vault sources, papers, repos)."""
    try:
        from factory.obsidian.notes import vault_path as _get_vault

        vault = _get_vault()
        archive_dir = project_path / ".factory" / "archive"
        use_archive = vault is None and archive_dir.exists()

        if vault is None and not use_archive:
            return {
                "name": "research_grounding",
                "score": 0.0,
                "weight": 0.16,
                "passed": True,
                "details": "vault not configured — set $FACTORY_VAULT_PATH",
            }

        # Sub-score A: Research knowledge exists
        if vault is not None:
            sources_dir = vault / "20-Knowledge" / "Sources"
            source_files = list(sources_dir.glob("*.md")) if sources_dir.exists() else []
        else:
            source_files = _collect_archive_sources(archive_dir)
        source_count = len(source_files)
        knowledge_score = min(1.0, source_count / 8)

        # Sub-score B: Research informs experiments (keyword match)
        source_keywords: set[str] = set()
        for src in source_files:
            for word in re.split(r"[-_.]", src.stem.lower()):
                if len(word) >= 5:
                    source_keywords.add(word)

        tsv_path = project_path / ".factory" / "results.tsv"
        referenced = 0
        total_checked = 0
        if tsv_path.exists() and source_keywords:
            with open(tsv_path) as f:
                rows = list(csv.DictReader(f, dialect="excel-tab"))
            for row in rows[-10:]:
                hyp = row.get("hypothesis", "").lower()
                summary = row.get("change_summary", "").lower()
                text = hyp + " " + summary
                total_checked += 1
                if any(kw in text for kw in source_keywords):
                    referenced += 1
        utilization = referenced / max(total_checked, 1)

        # Sub-score C: Research report exists
        research_md = project_path / ".factory" / "strategy" / "research.md"
        has_research = 1.0 if research_md.exists() else 0.0

        # Sub-score D: Experiment notes documented
        if vault is not None:
            project_name = project_path.resolve().name
            project_vault = vault / "10-Projects" / project_name
            exp_dir = project_vault / "Experiments"
            exp_dir_count = len(list(exp_dir.glob("*.md"))) if exp_dir.exists() else 0
            flat_count = len(list(project_vault.glob("Exp-*.md"))) if project_vault.exists() else 0
            exp_notes = max(exp_dir_count, flat_count)
        else:
            archive_exp_dir = archive_dir / "experiments"
            exp_notes = len(list(archive_exp_dir.glob("*.md"))) if archive_exp_dir.exists() else 0
        factory_exp_dir = project_path / ".factory" / "experiments"
        exp_total = len(list(factory_exp_dir.iterdir())) if factory_exp_dir.exists() else 0
        doc_ratio = min(1.0, exp_notes / max(exp_total, 1))

        # Sub-score E: Citation ratio — fraction of recent experiments with citations
        from factory.research_index import citation_coverage

        citation_ratio = citation_coverage(project_path)

        score = (
            0.20 * knowledge_score
            + 0.28 * utilization
            + 0.12 * has_research
            + 0.20 * doc_ratio
            + 0.20 * citation_ratio
        )
        source_label = "archive" if use_archive else "vault"
        details = (
            f"sources={source_count} ({source_label}), utilization={utilization:.2f}, "
            f"research_report={'yes' if has_research else 'no'}, "
            f"doc_ratio={doc_ratio:.2f} ({exp_notes}/{max(exp_total, 1)}), "
            f"citation_ratio={citation_ratio:.2f}"
        )

        return {
            "name": "research_grounding",
            "score": round(score, 4),
            "weight": GROWTH_WEIGHTS["research_grounding"],
            "passed": score >= 0.3,
            "details": details,
        }
    except Exception as exc:
        return {
            "name": "research_grounding",
            "score": 0.0,
            "weight": GROWTH_WEIGHTS["research_grounding"],
            "passed": False,
            "details": f"Error: {exc}",
        }


def _discover_managed_projects(project_path: Path) -> int:
    """Count factory-managed projects from multiple sources.

    Sources checked (deduplicated by resolved path):
    1. FACTORY_MANAGED_DIRS env var — colon-separated list of directories to scan
    2. FACTORY_PROJECTS_DIR env var — single directory (legacy)
    3. Sibling directories of the current project (parent dir scan)
    """
    seen: set[Path] = set()

    def _scan_dir(directory: Path) -> None:
        if not directory.is_dir():
            return
        for child in directory.iterdir():
            if child.is_dir() and (child / ".factory" / "results.tsv").exists():
                seen.add(child.resolve())

    from factory.user_config import resolve

    managed_dirs = resolve("managed_dirs", env_var="FACTORY_MANAGED_DIRS") or ""
    if managed_dirs:
        for entry in managed_dirs.split(":"):
            entry = entry.strip()
            if entry:
                _scan_dir(Path(entry))

    projects_dir_str = resolve("projects_dir", env_var="FACTORY_PROJECTS_DIR") or ""
    if projects_dir_str:
        _scan_dir(Path(projects_dir_str))

    parent = project_path.resolve().parent
    _scan_dir(parent)

    seen.discard(project_path.resolve())
    return len(seen)


def eval_factory_effectiveness(project_path: Path) -> dict:
    """Measure whether the factory is effective: keep rate, positive deltas, multi-project reach."""
    try:
        tsv_path = project_path / ".factory" / "results.tsv"
        if not tsv_path.exists():
            return {
                "name": "factory_effectiveness",
                "score": 0.5,
                "weight": GROWTH_WEIGHTS["factory_effectiveness"],
                "passed": True,
                "details": "No experiment history yet — neutral score",
            }

        with open(tsv_path) as f:
            rows = list(csv.DictReader(f, dialect="excel-tab"))

        if len(rows) < 3:
            return {
                "name": "factory_effectiveness",
                "score": 0.5,
                "weight": GROWTH_WEIGHTS["factory_effectiveness"],
                "passed": True,
                "details": f"Only {len(rows)} experiments, too few to judge",
            }

        # Sub-score A: Recent keep rate
        recent = rows[-8:]
        kept = sum(1 for r in recent if r.get("verdict") == "keep")
        keep_rate = kept / len(recent)

        # Sub-score B: Positive deltas
        deltas: list[float] = []
        for r in recent:
            d = r.get("delta", "")
            if d and d.strip():
                try:
                    deltas.append(float(d))
                except ValueError:
                    pass
        if deltas:
            positive = sum(1 for d in deltas if d > 0)
            delta_score = positive / len(deltas)
        else:
            delta_score = 0.5

        # Sub-score C: Multi-project management (auto-discovery)
        managed = _discover_managed_projects(project_path)
        multi_project = min(1.0, managed / 3)

        score = 0.45 * keep_rate + 0.30 * delta_score + 0.25 * multi_project
        details = (
            f"keep_rate={keep_rate:.2f} ({kept}/{len(recent)}), "
            f"delta_score={delta_score:.2f}, "
            f"managed_projects={managed}"
        )

        return {
            "name": "factory_effectiveness",
            "score": round(score, 4),
            "weight": GROWTH_WEIGHTS["factory_effectiveness"],
            "passed": score >= 0.5,
            "details": details,
        }
    except Exception as exc:
        return {
            "name": "factory_effectiveness",
            "score": 0.0,
            "weight": GROWTH_WEIGHTS["factory_effectiveness"],
            "passed": False,
            "details": f"Error: {exc}",
        }


def eval_spec_compliance(project_path: Path) -> dict:
    """Measure eval spec compliance from .factory/spec_results.json.

    Score = passed/total. Returns neutral 0.5 if file is missing or stale (>24h).
    """
    spec_path = project_path / ".factory" / "spec_results.json"
    weight = GROWTH_WEIGHTS["spec_compliance"]

    if not spec_path.exists():
        return {
            "name": "spec_compliance",
            "score": 0.5,
            "weight": weight,
            "passed": True,
            "details": "spec_results.json not found — neutral score",
        }

    try:
        mtime = spec_path.stat().st_mtime
        if time.time() - mtime > 86400:
            return {
                "name": "spec_compliance",
                "score": 0.5,
                "weight": weight,
                "passed": True,
                "details": "spec_results.json is stale (>24h) — neutral score",
            }

        data = json.loads(spec_path.read_text())
        total = int(data.get("total", 0))
        passed = int(data.get("passed", 0))

        if total == 0:
            return {
                "name": "spec_compliance",
                "score": 0.5,
                "weight": weight,
                "passed": True,
                "details": "No spec checks recorded — neutral score",
            }

        score = max(0.0, min(passed / total, 1.0))
        return {
            "name": "spec_compliance",
            "score": round(score, 4),
            "weight": weight,
            "passed": score >= 0.5,
            "details": f"{passed}/{total} spec checks passed",
        }
    except Exception as exc:
        return {
            "name": "spec_compliance",
            "score": 0.5,
            "weight": weight,
            "passed": True,
            "details": f"Error reading spec_results.json: {exc} — neutral score",
        }


def compute_growth_results(project_path: Path) -> list[dict]:
    """Compute all growth dimensions for a project. Returns list of result dicts."""
    return [
        eval_capability_surface(project_path),
        eval_experiment_diversity(project_path),
        eval_observability(project_path),
        eval_research_grounding(project_path),
        eval_factory_effectiveness(project_path),
        eval_spec_compliance(project_path),
    ]


def _find_src_dirs(project_path: Path) -> list[Path]:
    """Find Python source directories in a project (not tests, venvs, etc.)."""
    candidates = []
    # Check for common patterns: src/<name>/, <name>/, app/, lib/
    for child in project_path.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in {
            "tests", "test", "node_modules", ".venv", "venv", "__pycache__",
            "build", "dist", "docs", "doc", "examples", "scripts", "eval",
        }:
            continue
        if any(child.rglob("*.py")):
            candidates.append(child)

    # Also check src/ subdirectory
    src_dir = project_path / "src"
    if src_dir.is_dir():
        for child in src_dir.iterdir():
            if child.is_dir() and any(child.rglob("*.py")):
                candidates.append(child)

    return candidates or [project_path]
