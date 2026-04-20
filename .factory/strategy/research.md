# Research Report — remote-factory
Date: 2026-04-20

## Project Summary

The remote-factory is a three-layer AI agent orchestration system designed for self-improving software development. It consists of a Python CLI, a CEO Agent orchestrator, and seven specialist agents (Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, CEO). The factory uses a structured evaluation system with 11 dimensions (6 hygiene + 5 growth) and implements an ACE-style self-improvement loop. Current composite score: 0.8009 with key weaknesses in experiment_diversity (0.53), capability_surface (0.62), and research_grounding (0.645).

## External Research

### Similar Projects

- **[ACE Framework](https://github.com/ace-agent/ace)** — Self-improving LLM contexts via Generator/Reflector/Curator pattern, achieving +10.6% agent task performance and 86.9% latency reduction through evolving playbooks with helpful/harmful counters
- **[Meta's Hyperagents](https://venturebeat.com/orchestration/meta-researchers-introduce-hyperagents-to-unlock-self-improving-ai-for-non-coding-tasks)** — Self-referential AI systems that rewrite their own problem-solving logic through metacognitive self-modification
- **[LangGraph Multi-Agent Orchestration](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier)** — Production patterns for hierarchical agent coordination, checkpointing resilience, and parallel execution
- **[Confident AI Evaluation Framework](https://www.confident-ai.com/blog/definitive-ai-agent-evaluation-guide)** — Comprehensive agent evaluation through 5 pillars: performance, safety, user experience, trajectory vs outcome metrics, and component-level assessment

### Best Practices

- **Orchestration Patterns**: Sequential (linear dependencies), Hierarchical (tiered supervision), Orchestrator-Worker (central delegation), and Choreography (event-driven coordination) as the four fundamental patterns
- **Evaluation Design**: Component-level vs trajectory-level metrics, with LLM-as-a-Judge pattern for automated assessment and bias mitigation through ensemble approaches
- **Self-Improvement**: ACE's Reflect→Curate→Inject cycle with structured playbook evolution, helpful/harmful counters, and deterministic merging to prevent context collapse
- **Knowledge Integration**: Vault-as-knowledge-layer pattern where Obsidian becomes an active participant in thinking through keyword-triggered automation and Claude Code integration

### Relevant Techniques

- **Capability Surface Measurement**: Track new API endpoints, public functions, tool usage patterns, and multi-step interaction complexity rather than just completion rates
- **Experiment Diversity**: Curate test datasets with diverse scenarios, use multi-turn simulation, and sample traces from different usage patterns to avoid evaluation saturation
- **Adaptive Orchestration**: Task-adaptive coordination strategy selection rather than fixed patterns, with performance tradeoffs (2.1% accuracy gain at 2x cost for multi-agent vs single-agent)
- **Context Engineering**: Structured bullet-point playbooks with metadata, unique IDs, and version control rather than monolithic prompts

## Prior Knowledge (from Vault)

### Cross-Project Patterns

**Evaluation Anti-Patterns**:
- `eval_saturation`: 100% keep rates indicate evals are too easy or not measuring the right things
- `rigid_grading`: Don't check specific tool sequences; agents find valid alternatives

**Self-Evolution Patterns**:
- `experience_as_knowledge`: Compile offline experiences into queryable, actionable knowledge
- `capability_gap_analysis`: Analyze failures to identify missing tools/skills and auto-propose new capabilities
- `cross_project_hypothesis_transfer`: Winning hypotheses should be tested on similar projects

**Orchestration Patterns**:
- `checkpointing_resilience`: Save state after each agent completes; enable resume from checkpoint
- `parallel_agent_execution`: Run independent agents concurrently when possible
- `turn_limit_enforcement`: Production orchestrators must enforce turn limits and timeouts

**Balance Patterns**:
- `tech_debt_ratio`: Healthy systems spend 10-20% on debt reduction, 80-90% on features
- `feedback_loop_not_tradeoff`: Clean systems sustain feature velocity rather than trading off

## Recommended Focus Areas

### 1. Self-Improvement Infrastructure (HIGH PRIORITY)

**ACE Implementation for Factory Agents**: Based on the external ACE research, implement a proper Reflect→Curate→Inject cycle for all 7 agent roles. The current cross-project insights collection is a start, but we need structured playbook evolution with:
- Generator/Reflector/Curator roles for each agent type
- Helpful/harmful counters for playbook bullets
- Deterministic merging to prevent context collapse
- Per-project playbook variants based on codebase taxonomy

**Experiment Outcome Analysis**: Move beyond binary keep/revert to decomposed scoring: (1) hypothesis quality, (2) scope appropriateness, (3) implementation correctness, (4) eval improvement. This enables learning from partially-successful experiments and better pattern recognition.

### 2. Evaluation System Redesign (HIGH PRIORITY)

**Capability Surface Enhancement**: The current 0.62 score indicates we're not measuring the right surface. Based on evaluation research, track:
- New API endpoints and public functions (quantitative)
- Tool usage complexity and multi-step interactions (behavioral)
- Cross-domain knowledge integration (qualitative)
- Error recovery and edge case handling (resilience)

**Experiment Diversity Mechanisms**: Address the 0.53 score through:
- Curated test datasets with diverse scenarios per project type
- Multi-turn simulation for different usage patterns
- Two-sided testing (capability gains vs regressions)
- Variance testing to detect evaluation saturation

### 3. Knowledge Management Integration (MEDIUM PRIORITY)

**Real-Time Vault Querying**: Enable agents to query the factory vault mid-run for pattern matching and hypothesis generation. Build knowledge graph: projects → experiments → patterns → hypotheses.

**Automated Knowledge Capture**: Implement keyword-triggered automation similar to the Obsidian patterns research - scan agent outputs for learnings and automatically update vault structure.

### 4. Orchestration Resilience (MEDIUM PRIORITY)

**Checkpointing and Recovery**: Add `.factory/checkpoint.json` to save state after each agent completes, enabling `factory resume <path>` for crash recovery.

**Parallel Agent Execution**: Run independent operations concurrently (Researcher + initial eval in Discover mode, eval_before while Builder implements in Improve mode).

**Turn Limit Enforcement**: Add max-turn and wall-clock timeouts to prevent endless agent debates or stuck processes.

### 5. Meta-Learning Capabilities (RESEARCH PRIORITY)

**Cross-Project Hypothesis Transfer**: Implement automatic proposal of successful hypotheses from similar project types (if it worked in erica-agent, test it on cp-agent).

**Playbook Versioning**: Add metadata layer to agent prompts with version tags, effectiveness scores per bullet, and per-project applicability flags.

**MCP Server Exposure**: Make factory operations (scores, experiments, project lists) accessible via MCP server protocol to enable broader workflow integration.

Given the current score profile showing strong hygiene but weak growth dimensions, the factory should prioritize self-improvement infrastructure and evaluation redesign to break out of the hygiene-focused experiment trap and develop genuine capability expansion patterns.
