"""Contrastive reflection engine for outer loop evolution.

Analyzes CycleRecord exhaust from winners vs losers to identify structural
differences that explain performance gaps. Produces a ReflectionReport with
failure patterns, success patterns, and informed mutation suggestions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.cycle_analyzer import CycleRecord

log = structlog.get_logger()


@dataclass
class ReflectionReport:
    """Output of contrastive reflection analysis."""

    failure_patterns: list[str] = field(default_factory=list)
    success_patterns: list[str] = field(default_factory=list)
    mutation_suggestions: list[str] = field(default_factory=list)
    prompt_improvements: list[str] = field(default_factory=list)
    structural_recommendations: list[str] = field(default_factory=list)
    top_k_ids: list[str] = field(default_factory=list)
    bottom_k_ids: list[str] = field(default_factory=list)


class OuterLoopReflector:
    """Two-stage contrastive reflection on CycleRecord exhaust.

    Stage 1: Partition individuals into top-K and bottom-K by fitness.
    Stage 2: Compare their CycleRecords to identify causal structural differences.
    """

    def __init__(self, k: int = 2, project_dir: Path | None = None) -> None:
        self._k = k
        self._project_dir = project_dir

    def reflect(
        self,
        records: list[tuple[str, float, CycleRecord | None]],
        generation: int = 0,
    ) -> ReflectionReport:
        """Analyze a generation's results via contrastive reflection.

        Args:
            records: list of (individual_id, fitness, CycleRecord|None) triples
            generation: current generation number

        Returns:
            ReflectionReport with patterns and suggestions
        """
        valid = [(id_, score, rec) for id_, score, rec in records if rec is not None]
        if len(valid) < 2:
            log.warning("reflection_insufficient_data", count=len(valid))
            return ReflectionReport()

        valid.sort(key=lambda x: x[1], reverse=True)

        k = min(self._k, len(valid) // 2)
        if k < 1:
            k = 1

        top_k = valid[:k]
        bottom_k = valid[-k:]

        report = ReflectionReport(
            top_k_ids=[id_ for id_, _, _ in top_k],
            bottom_k_ids=[id_ for id_, _, _ in bottom_k],
        )

        self._extract_failure_patterns(bottom_k, report)
        self._extract_success_patterns(top_k, report)
        self._generate_mutation_suggestions(top_k, bottom_k, report)
        self._generate_structural_recommendations(top_k, bottom_k, report)

        if self._project_dir:
            self._save_report(report, generation)

        log.info(
            "reflection_complete",
            generation=generation,
            failures=len(report.failure_patterns),
            successes=len(report.success_patterns),
            suggestions=len(report.mutation_suggestions),
        )
        return report

    def _extract_failure_patterns(
        self,
        bottom_k: list[tuple[str, float, CycleRecord | None]],
        report: ReflectionReport,
    ) -> None:
        for id_, score, rec in bottom_k:
            if rec is None:
                continue
            for step in rec.steps:
                if not step.succeeded:
                    report.failure_patterns.append(
                        f"Agent {step.role} failed in individual {id_[:8]} "
                        f"(score={score:.3f}): {step.error or 'unknown error'}"
                    )
            if rec.errored and rec.errored > 0:
                report.failure_patterns.append(
                    f"Individual {id_[:8]} had {rec.errored} errored experiments"
                )
            if rec.reverted > rec.kept:
                report.failure_patterns.append(
                    f"Individual {id_[:8]} had more reverts ({rec.reverted}) than keeps ({rec.kept})"
                )

    def _extract_success_patterns(
        self,
        top_k: list[tuple[str, float, CycleRecord | None]],
        report: ReflectionReport,
    ) -> None:
        for id_, score, rec in top_k:
            if rec is None:
                continue
            successful_roles = [s.role for s in rec.steps if s.succeeded]
            if successful_roles:
                report.success_patterns.append(
                    f"Individual {id_[:8]} (score={score:.3f}) succeeded with "
                    f"agents: {', '.join(successful_roles)}"
                )
            if rec.kept > 0:
                report.success_patterns.append(
                    f"Individual {id_[:8]} kept {rec.kept} experiments"
                )

    def _generate_mutation_suggestions(
        self,
        top_k: list[tuple[str, float, CycleRecord | None]],
        bottom_k: list[tuple[str, float, CycleRecord | None]],
        report: ReflectionReport,
    ) -> None:
        top_roles: set[str] = set()
        bottom_roles: set[str] = set()

        for _, _, rec in top_k:
            if rec:
                top_roles |= {s.role for s in rec.steps if s.succeeded}
        for _, _, rec in bottom_k:
            if rec:
                bottom_roles |= {s.role for s in rec.steps if s.succeeded}

        roles_in_top_not_bottom = top_roles - bottom_roles
        for role in roles_in_top_not_bottom:
            report.mutation_suggestions.append(
                f"NODE_INSERT: Add {role} agent — present in winners but not losers"
            )

        roles_in_bottom_not_top = bottom_roles - top_roles
        for role in roles_in_bottom_not_top:
            report.mutation_suggestions.append(
                f"NODE_REMOVE: Consider removing {role} — present in losers but not winners"
            )

        top_avg_steps = 0.0
        bottom_avg_steps = 0.0
        top_count = sum(1 for _, _, r in top_k if r)
        bottom_count = sum(1 for _, _, r in bottom_k if r)

        if top_count:
            top_avg_steps = sum(len(r.steps) for _, _, r in top_k if r) / top_count
        if bottom_count:
            bottom_avg_steps = sum(len(r.steps) for _, _, r in bottom_k if r) / bottom_count

        if top_avg_steps > bottom_avg_steps + 1:
            report.mutation_suggestions.append(
                f"NODE_INSERT: Winners use more agents ({top_avg_steps:.1f} avg) "
                f"vs losers ({bottom_avg_steps:.1f} avg) — consider adding nodes"
            )
        elif bottom_avg_steps > top_avg_steps + 1:
            report.mutation_suggestions.append(
                f"NODE_REMOVE: Losers use more agents ({bottom_avg_steps:.1f} avg) "
                f"vs winners ({top_avg_steps:.1f} avg) — consider removing nodes"
            )

    def _generate_structural_recommendations(
        self,
        top_k: list[tuple[str, float, CycleRecord | None]],
        bottom_k: list[tuple[str, float, CycleRecord | None]],
        report: ReflectionReport,
    ) -> None:
        for _, score, rec in bottom_k:
            if rec is None:
                continue
            timeout_failures = [s for s in rec.steps if not s.succeeded and s.duration_s > 500]
            if timeout_failures:
                report.structural_recommendations.append(
                    f"PARAM_MUTATE: Increase timeout for agents that timed out "
                    f"({', '.join(s.role for s in timeout_failures)})"
                )

        for _, score, rec in top_k:
            if rec is None:
                continue
            if rec.node_trace:
                parallel_nodes = [
                    nid for nid, nt in rec.node_trace.items()
                    if nt.node_type == "ForkNode"
                ]
                if parallel_nodes:
                    report.structural_recommendations.append(
                        f"PARALLELIZE: Winners use parallel execution — "
                        f"consider parallelizing independent agents"
                    )
                    break

    def _save_report(self, report: ReflectionReport, generation: int) -> None:
        if not self._project_dir:
            return
        reflect_dir = self._project_dir / ".factory" / "outer_loop" / "reflections"
        reflect_dir.mkdir(parents=True, exist_ok=True)

        report_data = {
            "generation": generation,
            "failure_patterns": report.failure_patterns,
            "success_patterns": report.success_patterns,
            "mutation_suggestions": report.mutation_suggestions,
            "prompt_improvements": report.prompt_improvements,
            "structural_recommendations": report.structural_recommendations,
            "top_k_ids": report.top_k_ids,
            "bottom_k_ids": report.bottom_k_ids,
        }
        path = reflect_dir / f"gen{generation}.json"
        path.write_text(json.dumps(report_data, indent=2))

        md_path = reflect_dir / f"gen{generation}.md"
        lines = [f"# Reflection — Generation {generation}\n"]
        if report.failure_patterns:
            lines.append("## Failure Patterns")
            for p in report.failure_patterns:
                lines.append(f"- {p}")
            lines.append("")
        if report.success_patterns:
            lines.append("## Success Patterns")
            for p in report.success_patterns:
                lines.append(f"- {p}")
            lines.append("")
        if report.mutation_suggestions:
            lines.append("## Mutation Suggestions")
            for s in report.mutation_suggestions:
                lines.append(f"- {s}")
            lines.append("")
        if report.structural_recommendations:
            lines.append("## Structural Recommendations")
            for r in report.structural_recommendations:
                lines.append(f"- {r}")
        md_path.write_text("\n".join(lines) + "\n")
