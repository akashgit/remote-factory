"""Core evolutionary search controller for workflow optimization."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.models import (
    GenerationSummary,
    HyperparameterRecord,
    MutationRecord,
    OuterLoopResult,
    SwarmConfig,
)
from factory.outer_loop.mutations import (
    MutationStrategy,
    WeightedRandomStrategy,
    apply_random_mutation,
)
from factory.outer_loop.overfit import OverfitDetector
from factory.outer_loop.population import MAPElitesArchive, Population
from factory.outer_loop.similarity import NoveltyFilter
from factory.outer_loop.subset import FixedSubsetSelector, SubsetSelector
from factory.workflow.primitives import Workflow

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

PLATEAU_WINDOW = 3


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
    ) -> None:
        self._config = config
        self._evaluator = evaluator
        self._strategy: MutationStrategy = strategy or WeightedRandomStrategy(
            mutation_rate=config.mutation_rate,
        )
        self._subset: SubsetSelector = subset_selector or FixedSubsetSelector(
            config.training_instances,
        )
        self._overfit = overfit_detector or OverfitDetector()
        self._novelty = novelty_filter or NoveltyFilter(min_edit_distance=3)
        self._budget = BudgetTracker(config.budget)
        self._archive = MAPElitesArchive()
        self._score_trajectory: list[float] = []

    @property
    def archive(self) -> MAPElitesArchive:
        return self._archive

    @property
    def budget(self) -> BudgetTracker:
        return self._budget

    def seed(
        self,
        base_workflow: Workflow,
        config: SwarmConfig | None = None,
    ) -> Population:
        """Create the initial population from a base workflow.

        Slot 0: unmodified seed.
        Slots 1..N: random mutations of seed.
        """
        cfg = config or self._config
        pop = Population()

        seed_ind = Population.make_individual(base_workflow, generation=0)
        pop.add(seed_ind)
        self._novelty.add(base_workflow)

        target_size = cfg.population_size
        attempts = 0
        max_attempts = target_size * 10
        while pop.size < target_size and attempts < max_attempts:
            attempts += 1
            result = apply_random_mutation(
                base_workflow,
                self._strategy,
                generation=0,
                frozen_nodes=set(cfg.frozen_node_ids),
            )
            if result is None:
                continue
            mutated_wf, mutation_rec = result
            if not self._novelty.is_novel(mutated_wf):
                continue
            self._novelty.add(mutated_wf)
            ind = Population.make_individual(
                mutated_wf,
                generation=0,
                parent_id=seed_ind.id,
                mutation_record=mutation_rec,
            )
            pop.add(ind)

        log.info("population_seeded", size=pop.size, target=target_size)
        return pop

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

        # Evaluate current population
        for ind in population.individuals:
            if self._budget.exhausted:
                break
            wf = Workflow.from_dict(ind.workflow_data)  # type: ignore[arg-type]
            ev = self._evaluator.evaluate(wf, project_dir, instances)
            self._budget.consume(1, cost_usd=ev.cost_usd)
            updated = ind.model_copy(update={"score": ev.score, "cost_usd": ev.cost_usd})
            population.remove(ind.id)
            population.add(updated)
            self._archive.add(updated)

        # Select parents and create offspring
        mutations_applied: list[MutationRecord] = []
        novel_count = 0
        rejected_dupes = 0
        offspring: list[tuple[Workflow, MutationRecord, str]] = []

        mutation_rate = self._strategy.get_mutation_rate(generation)
        for _ in range(self._config.population_size):
            parent = self._archive.sample_parent(self._config.tournament_size)
            if parent is None:
                continue
            parent_wf = Workflow.from_dict(parent.workflow_data)  # type: ignore[arg-type]
            mutation_result = apply_random_mutation(
                parent_wf,
                self._strategy,
                generation,
                frozen_nodes=set(self._config.frozen_node_ids),
            )
            if mutation_result is None:
                continue
            child_wf, mutation_rec = mutation_result
            if self._novelty.is_novel(child_wf):
                self._novelty.add(child_wf)
                offspring.append((child_wf, mutation_rec, parent.id))
                mutations_applied.append(mutation_rec)
                novel_count += 1
            else:
                rejected_dupes += 1

        # Evaluate offspring and add to population
        for child_wf, mutation_rec, parent_id in offspring:
            if self._budget.exhausted:
                break
            eval_result = self._evaluator.evaluate(child_wf, project_dir, instances)
            self._budget.consume(1, cost_usd=eval_result.cost_usd)
            ind = Population.make_individual(
                child_wf,
                generation=generation,
                parent_id=parent_id,
                mutation_record=mutation_rec,
                score=eval_result.score,
                cost_usd=eval_result.cost_usd,
            )
            population.add(ind)
            self._archive.add(ind)

        # Track best score
        best = population.best()
        best_score = best.score if best else 0.0
        mean_score = population.mean_score()
        diversity = self._archive.diversity_metric()
        self._score_trajectory.append(best_score)

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

        return GenerationSummary(
            generation=generation,
            population_size=population.size,
            best_score=best_score,
            mean_score=mean_score,
            diversity=diversity,
            mutations_applied=mutations_applied,
            novel_count=novel_count,
            rejected_duplicates=rejected_dupes,
            hyperparameters=hp_record,
        )

    def _detect_plateau(self) -> bool:
        """Detect plateau: 3 consecutive non-improving generations."""
        if len(self._score_trajectory) < PLATEAU_WINDOW + 1:
            return False
        recent = self._score_trajectory[-(PLATEAU_WINDOW + 1):]
        baseline = recent[0]
        return all(s <= baseline for s in recent[1:])

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
            # Give one extra generation after plateau adaptation
            if len(self._score_trajectory) >= PLATEAU_WINDOW + 2:
                recent = self._score_trajectory[-(PLATEAU_WINDOW + 2):]
                if all(s <= recent[0] for s in recent[1:]):
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
        return "unknown"
