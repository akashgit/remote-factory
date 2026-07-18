"""Composable node factories for the knowledge workflow.

Each function returns a configured node that can be embedded into any workflow.
"""

from __future__ import annotations

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
)


# ── tau-bench nodes ─────────────────────────────────────────────


def make_run_eval_node(node_id: str = "run_eval") -> FnNode:
    """Run tau-bench evaluation and record baseline score."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            'python3 -c "'
            "from pathlib import Path; "
            "from factory.knowledge.tau_adapter import run_tau_eval; "
            "run_tau_eval(Path('.factory/knowledge/task_config.json'))"
            '"'
        ),
        notes="Run tau-bench evaluation and record score in task_config.json.",
        writes={".factory/knowledge/simulation.json"},
    )


def make_extract_tau_node(node_id: str = "extract_tau") -> FnNode:
    """Parse tau-bench simulation JSON into knowledge graph triplets."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "task_id = cfg['task_id']; "
            "sim_path = pathlib.Path(cfg['simulation_path']); "
            "from factory.knowledge.tau_adapter import parse_simulation; "
            "triplets = parse_simulation(sim_path, cfg.get('task_context', task_id)); "
            "out = [t.model_dump(mode='json') for t in triplets]; "
            "pathlib.Path(f'.factory/knowledge/{task_id}_det_triplets.json').write_text("
            "    json.dumps(out, indent=2, default=str)); "
            "print(f'Extracted {len(out)} tau-bench triplets')"
            '"'
        ),
        notes="Parse tau-bench simulation JSON into triplets via the tau adapter.",
        reads={".factory/knowledge/simulation.json"},
        writes={".factory/knowledge/det_triplets.json"},
    )


def make_gate_score_node(node_id: str = "gate_score") -> GateNode:
    """Gate on tau-bench score — PROCEED if meets threshold, RELOOP to improve."""
    return GateNode(
        id=node_id,
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "score = cfg.get('current_score', 0.0); "
            "threshold = cfg.get('score_threshold', 0.8); "
            "if score is not None and score >= threshold: "
            "    print(f'pass: score {score:.4f} meets threshold {threshold}'); "
            "else: "
            "    print(f'reloop: score {score} below threshold {threshold}')"
            '"'
        ),
        reads={".factory/knowledge/insights.json"},
    )


def make_improve_node(node_id: str = "improve") -> AgentNode:
    """Apply knowledge graph insights to improve the tau-bench agent."""
    return AgentNode(
        id=node_id,
        role=AgentRole.BUILDER,
        model="opus",
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "You are improving a tau-bench airline agent based on failure analysis.\n\n"
            "1. Read `.factory/knowledge/task_config.json` for `task_id`, "
            "`improvement_target`, and current scores.\n"
            "2. Read `.factory/knowledge/{task_id}_insights.json` for failure patterns.\n"
            "3. Read `.factory/knowledge/{task_id}_report.md` for the full analysis.\n"
            "4. Read the improvement target file(s) in `improvement_target`.\n\n"
            "Focus on:\n"
            "- Failed NL assertions: what did the agent do wrong?\n"
            "- Failed action checks: what expected actions were missing?\n"
            "- DB check failures: what state changes were incorrect?\n"
            "- Causal chains tracing failures to root causes\n\n"
            "Make ONE focused improvement per iteration. Only modify files "
            "listed in `improvement_target`.\n"
            "Write a brief summary of what you changed and why to "
            "`.factory/knowledge/{task_id}_improvement.md`.\n"
        ),
        reads={".factory/knowledge/insights.json"},
        writes={".factory/knowledge/improvement.md"},
    )


