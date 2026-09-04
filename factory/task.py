"""First-class Task abstraction with four-hook interface.

Defines what to evaluate (instances, setup, prompt, verify) independently
of modes, workflows, and the outer loop.

Architectural constraint: this module has ZERO module-level imports from
factory/workflow/, factory/outer_loop/, factory/agents/, or factory/compose.py.
"""

from __future__ import annotations

import re
import subprocess
import traceback
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = structlog.get_logger()

_SHELL_OPERATORS_RE = re.compile(r"&&|\|\||[;|]")


def _needs_shell(cmd: str) -> bool:
    """Return True if cmd contains shell operators that require shell=True."""
    return bool(_SHELL_OPERATORS_RE.search(cmd))


# ── Supporting Models ────────────────────────────────────────────


class TaskInstance(BaseModel):
    """Lightweight container yielded by Task.instances()."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    path: Path | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerifyResult(BaseModel):
    """Standardised verification + scoring output from Task.verify()."""

    model_config = ConfigDict(strict=True, extra="forbid")

    passed: bool
    score: float
    details: dict[str, Any] = Field(default_factory=dict)


# ── Scoring Contract (discriminated union) ───────────────────────


class PytestScoring(BaseModel):
    """Pytest partial-credit scoring."""

    model_config = ConfigDict(strict=True, extra="forbid")

    method: Literal["pytest"] = "pytest"
    partial_credit: bool = True


class ExitCodeScoring(BaseModel):
    """Binary pass/fail from subprocess exit code."""

    model_config = ConfigDict(strict=True, extra="forbid")

    method: Literal["exit_code"] = "exit_code"


class JSONScoring(BaseModel):
    """Extract numeric metric from JSON output."""

    model_config = ConfigDict(strict=True, extra="forbid")

    method: Literal["json"] = "json"
    metric_path: str = "score"


class ExactMatchScoring(BaseModel):
    """Exact-match comparison against expected answer."""

    model_config = ConfigDict(strict=True, extra="forbid")

    method: Literal["exact_match"] = "exact_match"
    answer_extraction: str = ""


ScoringContract = PytestScoring | ExitCodeScoring | JSONScoring | ExactMatchScoring


# ── Capability StrEnum ───────────────────────────────────────────


class Capability(StrEnum):
    """Closed set of capabilities that modes provide and tasks require."""

    CAN_MODIFY_CODE = "can_modify_code"
    CAN_RUN_TESTS = "can_run_tests"
    HAS_BUILDER = "has_builder"
    HAS_RESEARCHER = "has_researcher"
    HAS_STRATEGIST = "has_strategist"
    HAS_QUALITY_GATE = "has_quality_gate"
    HAS_PARALLELISM = "has_parallelism"
    HAS_CODE_REVIEW = "has_code_review"
    HAS_ADVERSARIAL_QA = "has_adversarial_qa"
    HAS_ARCHIVIST = "has_archivist"
    CAN_GENERATE_PROMPTS = "can_generate_prompts"
    CAN_RUN_SUBPROCESS = "can_run_subprocess"
    CAN_ACCESS_NETWORK = "can_access_network"
    HAS_HEALTH_CHECK = "has_health_check"
    CAN_ITERATE = "can_iterate"


CAPABILITY_ALIASES: dict[str, Capability] = {
    "codebase-analysis": Capability.HAS_RESEARCHER,
    "code-generation": Capability.CAN_MODIFY_CODE,
    "health-check": Capability.HAS_HEALTH_CHECK,
    "code-review": Capability.HAS_CODE_REVIEW,
    "adversarial-qa": Capability.HAS_ADVERSARIAL_QA,
    "observation": Capability.HAS_RESEARCHER,
}


# ── Task Constraints ─────────────────────────────────────────────


class TaskConstraints(BaseModel):
    """Operational limits for task execution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout: int = 600
    max_retries: int = 1
    required_capabilities: list[Capability] = Field(default_factory=list)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _coerce_capabilities(cls, v: object) -> list[Capability]:
        if isinstance(v, list):
            return [Capability(x) if isinstance(x, str) else x for x in v]
        return v  # type: ignore[return-value]


