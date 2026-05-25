"""Contribution pipeline for meta mode.

Classifies evolved playbook items as general (upstream PR candidates) vs
project-specific (local only), generates diffs, packages evidence, and
can submit PRs to the factory repo.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from factory.ace.models import Playbook, PlaybookItem
from factory.ace.paths import DEFAULTS_DIR, user_playbooks_dir
from factory.insights import classify_hypothesis, discover_projects, load_all_histories
from factory.models import ExperimentRecord

log = structlog.get_logger()

# ── Constants ──────────────────────────────────────────────────────

FACTORY_INTERNAL_KEYWORDS: set[str] = {
    "prompt", "agent", "eval", "hypothesis", "experiment", "score",
    "playbook", "reviewer", "builder", "strategist", "evaluator",
    "archivist", "researcher", "ceo", "guard", "precheck", "ace",
    "meta", "factory", "type checker", "linter", "tests", "ci",
    "type check", "lint", "precommit",
}

DOMAIN_SPECIFIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\breact\b", r"\bangular\b", r"\bvue\b", r"\bdjango\b",
        r"\brails\b", r"\bflask\b", r"\bfastapi\b", r"\bplaywright\b",
        r"\bselenium\b", r"\bpuppeteer\b", r"\bgraphql\b",
        r"\bpostgres(?:ql)?\b", r"\bmongodb\b", r"\bredis\b",
        r"\bdocker\b", r"\bkubernetes\b", r"\biframe\b",
        r"\bwebpack\b", r"\bnext\.?js\b", r"\bexpress\b",
        r"\bspring\b", r"\blaravel\b", r"\bswift(?:ui)?\b",
        r"\bkotlin\b", r"\bmigration\b",
    ]
]

_CATEGORY_SCORES: dict[str, float] = {
    "prompt_engineering": 0.9,
    "agent_improvement": 0.9,
    "eval_improvement": 0.9,
    "infrastructure": 0.9,
    "observability": 0.7,
    "coverage": 0.7,
    "testing": 0.7,
    "lint": 0.7,
    "type_safety": 0.7,
    "refactoring": 0.5,
    "performance": 0.5,
    "feature": 0.3,
    "bugfix": 0.5,
}

_WEIGHTS = {
    "cross_project_prevalence": 0.40,
    "domain_independence": 0.25,
    "evidence_strength": 0.20,
    "category_signal": 0.15,
}

_CANDIDATES_FILE = ".factory/contribution_candidates.json"


# ── Models ─────────────────────────────────────────────────────────


class ClassifiedItem(BaseModel):
    """A PlaybookItem with generality classification metadata.

    Uses composition since PlaybookItem has extra='forbid'.
    """

    item: PlaybookItem
    role: str = ""
    generality_score: float = 0.0
    classification: Literal["general", "specific", "uncertain"] = "uncertain"
    source_projects: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @property
    def cross_project_count(self) -> int:
        return len(set(self.source_projects))


class EvidencePackage(BaseModel):
    """Cross-project evidence supporting a contribution candidate."""

    cross_project_stats: dict[str, float] = Field(default_factory=dict)
    total_experiments: int = 0
    total_projects: int = 0
    example_experiments: list[str] = Field(default_factory=list)
    category: str = ""
    confidence: float = 0.0


class ContributionReport(BaseModel):
    """Classification results from meta mode evolution analysis."""

    general_items: list[ClassifiedItem] = Field(default_factory=list)
    specific_items: list[ClassifiedItem] = Field(default_factory=list)
    uncertain_items: list[ClassifiedItem] = Field(default_factory=list)
    generated_at: str = ""


# ── Fuzzy matching ─────────────────────────────────────────────────


def _fuzzy_match(text_a: str, text_b: str, threshold: float = 0.75) -> bool:
    """Match using SequenceMatcher, consistent with reflector.py."""
    return difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio() >= threshold


# ── Classification ─────────────────────────────────────────────────


def score_cross_project_prevalence(
    classified: ClassifiedItem,
    cross_project_data: dict[str, list[ExperimentRecord]],
) -> float:
    """Score based on how many projects show evidence for this item.

    Linear scoring: min(1.0, matching_projects / 3), with a 0.1 bonus
    when the cross-project keep rate is >= 80%.
    """
    matching_projects: list[str] = []
    kept_count = 0
    total_matches = 0

    for project_name, experiments in cross_project_data.items():
        project_matched = False
        for exp in experiments:
            if _fuzzy_match(classified.item.content, exp.hypothesis):
                project_matched = True
                total_matches += 1
                if exp.verdict == "keep":
                    kept_count += 1
        if project_matched:
            matching_projects.append(project_name)

    classified.source_projects = matching_projects

    if not matching_projects:
        return 0.0

    score = min(1.0, len(matching_projects) / 3)
    if total_matches > 0 and (kept_count / total_matches) >= 0.80:
        score = min(1.0, score + 0.1)

    return score


def score_domain_independence(classified: ClassifiedItem) -> float:
    """Score based on whether content is factory-internal vs domain-specific.

    Returns ratio of factory keyword matches to total keyword matches.
    Returns 0.5 when no keywords match either set.
    """
    text_lower = classified.item.content.lower()
    factory_matches = sum(1 for kw in FACTORY_INTERNAL_KEYWORDS if kw in text_lower)
    domain_matches = sum(1 for p in DOMAIN_SPECIFIC_PATTERNS if p.search(classified.item.content))

    total = factory_matches + domain_matches
    if total == 0:
        return 0.5
    return factory_matches / total


def score_evidence_strength(classified: ClassifiedItem) -> float:
    """Score based on helpful/harmful counters and observation volume."""
    total = classified.item.helpful + classified.item.harmful

    if total < 3:
        return 0.2

    net_ratio = classified.item.helpful / max(1, total)
    volume_bonus = min(1.0, total / 10)
    score = 0.6 * net_ratio + 0.4 * volume_bonus

    if classified.item.helpful > 0 and classified.item.harmful > 0:
        score = max(0.0, score - 0.2)

    return score


def score_category_signal(classified: ClassifiedItem) -> float:
    """Score based on the hypothesis category's inherent generality."""
    category = classify_hypothesis(classified.item.content)
    return _CATEGORY_SCORES.get(category, 0.5)


