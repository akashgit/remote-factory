"""Instance preparation — prepare benchmark instances from config.

Runs prep_command from benchmark config, validates results, and creates
instance directories ready for the outer loop.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import structlog

from factory.outer_loop.benchmark_config import BenchmarkConfig

log = structlog.get_logger()

_SHELL_OPERATORS_RE = re.compile(r"&&|\|\||[;|]")


def _needs_shell(cmd: str) -> bool:
    """Return True if cmd contains shell operators that require shell=True."""
    return bool(_SHELL_OPERATORS_RE.search(cmd))


def prepare_instances_raw(
    prep_command: str,
    instance_format: str,
    instance_ids: list[str],
    output_dir: Path,
) -> list[Path]:
    """Stateless variant of prepare_instances — no BenchmarkConfig dependency.

    Can be used by Task.setup() default implementations without coupling to
    the BenchmarkConfig model.
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prepared: list[Path] = []

    for instance_id in instance_ids:
        instance_dir = output_dir / instance_id
        instance_dir.mkdir(parents=True, exist_ok=True)

        if prep_command:
            cmd = prep_command.replace(
                "{instance_id}", instance_id
            ).replace(
                "{instance_dir}", str(instance_dir)
            )

            use_shell = _needs_shell(cmd)
            log.info("prep_instance_raw", instance_id=instance_id, command=cmd, shell=use_shell)
            try:
                result = subprocess.run(
                    cmd if use_shell else shlex.split(cmd),
                    cwd=str(output_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,
                    shell=use_shell,
                )
                if result.returncode != 0:
                    log.error(
                        "prep_instance_raw_failed",
                        instance_id=instance_id,
                        returncode=result.returncode,
                        stderr=result.stderr[:500],
                    )
                    continue
            except subprocess.TimeoutExpired:
                log.error("prep_instance_raw_timeout", instance_id=instance_id)
                continue
            except Exception as exc:
                log.error("prep_instance_raw_error", instance_id=instance_id, error=str(exc))
                continue

        if validate_instance(instance_dir, instance_format):
            prepared.append(instance_dir)
            log.info("prep_instance_raw_ok", instance_id=instance_id)
        else:
            log.warning(
                "prep_instance_raw_invalid",
                instance_id=instance_id,
                format=instance_format,
            )

    return prepared


def prepare_instances(
    config: BenchmarkConfig,
    instance_ids: list[str],
    output_dir: Path,
) -> list[Path]:
    """Prepare benchmark instances using the config's prep_command.

    Expands template variables ({instance_id}, {instance_dir}) in prep_command,
    runs via subprocess, validates required files exist based on instance_format.
    Uses shell=True when the command contains shell operators (&&, ||, ;, |).

    Returns list of successfully prepared instance directories.
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prepared: list[Path] = []

    for instance_id in instance_ids:
        instance_dir = output_dir / instance_id
        instance_dir.mkdir(parents=True, exist_ok=True)

        if config.prep_command:
            cmd = config.prep_command.replace(
                "{instance_id}", instance_id
            ).replace(
                "{instance_dir}", str(instance_dir)
            )

            use_shell = _needs_shell(cmd)
            log.info("prep_instance", instance_id=instance_id, command=cmd, shell=use_shell)
            try:
                result = subprocess.run(
                    cmd if use_shell else shlex.split(cmd),
                    cwd=str(output_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,
                    shell=use_shell,
                )
                if result.returncode != 0:
                    log.error(
                        "prep_instance_failed",
                        instance_id=instance_id,
                        returncode=result.returncode,
                        stderr=result.stderr[:500],
                    )
                    continue
            except subprocess.TimeoutExpired:
                log.error("prep_instance_timeout", instance_id=instance_id)
                continue
            except Exception as exc:
                log.error("prep_instance_error", instance_id=instance_id, error=str(exc))
                continue

        if validate_instance(instance_dir, config.instance_format):
            prepared.append(instance_dir)
            log.info("prep_instance_ok", instance_id=instance_id)
        else:
            log.warning("prep_instance_invalid", instance_id=instance_id, format=config.instance_format)

    return prepared


def validate_instance(instance_dir: Path, instance_format: str) -> bool:
    """Validate that an instance directory matches the expected format."""
    if not instance_dir.exists():
        return False

    if instance_format == "git-repo":
        git_dir = instance_dir / ".git"
        if not git_dir.exists():
            return False
        try:
            result = subprocess.run(
                ["git", "fsck", "--quick"],
                cwd=str(instance_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    if instance_format == "question-answer":
        has_question = (instance_dir / "question.txt").exists() or (
            instance_dir / "question.md"
        ).exists()
        has_answer = (instance_dir / "answer.txt").exists() or (
            instance_dir / "expected.txt"
        ).exists()
        return has_question and has_answer

    return instance_dir.exists() and instance_dir.is_dir()