# ── Evaluator Reference ─────────────────────────────────────────


class EvaluatorRef(BaseModel):
    """Serialisable reference to an evaluator class."""

    model_config = ConfigDict(strict=True, extra="forbid")

    ref: str = ""  # "module.path:ClassName" or shorthand

    _SHORTHANDS: dict[str, str] = {
        "pytest": "factory.outer_loop.featurebench_evaluator:FeatureBenchEvaluator",
        "exit_code": "factory.outer_loop.evaluators.exit_code:ExitCodeEvaluator",
        "json": "factory.outer_loop.evaluators.json_evaluator:JSONEvaluator",
        "exact_match": "factory.outer_loop.evaluators.exact_match:ExactMatchEvaluator",
    }

    def resolve(self) -> Any:
        """Dynamically import and instantiate the evaluator."""
        import importlib

        target = self._SHORTHANDS.get(self.ref, self.ref)
        if ":" not in target:
            raise ValueError(
                f"Invalid evaluator ref {self.ref!r}. "
                f"Expected 'module:Class' or one of {sorted(self._SHORTHANDS)}"
            )
        module_path, class_name = target.rsplit(":", 1)
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()


# ── Instances / Setup / Prompt / Verify config sections ──────────


class InstancesConfig(BaseModel):
    """Configuration for the instances() hook (TOML section)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    format: str = "directory"
    source: str = ""


class SetupConfig(BaseModel):
    """Configuration for the setup() hook (TOML section)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    command: str = ""


class PromptConfig(BaseModel):
    """Configuration for the prompt() hook (TOML section)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    text: str = ""


class VerifyConfig(BaseModel):
    """Configuration for the verify() hook (TOML section)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    command: str = ""


# ── TaskDefinition (serialisable Pydantic model) ─────────────────


