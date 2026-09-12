"""Core evolutionary search controller for workflow optimization."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.outer_loop.designer import DesignerAgent
from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.mode_registry import EphemeralModeRegistry
from factory.outer_loop.models import (
    GenerationSummary,
    HyperparameterRecord,
    MutationRecord,
    OuterLoopResult,
    SwarmConfig,
)
from factory.outer_loop.reflector import OuterLoopReflector, ReflectionReport
from factory.outer_loop.mutations import (
    MutationStrategy,
    MutationType,
    WeightedRandomStrategy,
    apply_random_mutation,
)
from factory.outer_loop.candidate_validation import CandidateValidator, ModeContract
from factory.workflow.opt_knobs import mode_parameters
from factory.outer_loop.optimizers import (
    Optimizer,
    Proposal,
    ProposalContext,
    RandomOptimizer,
    TraceSource,
)
from factory.outer_loop.overfit import OverfitDetector
from factory.outer_loop.population import MAPElitesArchive, Population
from factory.outer_loop.similarity import NoveltyFilter
from factory.outer_loop.subset import FixedSubsetSelector, SubsetSelector
from factory.workflow.primitives import Workflow
from factory.workflow.registry import WorkflowRegistry

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

PLATEAU_WINDOW = 3


def _auto_frozen_nodes(workflow: Workflow) -> set[str]:
    """Return node IDs that should always be frozen during mutation."""
    from factory.workflow.primitives import DataNode

    return {nid for nid, node in workflow.nodes.items() if isinstance(node, DataNode)}


def _operator_of(name: str) -> MutationType:
    """Map an optimizer's operator label onto a ``MutationType``.

    A reasoning optimizer reports the label it applied. Unknown labels fall back
    to ``PARAM_MUTATE`` so genealogy stays well-formed rather than raising deep
    inside a generation.
    """
    try:
        return MutationType(name)
    except ValueError:
        return MutationType.PARAM_MUTATE


class BudgetTracker:
    """Tracks evaluation budget consumption, cost, and wall-clock time."""

    def __init__(self, total_budget: int) -> None:
        self._total = total_budget
        self._consumed = 0
        self._cost_usd = 0.0
        self._start_time = time.monotonic()
        self._warned_80 = False
        self._warned_95 = False

    @property
    def remaining(self) -> int:
        return max(0, self._total - self._consumed)

    @property
    def consumed(self) -> int:
        return self._consumed

    @property
    def total_cost_usd(self) -> float:
        return self._cost_usd

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def exhausted(self) -> bool:
        return self._consumed >= self._total

    def consume(self, count: int = 1, cost_usd: float = 0.0) -> None:
        self._consumed += count
        self._cost_usd += cost_usd
        pct = self._consumed / self._total if self._total > 0 else 1.0
        if pct >= 0.95 and not self._warned_95:
            log.warning("budget_95_percent", consumed=self._consumed, total=self._total)
            self._warned_95 = True
        elif pct >= 0.80 and not self._warned_80:
            log.warning("budget_80_percent", consumed=self._consumed, total=self._total)
            self._warned_80 = True


class SwarmEngine:
    """Orchestrates the evolutionary search loop."""

    def __init__(
        self,
        config: SwarmConfig,
        evaluator: SwarmEvaluator,
        strategy: MutationStrategy | None = None,
        subset_selector: SubsetSelector | None = None,
        overfit_detector: OverfitDetector | None = None,
        novelty_filter: NoveltyFilter | None = None,
        designer: DesignerAgent | None = None,
        mode_registry: EphemeralModeRegistry | None = None,
        project_dir: Path | None = None,
        optimizer: "Optimizer | None" = None,
        trace_source: "TraceSource | None" = None,
        frozen_knobs: set[str] | None = None,
    ) -> None:
        self._config = config
        self._evaluator = evaluator
        self._strategy: MutationStrategy = strategy or WeightedRandomStrategy(
            mutation_rate=config.mutation_rate,
        )
        # The optimizer decides how the gradient becomes candidates. Defaulting
        # to RandomOptimizer keeps the pre-existing behaviour for every caller
        # that does not supply one.
        self._optimizer: Optimizer = optimizer or RandomOptimizer(self._strategy)
        self._trace_source: TraceSource | None = trace_source
        # Knobs the user has pinned: the search may not move them, so a proposal
        # that does is refused like any other contract violation.
        self._frozen_knobs: set[str] = set(frozen_knobs or set())
        self._traces: list[dict[str, object]] = []
        self._proposal_rationales: dict[str, Proposal] = {}
        # The contract is captured from the seed the run started with, which is
        # the only definition of what this mode is supposed to keep doing.
        self._validator: CandidateValidator | None = None
        self._rejections: list[dict[str, object]] = []
        self._subset: SubsetSelector = subset_selector or FixedSubsetSelector(
            config.training_instances,
        )
        self._overfit = overfit_detector or OverfitDetector()
        self._novelty = novelty_filter or NoveltyFilter(min_edit_distance=3)
        self._designer = designer or DesignerAgent()
        self._budget = BudgetTracker(config.budget)
        self._archive = MAPElitesArchive()
        self._score_trajectory: list[float] = []
        self._mode_registry = mode_registry
        self._project_dir = project_dir
        self._reflector = OuterLoopReflector(project_dir=project_dir, llm_reflect=True)
        self._last_reflection: ReflectionReport | None = None
        self._initial_diversity: float = 0.0
        self._top_ids_history: list[frozenset[str]] = []

    @property
    def archive(self) -> MAPElitesArchive:
        return self._archive

    @property
    def optimizer(self) -> Optimizer:
        return self._optimizer

    @property
    def proposal_rationales(self) -> dict[str, Proposal]:
        """The proposals that produced this run's candidates, keyed by name."""
        return self._proposal_rationales

    @property
    def rejections(self) -> list[dict[str, object]]:
        """Candidates refused before evaluation, with the reasons."""
        return self._rejections

    def set_seed_contract(self, seed: Workflow) -> None:
        """Pin the mode contract to a known-good workflow.

        Called with the run's original seed. Rebuilding it from the current best
        would let the contract drift as the search moves, which is exactly the
        failure the guard exists to catch.
        """
        self._validator = CandidateValidator(ModeContract.from_workflow(seed))

    def _ensure_validator(self, population: Population) -> None:
        """Build the contract from the population's root once, if not set."""
        if self._validator is not None:
            return
        root = None
        for individual in population.individuals:
            if individual.parent_id is None:
                root = individual
                break
        if root is None and population.individuals:
            root = population.individuals[0]
        if root is not None:
            self.set_seed_contract(Workflow.from_dict(root.workflow_data))  # type: ignore[arg-type]

    def _knob_surface(self) -> list[dict[str, object]]:
        """The declared OptKnob surface from the archive's best candidate.

        Read from the best individual so an optimizer sees what it is allowed to
        move, including each knob's kind and whether its domain is expandable —
        which is what makes a generated value legal for some knobs and not
        others.
        """
        best = self._archive.best()
        if best is None:
            return []
        wf = Workflow.from_dict(best.workflow_data)  # type: ignore[arg-type]
        # The mode derives its own optimizable surface (see
        # factory.workflow.opt_knobs), so a knob only reaches the optimizer if the
        # mode declares it AND something reads it. Graph-level knobs consumed by an
        # op through SRF_KNOBS are carried through the same call.
        surface: list[dict[str, object]] = [
            {
                "name": k.name,
                "kind": k.kind,
                "node_id": k.node_id,
                "default": k.default,
                "bounds": list(k.bounds),
                "value": wf.knob_values.get(k.name, k.default),
                "expandable": k.expandable,
                "expansion_hint": k.expansion_hint,
                "description": k.description,
                "field": k.name.split(".", 1)[1] if "." in k.name else None,
            }
            for k in mode_parameters(wf).knobs
        ]
        return surface

    @property
    def budget(self) -> BudgetTracker:
        return self._budget

    def seed(
        self,
        base_workflow: Workflow,
        config: SwarmConfig | None = None,
    ) -> Population:
        """Create the initial population from a base workflow.

        If config.seed_workflow is set, looks up the workflow from
        WorkflowRegistry and uses it instead of the passed-in base_workflow.
        Falls back to base_workflow when seed_workflow is empty or not found.

        Slot 0: unmodified seed.
        Slots 1..N-designer_count: random mutations of seed.
        Last designer_count slots: from-scratch designs via DesignerAgent.
        """
        cfg = config or self._config
        pop = Population()

        if cfg.seed_workflow:
            registry_wf = WorkflowRegistry.get_workflow(cfg.seed_workflow)
            if registry_wf is not None:
                log.info("seed_workflow_from_registry", name=cfg.seed_workflow)
                base_workflow = registry_wf
            else:
                log.warning("seed_workflow_not_found", name=cfg.seed_workflow)

        seed_ind = Population.make_individual(base_workflow, generation=0)
        pop.add(seed_ind)
        # The seed defines the mode contract, and every variant built from it is
        # held to that contract immediately: an invalid individual admitted here
        # becomes a parent later, so a broken mode would be inherited by every
        # candidate descended from it.
        self.set_seed_contract(base_workflow)
        self._novelty.add(base_workflow)
        if self._mode_registry:
            self._mode_registry.register(seed_ind.id, 0, base_workflow)

        designer_count = cfg.designer_count
        mutation_slots = max(0, cfg.population_size - 1 - designer_count)

        attempts = 0
        max_attempts = mutation_slots * 10
        while pop.size < 1 + mutation_slots and attempts < max_attempts:
            attempts += 1
            result = apply_random_mutation(
                base_workflow,
                self._strategy,
                generation=0,
                frozen_nodes=set(cfg.frozen_node_ids) | _auto_frozen_nodes(base_workflow),
            )
            if result is None:
                continue
            mutated_wf, mutation_rec = result
            if not self._novelty.is_novel(mutated_wf):
                continue
            if self._validator is not None:
                reasons = self._validator.issues(mutated_wf)
                if reasons:
                    self._rejections.append(
                        {"candidate": mutated_wf.name, "optimizer": "seed", "reasons": reasons}
                    )
                    continue
            self._novelty.add(mutated_wf)
            ind = Population.make_individual(
                mutated_wf,
                generation=0,
                parent_id=seed_ind.id,
                mutation_record=mutation_rec,
            )
            pop.add(ind)
            if self._mode_registry:
                self._mode_registry.register(ind.id, 0, mutated_wf)

        if designer_count > 0:
            self._add_designer_variants(pop, cfg, designer_count)

        log.info(
            "population_seeded",
            size=pop.size,
            target=cfg.population_size,
            designer_variants=min(designer_count, pop.size),
        )
        return pop

    def _add_designer_variants(
        self,
        pop: Population,
        cfg: SwarmConfig,
        designer_count: int,
    ) -> None:
        """Add from-scratch designed workflows to the population."""
        benchmark_spec = cfg.benchmark
        designs: list[Workflow] = []

        if designer_count >= 1:
            try:
                minimal = self._designer.design_minimal(benchmark_spec)
                designs.append(minimal)
            except Exception:
                log.warning("designer_minimal_failed", exc_info=True)

        if designer_count >= 2:
            try:
                thorough = self._designer.design_thorough(benchmark_spec)
                designs.append(thorough)
            except Exception:
                log.warning("designer_thorough_failed", exc_info=True)

        for i in range(2, designer_count):
            try:
                custom = self._designer.design_custom(
                    benchmark_spec,
                    {"max_nodes": 4 + i, "parallel": i % 2 == 0},
                )
                designs.append(custom)
            except Exception:
                log.warning("designer_custom_failed", index=i, exc_info=True)

        for wf in designs:
            if pop.size >= cfg.population_size:
                break
            if self._novelty.is_novel(wf):
                self._novelty.add(wf)
                ind = Population.make_individual(wf, generation=0)
                pop.add(ind)
                if self._mode_registry:
                    self._mode_registry.register(ind.id, 0, wf)

    # ── Decomposable generation steps ──────────────────────────────────────
    # evolve_generation runs these in order. They are public so the outer-loop
    # graph can drive each step as its own node, which is what makes the loop
    # legible and lets a model-owned `propose` node sit between reflect and apply.

    def evaluate_population(
        self,
        population: Population,
        project_dir: str,
        instances: list[str],
        skip_ids: set[str] | None = None,
    ) -> int:
        """Evaluate every individual in the population and archive the results.

        ``skip_ids`` names individuals already evaluated by an earlier step, so a
        decomposed loop can evaluate only what is new instead of paying for the
        whole population every cycle. Returns how many were evaluated; stops
        early when the budget is spent.
        """
        evaluated = 0
        for ind in list(population.individuals):
            if self._budget.exhausted:
                break
            if skip_ids and ind.id in skip_ids:
                continue
            wf = Workflow.from_dict(ind.workflow_data)  # type: ignore[arg-type]
            ev = self._evaluator.evaluate(wf, project_dir, instances, individual_id=ind.id)
            self._budget.consume(1, cost_usd=ev.cost_usd)
            updated = ind.model_copy(update={"score": ev.score, "cost_usd": ev.cost_usd})
            population.remove(ind.id)
            population.add(updated)
            self._archive.add(updated)
            evaluated += 1
        return evaluated

    def reflect_generation(
        self, population: Population, generation: int
    ) -> ReflectionReport | None:
        """Contrastive reflection over this generation's results."""
        if not (generation > 0 or len(population.individuals) >= 2):
            return self._last_reflection
        records = []
        kvbi: dict[str, dict[str, object]] = {}
        for ind in population.individuals:
            cycle_rec = self._evaluator.get_cycle_record(ind.id)
            records.append((ind.id, ind.score, cycle_rec))
            ind_wf = Workflow.from_dict(ind.workflow_data)  # type: ignore[arg-type]
            if ind_wf.knob_values:
                kvbi[ind.id] = dict(ind_wf.knob_values)
        self._last_reflection = self._reflector.reflect(
            records, generation, knob_values_by_id=kvbi,
        )
        return self._last_reflection

    def evaluate_offspring(
        self,
        offspring: list[tuple[Workflow, MutationRecord, str]],
        population: Population,
        generation: int,
        instances: list[str],
        project_dir: str,
    ) -> int:
        """Turn validated offspring into individuals, evaluate and archive them."""
        added = 0
        for child_wf, mutation_rec, parent_id in offspring:
            if self._budget.exhausted:
                break
            ind = Population.make_individual(
                child_wf,
                generation=generation,
                parent_id=parent_id,
                mutation_record=mutation_rec,
            )
            if self._mode_registry:
                self._mode_registry.register(ind.id, generation, child_wf)
            eval_result = self._evaluator.evaluate(
                child_wf, project_dir, instances, individual_id=ind.id
            )
            self._budget.consume(1, cost_usd=eval_result.cost_usd)
            updated = ind.model_copy(
                update={"score": eval_result.score, "cost_usd": eval_result.cost_usd}
            )
            population.add(updated)
            self._archive.add(updated)
            added += 1
        return added

    def validate_and_select(
        self,
        proposals: list[Proposal],
        population: Population,
        generation: int,
    ) -> tuple[list[tuple[Workflow, MutationRecord, str]], int, int, int, int]:
        """Filter proposals into offspring: novelty, then the mode contract.

        Returns ``(offspring, novel, duplicates, invalid, applied)``. Shared by
        the monolithic generation and by a graph-driven `apply` step so both
        enforce the same rules.
        """
        offspring: list[tuple[Workflow, MutationRecord, str]] = []
        novel = duplicates = invalid = 0
        self._ensure_validator(population)
        for proposal in proposals:
            child_wf = proposal.workflow
            knob = (proposal.before or {}).get("knob")
            if knob and str(knob) in self._frozen_knobs:
                self._rejections.append(
                    {"candidate": child_wf.name, "optimizer": proposal.optimizer,
                     "reasons": [f"knob '{knob}' is frozen by steering"]}
                )
                invalid += 1
                continue
            if not self._novelty.is_novel(child_wf):
                duplicates += 1
                continue
            reasons = self._validator.issues(child_wf) if self._validator else []
            if reasons:
                self._rejections.append(
                    {"candidate": child_wf.name, "optimizer": proposal.optimizer,
                     "reasons": reasons}
                )
                invalid += 1
                continue
            self._novelty.add(child_wf)
            record = MutationRecord(
                operator=_operator_of(proposal.operator),
                target_node=proposal.target_node,
                before=dict(proposal.before or {}),
                after=dict(proposal.after or {}),
                rationale=proposal.rationale or None,
            )
            self._proposal_rationales[child_wf.name] = proposal
            offspring.append((child_wf, record, proposal.parent_id or ""))
            novel += 1
        return offspring, novel, duplicates, invalid, len(proposals)

    def build_proposal_context(
        self, generation: int, population: Population
    ) -> ProposalContext:
        """Assemble the gradient an optimizer conditions its proposal on."""

        def sample_parent() -> tuple[str, Workflow] | None:
            parent = self._archive.sample_parent(
                self._config.tournament_size,
                rank_weighted=self._config.rank_weighted_selection,
            )
            if parent is None:
                return None
            return parent.id, Workflow.from_dict(parent.workflow_data)  # type: ignore[arg-type]

        best = self._archive.best()
        return ProposalContext(
            generation=generation,
            sample_parent=sample_parent,
            frozen_nodes=set(self._config.frozen_node_ids),
            reflection=self._last_reflection,
            archive_stats={"size": self._archive.size, "best_score": best.score if best else None},
            traces=(
                self._trace_source.recent(32)
                if self._trace_source is not None
                else list(self._traces)
            ),
            knob_surface=self._knob_surface(),
        )

    def evolve_generation(
        self,
        population: Population,
        generation: int,
        project_dir: str = "",
    ) -> GenerationSummary:
        """Run one generation of evolution."""
        instances = self._subset.select(
            self._config.training_instances, generation, self._budget.remaining
        )

        self.evaluate_population(population, project_dir, instances)

        self.reflect_generation(population, generation)

        # Propose, filter and select offspring. All the rules live in
        # validate_and_select so a graph-driven `apply` step enforces exactly
        # what the monolithic generation does.
        mutation_rate = self._strategy.get_mutation_rate(generation)
        proposals = self._optimizer.propose(
            self.build_proposal_context(generation, population),
            self._config.population_size,
        )
        offspring, novel_count, rejected_dupes, rejected_invalid, _applied = (
            self.validate_and_select(proposals, population, generation)
        )
        mutations_applied: list[MutationRecord] = [rec for _wf, rec, _pid in offspring]

        offspring_evaluated = self.evaluate_offspring(
            offspring, population, generation, instances, project_dir
        )
        if novel_count and not offspring_evaluated:
            # The budget ran out before a single generated candidate could be
            # tried. Without this the run still reports a new generation and
            # looks productive while having tested nothing it proposed.
            log.warning(
                "budget_exhausted_before_offspring",
                generation=generation,
                proposed=novel_count,
                evaluated=offspring_evaluated,
                budget_total=self._budget._total,
                population_size=self._config.population_size,
                action="raise the budget above the population size",
            )

        # Cleanup non-surviving ephemeral mode files
        if self._mode_registry:
            survivor_names = set()
            for ind in population.individuals:
                for g in range(generation + 1):
                    survivor_names.add(f"evolve-gen{g}-{ind.id[:8]}")
            self._mode_registry.cleanup_generation(survivor_names)

        # Track best score and diversity
        best = population.best()
        best_score = best.score if best else 0.0
        mean_score = population.mean_score()
        diversity = self._archive.diversity_metric()
        self._score_trajectory.append(best_score)

        if generation == 0:
            self._initial_diversity = diversity if diversity > 0 else 1.0

        top_3 = sorted(population.individuals, key=lambda i: i.score, reverse=True)[:3]
        self._top_ids_history.append(frozenset(i.id for i in top_3))

        self._log_event(generation, best_score, mean_score, diversity, self._archive.size)
        self._log_costs(generation, population)

        hp_record = HyperparameterRecord(
            generation=generation,
            mutation_rate=mutation_rate,
            population_size=population.size,
            tournament_size=self._config.tournament_size,
            designer_ratio=self._strategy.get_designer_ratio(generation),
            operator_weights=(
                self._strategy.get_operator_weights()
                if hasattr(self._strategy, "get_operator_weights")
                else {}
            ),
            best_score=best_score,
            mean_score=mean_score,
            diversity=diversity,
            novel_count=novel_count,
        )

        # Holdout evaluation for best candidate
        holdout_score = 0.0
        if best and self._config.holdout_instances:
            best_wf = Workflow.from_dict(best.workflow_data)  # type: ignore[arg-type]
            holdout_result = self._evaluator.evaluate(best_wf, project_dir, self._config.holdout_instances)
            holdout_score = holdout_result.score
            self._budget.consume(1, cost_usd=holdout_result.cost_usd)
            log.info(
                "holdout_eval",
                generation=generation,
                holdout_score=holdout_score,
                training_best=best_score,
            )

        return GenerationSummary(
            generation=generation,
            population_size=population.size,
            best_score=best_score,
            mean_score=mean_score,
            diversity=diversity,
            mutations_applied=mutations_applied,
            novel_count=novel_count,
            rejected_duplicates=rejected_dupes,
            rejected_invalid=rejected_invalid,
            offspring_evaluated=offspring_evaluated,
            holdout_score=holdout_score,
            hyperparameters=hp_record,
        )

    def _detect_plateau(self) -> bool:
        """Detect plateau: N consecutive generations with improvement < threshold."""
        window = self._config.plateau_window
        threshold = self._config.plateau_threshold
        if len(self._score_trajectory) < window + 1:
            return False
        recent = self._score_trajectory[-(window + 1):]
        baseline = recent[0]
        return all(abs(s - baseline) < threshold for s in recent[1:])

    def _detect_diversity_collapse(self) -> bool:
        """Detect diversity collapse: archive diversity below floor."""
        if not self._initial_diversity:
            return False
        current = self._archive.diversity_metric()
        return current < self._config.diversity_floor * self._initial_diversity

    def _detect_early_stop(self) -> bool:
        """Detect early stop: top 3 individuals unchanged for N generations."""
        n = self._config.early_stop_unchanged
        if len(self._top_ids_history) < n:
            return False
        recent = self._top_ids_history[-n:]
        return all(s == recent[0] for s in recent[1:])

    def _log_event(
        self, generation: int, best_score: float, mean_score: float,
        diversity: float, archive_size: int,
    ) -> None:
        if not self._project_dir:
            return
        import json
        events_path = self._project_dir / ".factory" / "outer_loop" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "generation": generation,
            "best_score": best_score,
            "mean_score": mean_score,
            "diversity": diversity,
            "archive_size": archive_size,
        }
        with events_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def _log_costs(self, generation: int, population: Population) -> None:
        if not self._project_dir:
            return
        import json
        costs_path = self._project_dir / ".factory" / "outer_loop" / "costs.jsonl"
        costs_path.parent.mkdir(parents=True, exist_ok=True)
        for ind in population.individuals:
            entry = {
                "generation": generation,
                "individual_id": ind.id,
                "score": ind.score,
                "cost_usd": ind.cost_usd,
            }
            with costs_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")

    def run(
        self,
        base_workflow: Workflow,
        project_dir: str = "",
    ) -> OuterLoopResult:
        """Run the full evolutionary search loop."""
        population = self.seed(base_workflow)
        generation = 0
        summaries: list[GenerationSummary] = []
        hp_history: list[HyperparameterRecord] = []

        while not self._should_terminate(generation):
            log.info("generation_start", generation=generation, budget_remaining=self._budget.remaining)
            summary = self.evolve_generation(population, generation, project_dir)
            summaries.append(summary)
            if summary.hyperparameters:
                hp_history.append(summary.hyperparameters)

            # Plateau detection with adaptive response
            if self._detect_plateau():
                if hasattr(self._strategy, "on_plateau"):
                    self._strategy.on_plateau()  # type: ignore[union-attr]
                    log.info("plateau_detected_adapting", generation=generation)
            elif len(self._score_trajectory) >= 2 and self._score_trajectory[-1] > self._score_trajectory[-2]:
                if hasattr(self._strategy, "on_improvement"):
                    self._strategy.on_improvement()  # type: ignore[union-attr]

            generation += 1

        convergence_reason = self._get_convergence_reason(generation)
        log.info("evolution_complete", reason=convergence_reason, generations=generation)

        # Post-evolution overfit audit
        best = self._archive.best()
        audit_result = None
        if best and self._config.holdout_instances:
            best_wf = Workflow.from_dict(best.workflow_data)  # type: ignore[arg-type]
            audit_result = self._overfit.audit(
                best_wf,
                self._config.training_instances,
                self._config.holdout_instances,
                self._evaluator,
                project_dir,
            )

        pareto = self._archive.pareto_front()

        return OuterLoopResult(
            best_workflow_data=best.workflow_data if best else {},
            best_score=best.score if best else 0.0,
            holdout_score=audit_result.holdout_score if audit_result else 0.0,
            overfit_flag=audit_result.overfit_flag if audit_result else False,
            trajectory=summaries,
            total_cost_usd=self._budget.total_cost_usd,
            convergence_reason=convergence_reason,
            generations_completed=generation,
            total_evaluations=self._budget.consumed,
            archive_size=self._archive.size,
            pareto_front=pareto,
            hyperparameter_history=hp_history,
        )

    def _should_terminate(self, generation: int) -> bool:
        if self._budget.exhausted:
            return True
        if self._config.target_score is not None and self._score_trajectory:
            if self._score_trajectory[-1] >= self._config.target_score:
                return True
        if self._detect_plateau():
            window = self._config.plateau_window
            if len(self._score_trajectory) >= window + 2:
                recent = self._score_trajectory[-(window + 2):]
                threshold = self._config.plateau_threshold
                if all(abs(s - recent[0]) < threshold for s in recent[1:]):
                    return True
        if self._detect_diversity_collapse():
            return True
        if self._detect_early_stop():
            return True
        return False

    def _get_convergence_reason(self, generation: int) -> str:
        if self._budget.exhausted:
            return "budget_exhausted"
        if self._config.target_score is not None and self._score_trajectory:
            if self._score_trajectory[-1] >= self._config.target_score:
                return "target_score_reached"
        if self._detect_plateau():
            return "plateau"
        if self._detect_diversity_collapse():
            return "diversity_collapse"
        if self._detect_early_stop():
            return "early_stop_unchanged"
        return "unknown"
