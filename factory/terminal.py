"""Live terminal status line — tails events.jsonl and renders agent-based progression."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from factory.visualizer.state import (
    FactoryLiveState,
    format_elapsed,
    update_state,
)

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

_OUTER_AGENTS = {"scrummaster", "ceo"}
_HIDDEN_AGENTS = {"archivist"}


@dataclass
class AgentRecord:
    role: str
    elapsed: str = ""
    started_at: str = ""
    archived: bool = False


@dataclass
class ExperimentRecord:
    exp_id: str
    hypothesis: str
    agents: list[AgentRecord] = field(default_factory=list)
    verdict: str | None = None


class TerminalStatus:
    """Tail .factory/events.jsonl in a background thread and render agent progression."""

    def __init__(self, project_path: Path, mode: str) -> None:
        self._project_path = project_path.resolve()
        self._mode = mode
        self._events_file = self._project_path / ".factory" / "events.jsonl"
        self._state = FactoryLiveState(current_mode=mode)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._cycle: int | None = None
        self._completed_pre: list[str] = []

        self._pre_loop: list[AgentRecord] = []
        self._experiments: list[ExperimentRecord] = []
        self._current_exp: ExperimentRecord | None = None
        self._last_agent: AgentRecord | None = None
        self._prev_line_count: int = 0

    def start(self) -> None:
        if not sys.stderr.isatty() or os.environ.get("NO_COLOR"):
            return
        self._active = True
        self._thread = threading.Thread(target=self._tail_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._active and self._prev_line_count > 0:
            if self._prev_line_count > 1:
                sys.stderr.write(f"\033[{self._prev_line_count - 1}A")
            for _ in range(self._prev_line_count):
                sys.stderr.write("\r\033[2K\n")
            sys.stderr.write(f"\033[{self._prev_line_count}A")
            sys.stderr.flush()

    def _agent_list(self) -> list[AgentRecord]:
        if self._current_exp:
            return self._current_exp.agents
        return self._pre_loop

    def _read_recent_cycle(self) -> None:
        if not self._events_file.exists():
            return
        try:
            size = self._events_file.stat().st_size
            read_from = max(0, size - 8192)
            with open(self._events_file) as f:
                if read_from > 0:
                    f.seek(read_from)
                    f.readline()
                for raw_line in f:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "cycle.started":
                        self._cycle = (event.get("data") or {}).get("cycle")
        except OSError:
            pass

    def _process_event(self, event: dict) -> None:
        self._state = update_state(self._state, event)
        etype = event.get("type", "")
        agent = event.get("agent", "")
        data = event.get("data") or {}
        ts = event.get("timestamp", "")

        if etype == "cycle.started":
            self._cycle = data.get("cycle")
            self._completed_pre.clear()
            self._pre_loop.clear()
            self._experiments.clear()
            self._current_exp = None
            self._last_agent = None

        elif etype == "agent.started":
            if agent in _OUTER_AGENTS:
                if agent not in self._completed_pre:
                    pass
            elif agent in _HIDDEN_AGENTS:
                pass
            else:
                rec = AgentRecord(role=agent, started_at=ts)
                self._agent_list().append(rec)
                self._last_agent = rec

        elif etype == "agent.completed":
            if agent in _OUTER_AGENTS:
                if agent not in self._completed_pre:
                    self._completed_pre.append(agent)
            elif agent in _HIDDEN_AGENTS:
                if self._last_agent and self._last_agent.role != agent:
                    self._last_agent.archived = True
            else:
                for rec in reversed(self._agent_list()):
                    if rec.role == agent and not rec.elapsed:
                        rec.elapsed = format_elapsed(rec.started_at)
                        break

        elif etype == "experiment.begin":
            exp_id = str(data.get("exp_id", "?"))
            hyp = data.get("hypothesis", "")
            self._current_exp = ExperimentRecord(exp_id=exp_id, hypothesis=hyp)

        elif etype == "experiment.finalize":
            if self._current_exp:
                verdict = data.get("verdict", "")
                self._current_exp.verdict = verdict.upper() if verdict else None
                self._experiments.append(self._current_exp)
                self._current_exp = None
                self._last_agent = None

    def _tail_loop(self) -> None:
        self._read_recent_cycle()
        pos = self._events_file.stat().st_size if self._events_file.exists() else 0

        while not self._stop.is_set():
            try:
                if self._events_file.exists():
                    size = self._events_file.stat().st_size
                    if size > pos:
                        with open(self._events_file) as f:
                            f.seek(pos)
                            for raw_line in f:
                                stripped = raw_line.strip()
                                if not stripped:
                                    continue
                                try:
                                    event = json.loads(stripped)
                                except json.JSONDecodeError:
                                    continue
                                self._process_event(event)
                        pos = size
            except OSError:
                pass
            self._render()
            self._stop.wait(1.0)

    def _render(self) -> None:
        if not self._state.active_agents and not self._completed_pre:
            return

        bold = "\033[1m"
        cyan = "\033[36m"
        green = "\033[32m"
        dim = "\033[2m"
        reset = "\033[0m"
        yellow = "\033[33m"
        red = "\033[31m"

        # Scrummaster active — just show scrummaster
        if "scrummaster" in self._state.active_agents:
            activity = self._state.active_agents["scrummaster"]
            elapsed = format_elapsed(activity.started_at)
            cycle_label = f"{dim}C{self._cycle}{reset} " if self._cycle else ""
            line = f"  {cycle_label}{bold}{cyan}scrummaster ● {elapsed}{reset}"
            self._write_lines([line])
            return

        # Header: scrummaster ✓ → ceo Xm:
        header = ""
        if "scrummaster" in self._completed_pre:
            header += f"{green}scrummaster ✓{reset} → "
        ceo_activity = self._state.active_agents.get("ceo")
        if ceo_activity:
            elapsed = format_elapsed(ceo_activity.started_at)
            header += f"{bold}{cyan}ceo {elapsed}:{reset} "
        elif "ceo" in self._completed_pre:
            header += f"{green}ceo ✓:{reset} "

        # Pre-loop agents — collapse once experiments start
        in_experiments = bool(self._experiments or self._current_exp)
        if in_experiments and self._pre_loop:
            all_done = all(r.elapsed for r in self._pre_loop)
            if all_done:
                pre_parts = [f"{green}[pre ✓]{reset}"]
            else:
                pre_parts = self._render_agents(self._pre_loop, bold, cyan, green, dim, reset)
        else:
            pre_parts = self._render_agents(
                self._pre_loop, bold, cyan, green, dim, reset, show_active=True,
            )

        # Experiments
        exp_parts: list[str] = []
        total_exp = len(self._experiments) + (1 if self._current_exp else 0)
        for i, exp in enumerate(self._experiments, 1):
            verdict_str = ""
            if exp.verdict == "KEEP":
                verdict_str = f" {green}KEEP{reset}"
            elif exp.verdict:
                verdict_str = f" {red}{exp.verdict}{reset}"
            exp_agents = self._render_agents(exp.agents, bold, cyan, green, dim, reset)
            agents_str = " → ".join(exp_agents) if exp_agents else ""
            exp_parts.append(
                f"{yellow}H{i}/{total_exp}{reset}"
                f"{': ' + agents_str if agents_str else ''}"
                f"{verdict_str}"
            )

        if self._current_exp:
            idx = len(self._experiments) + 1
            exp_agents = self._render_agents(
                self._current_exp.agents, bold, cyan, green, dim, reset,
                show_active=True,
            )
            agents_str = " → ".join(exp_agents) if exp_agents else ""
            hyp = self._current_exp.hypothesis
            if len(hyp) > 40:
                hyp = hyp[:37] + "..."
            hyp_str = f" {dim}{hyp}{reset}" if hyp else ""
            exp_parts.append(
                f"{yellow}H{idx}/{total_exp}{hyp_str}{reset}"
                f"{': ' + agents_str if agents_str else ''}"
            )

        # Assemble multi-line
        cycle_label = f"{dim}C{self._cycle}{reset} " if self._cycle else ""
        pre_str = " → ".join(pre_parts) if pre_parts else ""

        lines: list[str] = []
        header_line = f"  {cycle_label}{header}"
        if pre_str:
            header_line += pre_str
        lines.append(header_line)

        for exp_str in exp_parts:
            lines.append(f"    {dim}│{reset} {exp_str}")

        self._write_lines(lines)

    def _render_agents(
        self,
        agents: list[AgentRecord],
        bold: str, cyan: str, green: str, dim: str, reset: str,
        *,
        show_active: bool = False,
    ) -> list[str]:
        parts: list[str] = []
        for rec in agents:
            arch = f"{dim}[a]{reset}" if rec.archived else ""
            if rec.elapsed:
                parts.append(f"{green}{rec.role} ✓ {rec.elapsed}{arch}{reset}")
            elif show_active and rec.started_at:
                elapsed = format_elapsed(rec.started_at)
                parts.append(f"{bold}{cyan}{rec.role} ● {elapsed}{arch}{reset}")
            else:
                parts.append(f"{dim}{rec.role}{reset}")
        return parts

    def _write_lines(self, lines: list[str]) -> None:
        try:
            cols = os.get_terminal_size(sys.stderr.fileno()).columns
        except (ValueError, OSError):
            cols = 120

        # Move cursor up to overwrite previous render
        if self._prev_line_count > 1:
            sys.stderr.write(f"\033[{self._prev_line_count - 1}A")

        # Write each line
        for i, line in enumerate(lines):
            visible_len = len(_ANSI_RE.sub("", line))
            if visible_len > cols:
                line = line[:cols]
            if i < len(lines) - 1:
                sys.stderr.write(f"\r\033[2K{line}\n")
            else:
                sys.stderr.write(f"\r\033[2K{line}")

        # Clear leftover lines from previous render
        extra = self._prev_line_count - len(lines)
        if extra > 0:
            for _ in range(extra):
                sys.stderr.write("\n\033[2K")
            sys.stderr.write(f"\033[{extra}A")

        sys.stderr.flush()
        self._prev_line_count = len(lines)