class TaskDefinition(BaseModel):
    """Serialisable, config-level representation of a Task.

    Contains TOML-friendly flat fields plus four-hook configuration
    sections.  ``from_toml()`` and ``to_task()`` bridge between config
    and live objects.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str = ""
    version: str = ""
    source: str = ""
    scoring: ScoringContract = Field(
        default_factory=PytestScoring,
        discriminator="method",
    )
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    evaluator_ref: EvaluatorRef = Field(default_factory=EvaluatorRef)
    instances_config: InstancesConfig = Field(default_factory=InstancesConfig)
    setup_config: SetupConfig = Field(default_factory=SetupConfig)
    prompt_config: PromptConfig = Field(default_factory=PromptConfig)
    verify_config: VerifyConfig = Field(default_factory=VerifyConfig)

    # ── Factory methods ──────────────────────────────────────────

    @classmethod
    def from_toml(cls, path: str | Path) -> TaskDefinition:
        """Parse a TOML task definition file."""
        import tomllib

        raw = tomllib.loads(Path(path).read_text())

        task_section = raw.get("task", {})
        instances_section = raw.get("instances", {})
        setup_section = raw.get("setup", {})
        prompt_section = raw.get("prompt", {})
        verify_section = raw.get("verify", {})
        scoring_section = raw.get("scoring", {})
        constraints_section = raw.get("constraints", {})

        # Build scoring contract from [scoring] section
        method = scoring_section.get("method", "pytest")
        scoring: ScoringContract
        if method == "pytest":
            scoring = PytestScoring(
                partial_credit=scoring_section.get("partial_credit", True),
            )
        elif method == "exit_code":
            scoring = ExitCodeScoring()
        elif method == "json":
            scoring = JSONScoring(
                metric_path=scoring_section.get("metric_path", "score"),
            )
        elif method == "exact_match":
            scoring = ExactMatchScoring(
                answer_extraction=scoring_section.get("answer_extraction", ""),
            )
        else:
            raise ValueError(f"Unknown scoring method: {method}")

        return cls(
            name=task_section.get("name", Path(path).stem),
            description=task_section.get("description", ""),
            version=task_section.get("version", ""),
            source=task_section.get("source", ""),
            scoring=scoring,
            constraints=TaskConstraints(
                timeout=constraints_section.get(
                    "timeout", scoring_section.get("timeout", 600)
                ),
                max_retries=constraints_section.get("max_retries", 1),
                required_capabilities=constraints_section.get(
                    "required_capabilities", []
                ),
            ),
            evaluator_ref=EvaluatorRef(ref=scoring_section.get("evaluator_ref", "")),
            instances_config=InstancesConfig(
                format=instances_section.get("format", "directory"),
                source=instances_section.get("source", ""),
            ),
            setup_config=SetupConfig(
                command=setup_section.get("command", ""),
            ),
            prompt_config=PromptConfig(
                text=prompt_section.get("text", ""),
            ),
            verify_config=VerifyConfig(
                command=verify_section.get("command", ""),
            ),
        )

    def to_task(self) -> Task:
        """Convert this definition to a live Task with default hook impls."""
        return Task(definition=self)

    def get_evaluator(self) -> Any:
        """Derive the correct Evaluator from the scoring contract.

        Uses lazy imports to preserve task.py independence.
        """
        if self.evaluator_ref.ref:
            return self.evaluator_ref.resolve()

        if isinstance(self.scoring, PytestScoring):
            from factory.outer_loop.featurebench_evaluator import (
                FeatureBenchEvaluator,
            )

            return FeatureBenchEvaluator()

        if isinstance(self.scoring, ExitCodeScoring):
            from factory.outer_loop.evaluators.exit_code import ExitCodeEvaluator

            return ExitCodeEvaluator()

        if isinstance(self.scoring, JSONScoring):
            from factory.outer_loop.evaluators.json_evaluator import JSONEvaluator

            return JSONEvaluator(metric_path=self.scoring.metric_path)

        if isinstance(self.scoring, ExactMatchScoring):
            from factory.outer_loop.evaluators.exact_match import ExactMatchEvaluator

            return ExactMatchEvaluator()

        raise TypeError(
            f"Unknown scoring contract type: {type(self.scoring).__name__}"
        )


# ── Task base class (four-hook interface) ────────────────────────


@dataclass
class _RunResult:
    """Lightweight result of a shell command execution."""

    returncode: int
    stdout: str
    stderr: str
    score: float = 0.0


def _build_verify_details(
    scoring_name: str,
    result: _RunResult,
    passed: bool,
    **extra: Any,
) -> dict[str, Any]:
    """Build a consistent details dict for VerifyResult across all scoring branches."""
    details: dict[str, Any] = {
        "scoring_contract": scoring_name,
        "returncode": result.returncode,
    }
    if not passed:
        details["stdout"] = result.stdout[:2000]
        details["stderr"] = result.stderr[:2000]
    details.update(extra)
    return details


class Task:
    """Base class implementing the four-hook interface.

    Default implementations use flat fields from an optional TaskDefinition.
    Subclasses override whichever hooks need real domain logic.
    """

    def __init__(self, definition: TaskDefinition | None = None) -> None:
        self._definition = definition or TaskDefinition(name=self._derive_name())

    # ── Introspection ────────────────────────────────────────────

    @property
    def definition(self) -> TaskDefinition:
        return self._definition

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def scoring(self) -> ScoringContract:
        return self._definition.scoring

    @property
    def constraints(self) -> TaskConstraints:
        return self._definition.constraints

    # ── Four hooks ───────────────────────────────────────────────

    def instances(self) -> Iterator[TaskInstance]:
        """Discover what to work on.

        Default: yield a single instance with id="default".
        Override for multi-instance tasks (directory scanning, API queries).
        """
        cfg = self._definition.instances_config
        if cfg.source and cfg.format == "directory":
            source = Path(cfg.source)
            if source.is_dir():
                for subdir in sorted(source.iterdir()):
                    if subdir.is_dir():
                        yield TaskInstance(id=subdir.name, path=subdir)
                return
        yield TaskInstance(id="default")

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        """Prepare the environment for one instance.

        Default: run setup_config.command if set, else no-op.
        """
        cmd = self._definition.setup_config.command
        if not cmd:
            return
        expanded = cmd.replace("{instance_id}", instance.id)
        if instance.path is not None:
            expanded = expanded.replace("{instance_dir}", str(instance.path))
        self.shell(expanded, cwd=workspace)

    def prompt(self, instance: TaskInstance) -> str:
        """What should the agent do for this instance?

        Default: return prompt_config.text if set, else generic prompt.
        """
        text = self._definition.prompt_config.text
        if text:
            return text
        return "Implement the feature. All tests must pass."

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        """Did it work? Returns unified VerifyResult with pass/fail + score.

        Default: run verify_config.command and parse based on scoring contract.
        """
        cmd = self._definition.verify_config.command
        if not cmd:
            return VerifyResult(passed=False, score=0.0)

        result = self.shell(cmd, cwd=workspace)
        scoring = self._definition.scoring

        if isinstance(scoring, PytestScoring):
            score = self._parse_pytest_score(result.stdout)
            passed = result.returncode == 0
            return VerifyResult(
                passed=passed,
                score=score,
                details=_build_verify_details("PytestScoring", result, passed),
            )

        if isinstance(scoring, ExitCodeScoring):
            passed = result.returncode == 0
            s = 1.0 if passed else 0.0
            return VerifyResult(
                passed=passed,
                score=s,
                details=_build_verify_details("ExitCodeScoring", result, passed),
            )

        if isinstance(scoring, JSONScoring):
            return self._parse_json_verify(result, scoring.metric_path)

        if isinstance(scoring, ExactMatchScoring):
            return self._parse_exact_match_verify(result, scoring, workspace, instance)

        passed = result.returncode == 0
        return VerifyResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            details=_build_verify_details("unknown", result, passed),
        )

    # ── Utilities ────────────────────────────────────────────────

    def shell(self, cmd: str, cwd: Path | None = None) -> _RunResult:
        """Run a shell command and return a _RunResult."""
        import shlex as _shlex

        try:
            if _needs_shell(cmd):
                argv: str | list[str] = cmd
                use_shell = True
            else:
                argv = _shlex.split(cmd)
                use_shell = False
            proc = subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=self._definition.constraints.timeout,
                shell=use_shell,
            )
            score = 0.0
            if proc.returncode == 0:
                score = 1.0
            return _RunResult(
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                score=score,
            )
        except subprocess.TimeoutExpired:
            return _RunResult(returncode=-1, stdout="", stderr="timeout", score=0.0)
        except Exception as exc:
            log.error("task_run_failed", cmd=cmd, exc_info=True)
            return _RunResult(
                returncode=-1,
                stdout="",
                stderr=f"{exc}\n{traceback.format_exc()}",
                score=0.0,
            )

    def run(
        self,
        instance: TaskInstance,
        workspace: Path,
        workflow: Any = None,
    ) -> VerifyResult:
        """Unified execution entrypoint: setup → CEO subprocess → verify.

        Subclasses can override for bundled execution (e.g. HarborTask).
        Does NOT import from factory/workflow/ or factory/agents/ — shells out.
        """
        import sys as _sys
        import tempfile

        self.setup(instance, workspace)

        prompt_text = self.prompt(instance)
        prompt_file = Path(tempfile.mktemp(
            suffix=".md", prefix="task-prompt-", dir=str(workspace),
        ))
        prompt_file.write_text(prompt_text)

        mode_name = "improve"
        if workflow is not None:
            mode_name = getattr(workflow, "name", "improve")

        cmd = [
            _sys.executable, "-m", "factory", "ceo", str(workspace),
            "--mode", mode_name, "--headless", "--no-worktree",
            "--prompt", str(prompt_file),
        ]
        try:
            subprocess.run(
                cmd,
                cwd=str(workspace),
                timeout=self._definition.constraints.timeout,
            )
        except subprocess.TimeoutExpired:
            log.warning("task_run_timeout", instance=instance.id)
        except Exception:
            log.error("task_run_failed", instance=instance.id, exc_info=True)
        finally:
            if prompt_file.exists():
                prompt_file.unlink()

        return self.verify(instance, workspace)

    def get_evaluator(self) -> Any:
        """Delegate to TaskDefinition.get_evaluator()."""
        return self._definition.get_evaluator()

    def to_definition(self) -> TaskDefinition:
        """Convert this Task to its serialisable TaskDefinition."""
        return self._definition

    # ── Factory methods ──────────────────────────────────────────

    @classmethod
    def from_legacy(
        cls,
        name: str,
        test_command: str = "",
        test_format: str = "pytest",
        metric_path: str = "score",
        instance_format: str = "directory",
        prep_command: str = "",
    ) -> Task:
        """Construct a Task from legacy flat fields (backward compat)."""
        scoring: ScoringContract
        if test_format == "exit_code":
            scoring = ExitCodeScoring()
        elif test_format == "json":
            scoring = JSONScoring(metric_path=metric_path)
        elif test_format == "exact_match":
            scoring = ExactMatchScoring()
        else:
            scoring = PytestScoring()

        defn = TaskDefinition(
            name=name,
            scoring=scoring,
            instances_config=InstancesConfig(format=instance_format),
            setup_config=SetupConfig(command=prep_command),
            verify_config=VerifyConfig(command=test_command),
        )
        return cls(definition=defn)

    @classmethod
    def from_toml(cls, path: str | Path) -> Task:
        """Load a Task from a TOML file."""
        defn = TaskDefinition.from_toml(path)
        return cls(definition=defn)

    # ── Private helpers ──────────────────────────────────────────

    def _derive_name(self) -> str:
        """Derive a kebab-case name from the class name."""
        cls_name = type(self).__name__
        if cls_name == "Task":
            return "default"
        # CamelCase → kebab-case
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", cls_name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()

    @staticmethod
    def _parse_pytest_score(stdout: str) -> float:
        """Parse pytest stdout for pass/fail counts → fraction score."""
        # Collection/fixture errors are included in the denominator and score 0.0.
        # Match patterns like "5 passed, 3 failed" or "8 passed"
        passed = 0
        failed = 0
        errors = 0
        m = re.search(r"(\d+) passed", stdout)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", stdout)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) error", stdout)
        if m:
            errors = int(m.group(1))
        total = passed + failed + errors
        if total == 0:
            return 0.0
        return passed / total

    @staticmethod
    def _parse_json_verify(
        result: _RunResult, metric_path: str
    ) -> VerifyResult:
        """Parse JSON stdout and extract metric."""
        import json

        try:
            data = json.loads(result.stdout)
            obj: Any = data
            for key in metric_path.split("."):
                obj = obj[key]
            score = float(obj)
            passed = score > 0
            return VerifyResult(
                passed=passed,
                score=min(max(score, 0.0), 1.0),
                details=_build_verify_details(
                    "JSONScoring", result, passed,
                    metric_path=metric_path, raw_value=score,
                ),
            )
        except (Exception,):
            return VerifyResult(
                passed=False,
                score=0.0,
                details=_build_verify_details(
                    "JSONScoring", result, False,
                    error="json_parse_failed",
                ),
            )

    @staticmethod
    def _parse_exact_match_verify(
        result: _RunResult,
        scoring: ExactMatchScoring,
        workspace: Path,
        instance: TaskInstance | None = None,
    ) -> VerifyResult:
        """Compare output to expected answer."""
        output = result.stdout.strip()
        if scoring.answer_extraction:
            m = re.search(scoring.answer_extraction, output)
            if m:
                output = m.group(1)

        # Read expected answer from the trusted instance path first,
        # falling back to workspace only when instance.path is unavailable.
        search_dirs: list[Path] = []
        if instance is not None and instance.path is not None:
            search_dirs.append(instance.path)
        search_dirs.append(workspace)

        expected_path: Path | None = None
        for d in search_dirs:
            for name in ("expected_answer.txt", "expected.txt"):
                candidate = d / name
                if candidate.exists():
                    expected_path = candidate
                    break
            if expected_path is not None:
                break

        if expected_path is None:
            return VerifyResult(
                passed=False,
                score=0.0,
                details=_build_verify_details(
                    "ExactMatchScoring", result, False,
                    error="expected_answer_file_missing",
                ),
            )

        expected = expected_path.read_text(errors="replace").strip()
        matched = output == expected
        return VerifyResult(
            passed=matched,
            score=1.0 if matched else 0.0,
            details=_build_verify_details(
                "ExactMatchScoring", result, matched,
                matched=matched,
            ),
        )