def make_re_eval_node(node_id: str = "re_eval") -> FnNode:
    """Re-run tau-bench evaluation after improvements."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            'python3 -c "'
            "from pathlib import Path; "
            "from factory.knowledge.tau_adapter import run_tau_eval; "
            "run_tau_eval(Path('.factory/knowledge/task_config.json'), is_reeval=True)"
            '"'
        ),
        notes="Re-run tau-bench evaluation after improvements and update current_score.",
        reads={".factory/knowledge/improvement.md"},
        writes={".factory/knowledge/simulation.json"},
    )


def make_gate_compare_node(node_id: str = "gate_compare") -> GateNode:
    """Compare before/after tau-bench scores — PROCEED if threshold met."""
    return GateNode(
        id=node_id,
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "baseline = cfg.get('baseline_score', 0.0); "
            "current = cfg.get('current_score', 0.0); "
            "threshold = cfg.get('score_threshold', 0.8); "
            "if current is not None and current >= threshold: "
            "    print(f'pass: score {current:.4f} meets threshold {threshold} "
            "(baseline was {baseline:.4f})'); "
            "elif current is not None and current > baseline: "
            "    print(f'reloop: improved {baseline:.4f} -> {current:.4f} "
            "but still below threshold {threshold}'); "
            "else: "
            "    print(f'reloop: no improvement ({baseline} -> {current}), "
            "try a different approach')"
            '"'
        ),
        reads={".factory/knowledge/simulation.json"},
    )


def make_observe_node(node_id: str = "observe") -> FnNode:
    """Run the external agent and capture its output."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            "mkdir -p .factory/knowledge && "
            'TASK_CONFIG=".factory/knowledge/task_config.json" && '
            'if [ ! -f "$TASK_CONFIG" ]; then '
            'echo "Error: .factory/knowledge/task_config.json not found" >&2; exit 1; fi && '
            'TASK_ID=$(python3 -c "'
            "import json; c=json.load(open('.factory/knowledge/task_config.json')); "
            "print(c['task_id'])"
            '") && '
            'AGENT_CMD=$(python3 -c "'
            "import json; c=json.load(open('.factory/knowledge/task_config.json')); "
            "print(c['agent_command'])"
            '") && '
            'echo "Running observation for task $TASK_ID" && '
            'eval "$AGENT_CMD" > ".factory/knowledge/${TASK_ID}_observation.log" 2>&1 || true && '
            'echo "Observation captured to .factory/knowledge/${TASK_ID}_observation.log"'
        ),
        notes="Run the external agent and capture stdout/stderr to an observation log.",
        writes={".factory/knowledge/observation.log"},
    )


def make_extract_deterministic_node(
    node_id: str = "extract_deterministic",
) -> FnNode:
    """Parse observation log for structured tool calls and extract triplets."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib, re; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "task_id = cfg['task_id']; "
            "log_path = pathlib.Path(f'.factory/knowledge/{task_id}_observation.log'); "
            "log_text = log_path.read_text() if log_path.exists() else ''; "
            "from factory.knowledge.extractor import extract_from_tool_calls; "
            "tool_calls = []; "
            "tc_match = re.search(r'TOOL_CALLS_JSON:(.*?)END_TOOL_CALLS', log_text, re.DOTALL); "
            "if tc_match: tool_calls = json.loads(tc_match.group(1)); "
            "result = extract_from_tool_calls(tool_calls, cfg.get('task_context', task_id)); "
            "out = [t.model_dump(mode='json') for t in result.triplets]; "
            "pathlib.Path(f'.factory/knowledge/{task_id}_det_triplets.json').write_text("
            "json.dumps(out, indent=2, default=str)); "
            "print(f'Extracted {len(out)} deterministic triplets')"
            '"'
        ),
        notes="Extract triplets deterministically from structured tool call traces in the observation log.",
        reads={".factory/knowledge/observation.log"},
        writes={".factory/knowledge/det_triplets.json"},
    )


def make_extract_llm_node(node_id: str = "extract_llm") -> AgentNode:
    """Use a researcher agent to extract semantic triplets from the log."""
    return AgentNode(
        id=node_id,
        role=AgentRole.RESEARCHER,
        model="sonnet",
        timeout=120,
        prompt_template=(
            "You are extracting knowledge graph triplets from an agent execution log.\n\n"
            "1. Read `.factory/knowledge/task_config.json` to get the `task_id` and `task_context`.\n"
            "2. Read `.factory/knowledge/{task_id}_observation.log`.\n"
            "3. Analyze the log and extract triplets representing the agent's behavior:\n"
            "   - What tools did the agent call? Did they succeed or fail?\n"
            "   - What errors occurred? What caused them?\n"
            "   - What tasks did the agent attempt? What was the outcome?\n"
            "   - What preconditions were missing?\n\n"
            "4. Write a JSON array of triplet objects to "
            "`.factory/knowledge/{task_id}_llm_triplets.json`.\n\n"
            "Each triplet must have:\n"
            '- subject: {"id": "type:slug", "type": "<entity_type>", "name": "Human Name"}\n'
            "- predicate: one of: calls, fails_with, succeeds_at, fails_at, produces, "
            "requires, precedes, causes, is_a, part_of, related_to, contradicts, "
            "improves, degrades, correlates_with\n"
            '- object: {"id": "type:slug", "type": "<entity_type>", "name": "Human Name"}\n'
            "- confidence: 0.0-1.0 (1.0 for directly observed, 0.7-0.9 for inferred)\n"
            "- evidence: relevant log snippet\n\n"
            "Entity types: agent, tool, action, error, task, environment, concept, outcome.\n"
            "Entity IDs must use format `type:snake_case_name`.\n"
        ),
        reads={".factory/knowledge/observation.log"},
        writes={".factory/knowledge/llm_triplets.json"},
    )


def make_update_graph_node(node_id: str = "update_graph") -> FnNode:
    """Merge deterministic and LLM-extracted triplets into the knowledge graph."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib, asyncio; "
            "from factory.knowledge.store import KnowledgeStore; "
            "from factory.knowledge.models import Triplet; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "task_id = cfg['task_id']; "
            "store = KnowledgeStore(pathlib.Path('.')); "
            "all_triplets = []; "
            "for suffix in ['det', 'llm']: "
            "    p = pathlib.Path(f'.factory/knowledge/{task_id}_{suffix}_triplets.json'); "
            "    if p.exists(): "
            "        items = json.loads(p.read_text()); "
            "        all_triplets.extend(Triplet.model_validate(t, strict=False) for t in items); "
            "graph = asyncio.run(store.append_triplets(task_id, all_triplets)); "
            "print(f'Graph updated: {graph.entity_count()} entities, {graph.triplet_count()} triplets')"
            '"'
        ),
        notes="Merge extracted triplets into the persistent knowledge graph.",
        reads={
            ".factory/knowledge/det_triplets.json",
            ".factory/knowledge/llm_triplets.json",
        },
        writes={".factory/knowledge/graph.json"},
    )


