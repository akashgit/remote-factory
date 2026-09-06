"""Contrastive reflection engine for outer loop evolution.

Analyzes CycleRecord exhaust from winners vs losers to identify structural
differences that explain performance gaps. Produces a ReflectionReport with
failure patterns, success patterns, and informed mutation suggestions.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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
        knob_values_by_id: dict[str, dict[str, object]] | None = None,
    ) -> ReflectionReport:
        """Analyze a generation's results via contrastive reflection.

        Args:
            records: list of (individual_id, fitness, CycleRecord|None) triples
            generation: current generation number
            knob_values_by_id: optional mapping of individual_id to knob_values
                dict. When provided, enables knob-contrastive analysis that
                identifies which knob settings correlate with high/low scores.

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
        self._extract_eval_patterns(top_k, bottom_k, report)
        self._generate_mutation_suggestions(top_k, bottom_k, report)
        self._generate_structural_recommendations(top_k, bottom_k, report)
        if knob_values_by_id:
            self._extract_knob_patterns(valid, top_k, bottom_k, knob_values_by_id, report)

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
        bottom_k: Sequence[tuple[str, float, CycleRecord | None]],
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
        top_k: Sequence[tuple[str, float, CycleRecord | None]],
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

    def _extract_eval_patterns(
        self,
        top_k: Sequence[tuple[str, float, CycleRecord | None]],
        bottom_k: Sequence[tuple[str, float, CycleRecord | None]],
        report: ReflectionReport,
    ) -> None:
        """Extract patterns from EvalResult.details stored on CycleRecords."""
        top_details = [
            (id_, score, rec.eval_details)
            for id_, score, rec in top_k
            if rec is not None and isinstance(rec.eval_details, dict)
        ]
        bottom_details = [
            (id_, score, rec.eval_details)
            for id_, score, rec in bottom_k
            if rec is not None and isinstance(rec.eval_details, dict)
        ]

        if not top_details and not bottom_details:
            return

        # --- Verify patterns ---
        for id_, score, details in bottom_details:
            verify = details.get("verify")
            if not isinstance(verify, dict):
                continue
            failed = verify.get("failed_count", 0)
            total = verify.get("verify_count", 0)
            if isinstance(failed, (int, float)) and isinstance(total, (int, float)) and failed > 0:
                report.failure_patterns.append(
                    f"Individual {id_[:8]} (score={score:.3f}) failed "
                    f"{int(failed)}/{int(total)} verify checks"
                )
                instances = verify.get("instance_results")
                if isinstance(instances, list):
                    for inst in instances:
                        if isinstance(inst, dict) and not inst.get("passed", True):
                            inst_details = inst.get("details")
                            if isinstance(inst_details, dict):
                                rc = inst_details.get("returncode")
                                if rc is not None and rc != 0:
                                    report.failure_patterns.append(
                                        f"Individual {id_[:8]} verify instance "
                                        f"{inst.get('index', '?')} failed with "
                                        f"returncode={rc}"
                                    )

        for id_, score, details in top_details:
            verify = details.get("verify")
            if not isinstance(verify, dict):
                continue
            passed = verify.get("passed_count", 0)
            total = verify.get("verify_count", 0)
            if isinstance(passed, (int, float)) and isinstance(total, (int, float)) and passed > 0:
                report.success_patterns.append(
                    f"Individual {id_[:8]} (score={score:.3f}) passed "
                    f"{int(passed)}/{int(total)} verify checks"
                )

        # --- Verify score comparison between top-K and bottom-K ---
        top_verify_scores: list[float] = []
        bottom_verify_scores: list[float] = []
        for _, _, details in top_details:
            verify = details.get("verify")
            if isinstance(verify, dict):
                instances = verify.get("instance_results")
                if isinstance(instances, list):
                    for inst in instances:
                        if isinstance(inst, dict) and isinstance(inst.get("score"), (int, float)):
                            top_verify_scores.append(float(inst["score"]))
        for _, _, details in bottom_details:
            verify = details.get("verify")
            if isinstance(verify, dict):
                instances = verify.get("instance_results")
                if isinstance(instances, list):
                    for inst in instances:
                        if isinstance(inst, dict) and isinstance(inst.get("score"), (int, float)):
                            bottom_verify_scores.append(float(inst["score"]))

        if top_verify_scores and bottom_verify_scores:
            top_avg = sum(top_verify_scores) / len(top_verify_scores)
            bottom_avg = sum(bottom_verify_scores) / len(bottom_verify_scores)
            if abs(top_avg - bottom_avg) > 0.05:
                report.mutation_suggestions.append(
                    f"Bottom-K scored {bottom_avg:.2f} avg on verify while "
                    f"top-K scored {top_avg:.2f} — focus mutations on "
                    f"improving test/verify pass rate"
                )

        # --- Test details patterns ---
        for id_, score, details in bottom_details:
            test_details = details.get("test_details")
            if not isinstance(test_details, dict):
                continue
            rc = test_details.get("returncode")
            if rc is not None and rc != 0:
                report.failure_patterns.append(
                    f"Individual {id_[:8]} (score={score:.3f}) tests failed "
                    f"with returncode={rc}"
                )
            failed_tests = test_details.get("failed")
            if isinstance(failed_tests, (int, float)) and failed_tests > 0:
                total_tests = test_details.get("total", "?")
                report.failure_patterns.append(
                    f"Individual {id_[:8]} had {int(failed_tests)}/{total_tests} "
                    f"test failures"
                )

        # --- Rejection/error patterns ---
        for id_, score, details in bottom_details:
            rejected = details.get("rejected")
            if isinstance(rejected, str):
                report.failure_patterns.append(
                    f"Individual {id_[:8]} was rejected: {rejected}"
                )
            error = details.get("error")
            if isinstance(error, str):
                report.failure_patterns.append(
                    f"Individual {id_[:8]} evaluation error: {error[:120]}"
                )

    def _generate_mutation_suggestions(
        self,
        top_k: Sequence[tuple[str, float, CycleRecord | None]],
        bottom_k: Sequence[tuple[str, float, CycleRecord | None]],
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
        top_k: Sequence[tuple[str, float, CycleRecord | None]],
        bottom_k: Sequence[tuple[str, float, CycleRecord | None]],
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
                        "PARALLELIZE: Winners use parallel execution — "
                        "consider parallelizing independent agents"
                    )
                    break

    def _extract_knob_patterns(
        self,
        all_sorted: Sequence[tuple[str, float, CycleRecord | None]],
        top_k: Sequence[tuple[str, float, CycleRecord | None]],
        bottom_k: Sequence[tuple[str, float, CycleRecord | None]],
        knob_values_by_id: dict[str, dict[str, object]],
        report: ReflectionReport,
    ) -> None:
        """Contrastive analysis of knob values between winners and losers.

        For each knob, computes the average score per value across all
        individuals. Reports knobs where the best value significantly
        outperforms the worst, giving the optimizer a per-knob gradient.
        """
        all_knob_names: set[str] = set()
        for kv in knob_values_by_id.values():
            all_knob_names.update(kv.keys())

        for knob in sorted(all_knob_names):
            is_prompt = knob.startswith("_prompt_") or knob.startswith("prompt_")
            val_scores: dict[str, list[float]] = {}
            for id_, score, _ in all_sorted:
                kv = knob_values_by_id.get(id_, {})
                val = str(kv.get(knob, ""))
                if not val:
                    continue
                if is_prompt:
                    val = val[:100]
                val_scores.setdefault(val, []).append(score)

            if len(val_scores) < 2:
                continue

            avg_by_val = {v: sum(s) / len(s) for v, s in val_scores.items() if s}
            if not avg_by_val:
                continue

            best_val = max(avg_by_val, key=avg_by_val.get)  # type: ignore[arg-type]
            worst_val = min(avg_by_val, key=avg_by_val.get)  # type: ignore[arg-type]
            gap = avg_by_val[best_val] - avg_by_val[worst_val]

            if gap > 0:
                op = "PROMPT_MUTATE" if is_prompt else "KNOB_MUTATE"
                display_best = best_val[:60] + "..." if len(best_val) > 60 else best_val
                display_worst = worst_val[:60] + "..." if len(worst_val) > 60 else worst_val
                report.mutation_suggestions.append(
                    f"{op}: {knob}={display_best} "
                    f"(avg score {avg_by_val[best_val]:+.0f}) "
                    f"outperforms {knob}={display_worst} "
                    f"({avg_by_val[worst_val]:+.0f}) by {gap:.0f}"
                )

        # Top-K vs bottom-K: which knobs differ consistently?
        top_ids = {id_ for id_, _, _ in top_k}
        bottom_ids = {id_ for id_, _, _ in bottom_k}
        for knob in sorted(all_knob_names):
            top_vals = {str(knob_values_by_id.get(id_, {}).get(knob, "")) for id_ in top_ids}
            bottom_vals = {str(knob_values_by_id.get(id_, {}).get(knob, "")) for id_ in bottom_ids}
            top_vals.discard("")
            bottom_vals.discard("")
            if top_vals and bottom_vals and not top_vals & bottom_vals:
                report.success_patterns.append(
                    f"Top performers use {knob}={','.join(top_vals)}; "
                    f"bottom use {knob}={','.join(bottom_vals)}"
                )

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