def classify_item(
    item: PlaybookItem,
    cross_project_data: dict[str, list[ExperimentRecord]],
    role: str = "",
) -> ClassifiedItem:
    """Classify a single playbook item on the general-vs-specific spectrum.

    Weighted composite: cross-project prevalence (40%), domain independence (25%),
    evidence strength (20%), category signal (15%).
    """
    classified = ClassifiedItem(item=item, role=role)

    signals = {
        "cross_project_prevalence": score_cross_project_prevalence(classified, cross_project_data),
        "domain_independence": score_domain_independence(classified),
        "evidence_strength": score_evidence_strength(classified),
        "category_signal": score_category_signal(classified),
    }

    classified.generality_score = sum(signals[k] * _WEIGHTS[k] for k in signals)

    if classified.generality_score >= 0.65:
        classified.classification = "general"
    elif classified.generality_score <= 0.35:
        classified.classification = "specific"
    else:
        classified.classification = "uncertain"

    return classified


def classify_evolved_playbooks(
    project_path: Path,
) -> ContributionReport:
    """Full pipeline: load playbooks, load cross-project data, classify all items.

    Discovers sibling projects to build cross-project evidence, loads the
    user's evolved playbooks, diffs them against factory defaults, and
    classifies each evolved item.
    """
    projects_dir = project_path.parent
    project_paths = discover_projects(projects_dir)
    cross_project_data = load_all_histories(project_paths) if project_paths else {}

    evolved_dir = user_playbooks_dir()
    general: list[ClassifiedItem] = []
    specific: list[ClassifiedItem] = []
    uncertain: list[ClassifiedItem] = []

    for pb_file in sorted(evolved_dir.glob("*.md")):
        role = pb_file.stem
        evolved = Playbook.from_markdown(pb_file.read_text())

        default_path = DEFAULTS_DIR / f"{role}.md"
        default = (
            Playbook.from_markdown(default_path.read_text())
            if default_path.exists()
            else Playbook.empty(role)
        )

        diffs = diff_playbooks(evolved, default)
        for d in diffs:
            evolved_item = d.get("evolved_item")
            if evolved_item is None:
                continue

            classified = classify_item(evolved_item, cross_project_data, role=role)

            if classified.classification == "general":
                general.append(classified)
            elif classified.classification == "specific":
                specific.append(classified)
            else:
                uncertain.append(classified)

    log.info(
        "classify_evolved_playbooks_complete",
        general=len(general),
        specific=len(specific),
        uncertain=len(uncertain),
    )

    return ContributionReport(
        general_items=general,
        specific_items=specific,
        uncertain_items=uncertain,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Diff & Contribution ───────────────────────────────────────────


def diff_playbooks(
    evolved: Playbook,
    default: Playbook,
) -> list[dict]:
    """Diff evolved playbooks against factory defaults.

    Matches items by fuzzy content similarity (SequenceMatcher >= 0.75)
    since IDs are reassigned by the curator across evolution cycles.

    Returns list of dicts with keys: diff_type, evolved_item, default_item, diff_text.
    """
    diffs: list[dict] = []
    matched_default_indices: set[int] = set()

    for evolved_item in evolved.items:
        best_match_idx: int | None = None
        best_ratio = 0.0

        for i, default_item in enumerate(default.items):
            if i in matched_default_indices:
                continue
            ratio = difflib.SequenceMatcher(
                None, evolved_item.content.lower(), default_item.content.lower()
            ).ratio()
            if ratio >= 0.75 and ratio > best_ratio:
                best_ratio = ratio
                best_match_idx = i

        if best_match_idx is not None:
            matched_default_indices.add(best_match_idx)
            matched = default.items[best_match_idx]
            if (
                evolved_item.content != matched.content
                or evolved_item.helpful != matched.helpful
                or evolved_item.harmful != matched.harmful
            ):
                diffs.append({
                    "diff_type": "modified",
                    "evolved_item": evolved_item,
                    "default_item": matched,
                    "diff_text": _make_unified_diff(matched, evolved_item),
                })
        else:
            diffs.append({
                "diff_type": "added",
                "evolved_item": evolved_item,
                "default_item": None,
                "diff_text": _make_unified_diff(None, evolved_item),
            })

    for i, default_item in enumerate(default.items):
        if i not in matched_default_indices:
            diffs.append({
                "diff_type": "removed",
                "evolved_item": None,
                "default_item": default_item,
                "diff_text": _make_unified_diff(default_item, None),
            })

    return diffs


def _make_unified_diff(
    default_item: PlaybookItem | None,
    evolved_item: PlaybookItem | None,
) -> str:
    old = [default_item.to_line()] if default_item else []
    new = [evolved_item.to_line()] if evolved_item else []
    return "\n".join(difflib.unified_diff(
        old, new, fromfile="default", tofile="evolved", lineterm=""
    ))


def package_evidence(
    classified: ClassifiedItem,
    cross_project_data: dict[str, list[ExperimentRecord]],
) -> EvidencePackage:
    """Assemble cross-project evidence for a contribution candidate."""
    stats: dict[str, float] = {}
    examples: list[str] = []
    total_matching = 0

    for project_name, experiments in cross_project_data.items():
        matching = [
            e for e in experiments
            if _fuzzy_match(classified.item.content, e.hypothesis)
        ]
        if not matching:
            continue

        kept = sum(1 for e in matching if e.verdict == "keep")
        stats[project_name] = round(kept / len(matching), 2)
        total_matching += len(matching)

        for exp in matching:
            if len(examples) < 5:
                examples.append(f"{project_name}: {exp.hypothesis}")

    return EvidencePackage(
        cross_project_stats=stats,
        total_experiments=total_matching,
        total_projects=len(stats),
        example_experiments=examples,
        category=classify_hypothesis(classified.item.content),
        confidence=round(min(1.0, total_matching / 10), 2),
    )


def generate_pr_body(
    candidates: list[ClassifiedItem],
    evidence_map: dict[str, EvidencePackage] | None = None,
) -> str:
    """Generate a structured PR body with evidence for upstream contribution."""
    if not candidates:
        return (
            "## Meta Mode Contribution\n\n"
            "No items were identified as generally useful for upstream contribution.\n"
        )

    if evidence_map is None:
        evidence_map = {}

    total_projects = len({p for c in candidates for p in c.source_projects})

    lines: list[str] = [
        "## Meta Mode Contribution\n",
        f"These playbook improvements were identified as generally useful across "
        f"{total_projects} projects during meta mode evolution.\n",
        "### Changes\n",
    ]

    by_role: dict[str, list[ClassifiedItem]] = {}
    for c in candidates:
        by_role.setdefault(c.role, []).append(c)

    for role in sorted(by_role):
        lines.append(f"#### {role.title()} Playbook\n")
        for c in by_role[role]:
            ev = evidence_map.get(c.item.id)
            if ev and ev.total_projects > 0:
                avg_keep = sum(ev.cross_project_stats.values()) / len(ev.cross_project_stats)
                lines.append(
                    f'- "{c.item.content}"\n'
                    f"  - Evidence: {avg_keep:.0%} keep rate across "
                    f"{ev.total_projects} projects ({ev.total_experiments} experiments)\n"
                    f"  - Generality score: {c.generality_score:.2f}\n"
                )
            else:
                lines.append(
                    f'- "{c.item.content}"\n'
                    f"  - Generality score: {c.generality_score:.2f}\n"
                )

    lines.append("### Evidence Summary\n")
    lines.append("| Item | Projects | Generality |")
    lines.append("|------|----------|------------|")
    for c in candidates:
        content_short = c.item.content[:50] + ("..." if len(c.item.content) > 50 else "")
        lines.append(f"| {content_short} | {c.cross_project_count} | {c.generality_score:.2f} |")

    lines.append("")
    lines.append("### Methodology\n")
    lines.append(
        "Classification used cross-project prevalence (40%), domain independence (25%), "
        "evidence strength (20%), and category signal (15%).\n"
    )
    lines.append("---")
    lines.append("*Generated by factory meta mode*\n")

    return "\n".join(lines)


def prepare_contribution(
    candidates: list[ClassifiedItem],
    factory_repo_path: Path,
    cross_project_data: dict[str, list[ExperimentRecord]] | None = None,
) -> dict:
    """Generate file change specs for a contribution branch.

    Reads existing default playbooks from factory_repo_path, merges in
    the general items via fuzzy matching, and returns a specification dict.
    Does NOT execute git.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch_name = f"meta-contrib/{today}-playbook-updates"

    evidence_map: dict[str, EvidencePackage] = {}
    if cross_project_data:
        for c in candidates:
            evidence_map[c.item.id] = package_evidence(c, cross_project_data)

    by_role: dict[str, list[ClassifiedItem]] = {}
    for c in candidates:
        by_role.setdefault(c.role, []).append(c)

    file_changes: list[dict] = []
    changed_roles: list[str] = []

    for role, role_items in sorted(by_role.items()):
        default_path = factory_repo_path / "factory" / "agents" / "playbooks" / f"{role}.md"
        if default_path.exists():
            default_pb = Playbook.from_markdown(default_path.read_text())
        else:
            default_pb = Playbook.empty(role)

        for c in role_items:
            matched = False
            for i, existing in enumerate(default_pb.items):
                ratio = difflib.SequenceMatcher(
                    None, c.item.content.lower(), existing.content.lower()
                ).ratio()
                if ratio >= 0.75:
                    default_pb.items[i] = c.item
                    matched = True
                    break
            if not matched:
                default_pb.items.append(c.item)

        file_changes.append({
            "path": f"factory/agents/playbooks/{role}.md",
            "content": default_pb.to_markdown(),
            "change_type": "modify",
        })
        changed_roles.append(role)

    n_items = len(candidates)
    roles_str = ", ".join(changed_roles)

    return {
        "branch_name": branch_name,
        "file_changes": file_changes,
        "commit_message": (
            f"Update playbook items from meta mode analysis\n\n"
            f"Add/update {n_items} generally-useful playbook items "
            f"across {len(changed_roles)} roles ({roles_str}).\n\n"
            f"Items were classified as general via cross-project prevalence analysis."
        ),
        "pr_title": f"Meta mode: update {n_items} playbook items ({roles_str})",
        "pr_body": generate_pr_body(candidates, evidence_map),
    }


# ── Summary ────────────────────────────────────────────────────────


def render_generality_bar(score: float, width: int = 10) -> str:
    """Render a visual bar for generality score. E.g., '████████░░ 0.85'"""
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled) + f" {score:.2f}"


def explain_specificity(item: ClassifiedItem) -> str:
    """Why an item is classified as project-specific."""
    reasons: list[str] = []

    if item.cross_project_count <= 1:
        reasons.append("single-project signal")

    domain_kws: list[str] = []
    for p in DOMAIN_SPECIFIC_PATTERNS:
        m = p.search(item.item.content)
        if m:
            domain_kws.append(m.group(0))
    if domain_kws:
        reasons.append(f"domain-specific ({', '.join(domain_kws)})")

    total = item.item.helpful + item.item.harmful
    if total < 3:
        reasons.append("low evidence")

    if item.item.harmful > 0 and item.item.helpful > 0 and item.cross_project_count > 1:
        reasons.append("high variance across projects")

    if total > 0 and (item.item.helpful / total) < 0.50:
        reasons.append("low consensus")

    if not reasons:
        reasons.append("below generality threshold")

    return ", ".join(reasons[:2])


def explain_uncertainty(item: ClassifiedItem) -> str:
    """What would resolve an uncertain classification."""
    reasons: list[str] = []

    if item.cross_project_count < 3:
        reasons.append(
            f"needs more cross-project evidence (currently {item.cross_project_count}/3 threshold)"
        )

    if item.item.harmful > 0 and item.item.helpful > 0:
        reasons.append("mixed signals: helpful in some projects, harmful in others")

    total = item.item.helpful + item.item.harmful
    if total < 5:
        reasons.append(f"insufficient observations ({total} total, need 5+)")

    if not reasons:
        reasons.append("borderline generality score")

    return reasons[0]


def render_summary(report: ContributionReport) -> str:
    """Generate a terminal-formatted meta mode summary."""
    total = len(report.general_items) + len(report.specific_items) + len(report.uncertain_items)

    roles: set[str] = set()
    for c in report.general_items:
        roles.add(c.role)
    for c in report.specific_items:
        roles.add(c.role)
    for c in report.uncertain_items:
        roles.add(c.role)

    double_line = "═" * 60
    single_line = "─" * 60

    sections: list[str] = [
        double_line,
        "                    META MODE SUMMARY",
        double_line,
        "",
        "PLAYBOOK EVOLUTION COMPLETE",
        f"  {total} items evolved across {len(roles)} roles",
        (
            f"  {len(report.general_items)} general (upstream candidates)"
            f"  |  {len(report.specific_items)} specific (local only)"
            f"  |  {len(report.uncertain_items)} uncertain"
        ),
    ]

    if report.general_items:
        sections.extend(["", single_line, "GENERAL IMPROVEMENTS (upstream candidates)", single_line, ""])
        for idx, c in enumerate(report.general_items, 1):
            bar = render_generality_bar(c.generality_score)
            total_exp = c.item.helpful + c.item.harmful
            category = classify_hypothesis(c.item.content)
            sections.append(f'  {idx}. [{c.role}] "{c.item.content}"')
            sections.append(
                f"     Generality: {bar}  |  {c.cross_project_count} projects"
                f"  |  {total_exp} experiments"
            )
            sections.append(f"     Category: {category}")
            sections.append("")

    if report.specific_items:
        sections.extend([single_line, "PROJECT-SPECIFIC IMPROVEMENTS (staying local)", single_line, ""])
        for idx, c in enumerate(report.specific_items, 1):
            bar = render_generality_bar(c.generality_score)
            total_exp = c.item.helpful + c.item.harmful
            why = explain_specificity(c)
            p_label = "project" if c.cross_project_count == 1 else "projects"
            sections.append(f'  {idx}. [{c.role}] "{c.item.content}"')
            sections.append(
                f"     Generality: {bar}  |  {c.cross_project_count} {p_label}"
                f"  |  {total_exp} experiments"
            )
            sections.append(f"     Why local: {why}")
            sections.append("")

    if report.uncertain_items:
        sections.extend([single_line, "UNCERTAIN (needs more data)", single_line, ""])
        for idx, c in enumerate(report.uncertain_items, 1):
            bar = render_generality_bar(c.generality_score)
            total_exp = c.item.helpful + c.item.harmful
            needs = explain_uncertainty(c)
            sections.append(f'  {idx}. [{c.role}] "{c.item.content}"')
            sections.append(
                f"     Generality: {bar}  |  {c.cross_project_count} projects"
                f"  |  {total_exp} experiments"
            )
            sections.append(f"     Needs: {needs}")
            sections.append("")

    sections.extend([
        double_line,
        "Run `factory contribute` to select items for upstream PR.",
        double_line,
    ])

    return "\n".join(sections)


# ── Submit ─────────────────────────────────────────────────────────


def execute_contribution(contribution_spec: dict, factory_repo_path: Path) -> str:
    """Execute git/gh commands to submit a contribution PR.

    Returns the PR URL on success, or an error message on failure.
    Cleans up the branch on error.
    """
    branch = contribution_spec["branch_name"]

    try:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=factory_repo_path,
            check=True,
            capture_output=True,
            text=True,
        )

        for change in contribution_spec["file_changes"]:
            file_path = factory_repo_path / change["path"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(change["content"])

        paths = [c["path"] for c in contribution_spec["file_changes"]]
        subprocess.run(
            ["git", "add"] + paths,
            cwd=factory_repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", contribution_spec["commit_message"]],
            cwd=factory_repo_path,
            check=True,
            capture_output=True,
            text=True,
        )

        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=factory_repo_path,
            check=True,
            capture_output=True,
            text=True,
        )

        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", contribution_spec["pr_title"],
                "--body", contribution_spec["pr_body"],
            ],
            cwd=factory_repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    except subprocess.CalledProcessError as exc:
        log.error("contribution_failed", error=str(exc), stderr=exc.stderr)
        subprocess.run(
            ["git", "checkout", "-"],
            cwd=factory_repo_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=factory_repo_path,
            capture_output=True,
        )
        return f"Error: {exc.stderr or str(exc)}"


# ── Persistence ────────────────────────────────────────────────────


def save_candidates(report: ContributionReport, project_path: Path) -> None:
    """Persist classification results to .factory/contribution_candidates.json."""
    out = project_path / _CANDIDATES_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2))
    log.info("save_candidates", path=str(out), general=len(report.general_items))


def load_candidates(project_path: Path) -> ContributionReport | None:
    """Load previously saved classification results."""
    path = project_path / _CANDIDATES_FILE
    if not path.exists():
        return None
    try:
        return ContributionReport.model_validate_json(path.read_text())
    except Exception:
        log.warning("load_candidates_failed", path=str(path))
        return None