def make_analyst_node(node_id: str = "analyst") -> AgentNode:
    """Knowledge analyst agent explores the graph and generates insights."""
    return AgentNode(
        id=node_id,
        role=AgentRole.KNOWLEDGE_ANALYST,
        model="opus",
        timeout=900,
        max_iterations=3,
        prompt_template=(
            "Explore the knowledge graph and generate insights.\n\n"
            "Read `.factory/knowledge/task_config.json` to get the `task_id`.\n"
            "The graph is at `.factory/knowledge/{task_id}.json`.\n"
            "Write insights to `.factory/knowledge/{task_id}_insights.json`.\n\n"
            "Use `python3 -c` commands to query the graph — see your system prompt "
            "for the full list of available operations (stats, query, traverse, "
            "causal_chain, find_paths, match_pattern, related_entities).\n\n"
            "Start with stats() to understand the graph, then explore failure "
            "patterns, causal chains, contradictions, and improvement opportunities.\n"
        ),
        reads={".factory/knowledge/graph.json"},
        writes={".factory/knowledge/insights.json"},
    )


def make_gate_insights_node(node_id: str = "gate_insights") -> GateNode:
    """Gate on insight quality — RELOOP if insufficient."""
    return GateNode(
        id=node_id,
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "task_id = cfg['task_id']; "
            "threshold = cfg.get('insight_threshold', 2); "
            "conf_threshold = cfg.get('confidence_threshold', 0.5); "
            "p = pathlib.Path(f'.factory/knowledge/{task_id}_insights.json'); "
            "if not p.exists(): print('reloop: no insights file found'); exit(); "
            "insights = json.loads(p.read_text()); "
            "if len(insights) < threshold: "
            "    print(f'reloop: only {len(insights)} insights, need at least {threshold}'); exit(); "
            "avg_conf = sum(i.get('confidence', 0) for i in insights) / len(insights) if insights else 0; "
            "if avg_conf < conf_threshold: "
            "    print(f'reloop: average confidence {avg_conf:.2f} below {conf_threshold}'); exit(); "
            "print(f'pass: {len(insights)} insights with avg confidence {avg_conf:.2f}')"
            '"'
        ),
        reads={".factory/knowledge/insights.json"},
    )


def make_report_node(node_id: str = "report") -> FnNode:
    """Format final insights into a human-readable report."""
    return FnNode(
        id=node_id,
        command=(
            "cd {project_path} && "
            'python3 -c "'
            "import json, pathlib; "
            "from factory.knowledge.models import KnowledgeGraph; "
            "from factory.knowledge.insight import Insight, format_insights; "
            "cfg = json.loads(pathlib.Path('.factory/knowledge/task_config.json').read_text()); "
            "task_id = cfg['task_id']; "
            "graph_path = pathlib.Path(f'.factory/knowledge/{task_id}.json'); "
            "insights_path = pathlib.Path(f'.factory/knowledge/{task_id}_insights.json'); "
            "if not graph_path.exists() or not insights_path.exists(): "
            "    print('No graph or insights to report'); exit(); "
            "graph = KnowledgeGraph.model_validate(json.loads(graph_path.read_text()), strict=False); "
            "insights = [Insight.model_validate(i, strict=False) for i in json.loads(insights_path.read_text())]; "
            "report = format_insights(insights, graph); "
            "pathlib.Path(f'.factory/knowledge/{task_id}_report.md').write_text(report); "
            "print(report)"
            '"'
        ),
        notes="Render insights as a markdown report.",
        reads={
            ".factory/knowledge/insights.json",
            ".factory/knowledge/graph.json",
        },
        writes={".factory/knowledge/report.md"},
    )
