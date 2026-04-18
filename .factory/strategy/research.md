# Research Report — remote-factory
Date: 2026-04-18

## Project Summary

The Remote Factory is a domain-agnostic multi-agent software evolution system built around a three-layer architecture: Python CLI tools (Layer 1), CEO orchestrator agent (Layer 2), and seven specialist agents (Layer 3). It auto-discovers evaluation dimensions for any project and continuously improves code through autonomous experiment loops with a 100% keep rate across 57 experiments in 3 projects (composite score: 0.802).

**Current weak dimensions:**
- config_parser: 0.0 (asyncio.run() bug in eval script)
- research_grounding: 0.575 (low doc utilization)
- capability_surface: 0.604 (169 surface vs 280 target)
- observability: 0.783 (46% function coverage)

## External Research

### Similar Projects & Systems

#### Self-Evolving Agent Architectures

**[Awesome-Self-Evolving-Agents Survey](https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents)** provides a comprehensive taxonomy organizing self-evolution into three dimensions:

1. **Model-Centric Evolution**: Inference-based (self-consistency, tree-of-thoughts, graph reasoning) and training-based (self-play fine-tuning, process reward guided tree search)
2. **Environment-Centric Evolution**: Static knowledge, dynamic experience, modular architecture changes, topology modifications
3. **Model-Environment Co-Evolution**: Simultaneous advancement of agent and surroundings

**Key techniques the factory could adopt:**
- **Experience management**: Compile offline experiences into actionable knowledge (currently we archive to Obsidian but don't reuse it for agent decisions)
- **Skill-augmented evolution**: Systematically discover and refine capabilities (our ACE playbook system is a primitive version of this)
- **Exploration-driven online improvement**: R-Zero/Absolute Zero frameworks that self-improve without labeled data (we rely on eval scores, not self-supervised discovery)

**[EvoScientist](https://evoailabs.medium.com/self-evolving-agents-open-source-projects-redefining-ai-in-2026-be2c60513e97)** (Andrej Karpathy): Agents modify their own training code, run tests, evaluate results, commit changes only if performance improves. This is exactly what factory Meta mode does, but Karpathy's approach focuses on model training loops rather than software artifacts.

#### Agent Orchestration Patterns

**Framework comparison findings:**

| Framework | Philosophy | Strengths | Failures |
|-----------|-----------|-----------|----------|
| **LangGraph** | Stateful graphs | Tight control, explicit state, easier tracing | Steep learning curve |
| **CrewAI** | Role-based teams | Simple, fast prototyping | No checkpointing, coarse error handling, migrations to LangGraph for production |
| **AutoGen** | Multi-agent conversations | Genuine collaboration, good for code gen | Unpredictable loops, expensive (20+ LLM calls for 4-agent 5-round debate), conversation context bloat |

**Critical patterns from [DataCamp comparison](https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen):**

- **Observability is mandatory**: All frameworks fail silently without tracing. LangSmith, LlamaTrace, Arize recommended.
- **State management beats conversation**: CrewAI's task-based mediation breaks down at scale; LangGraph's explicit state wins for production.
- **Turn limits prevent loops**: AutoGen agents can debate endlessly; production systems need timeouts, turn caps, referee logic.
- **Breaking changes are frequent**: CrewAI rewrote core API, AutoGen 0.4 is fundamentally different. Pin versions, expect instability.

**What the factory does well:**
- CEO uses explicit state machine (5 states) like LangGraph
- Specialists are non-interactive subprocesses (no conversation loops)
- Events.jsonl provides trace data

**What the factory could improve:**
- No turn limits or timeout enforcement for agent spawns
- Agent failures are logged but not recovered gracefully
- No explicit checkpointing within long-running cycles

#### Evaluation Frameworks

**[Anthropic's eval best practices](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):**

1. **Start with 20-50 real failures, not 100s of synthetic tasks** — early agents show large effect sizes
2. **Define success unambiguously** — "two domain experts would independently reach the same pass/fail verdict"
3. **Three grading tiers**: code-based (fast, cheap) → model-based (scalable) → human (gold standard)
4. **pass@k vs pass^k**: pass@k = at least one success in k tries; pass^k = all k trials succeed (critical for customer-facing agents)
5. **Avoid rigid grading**: Don't check specific tool call sequences; agents find valid alternatives
6. **Two-sided testing**: Test both when behavior should AND shouldn't occur
7. **Read transcripts regularly**: Understand why tasks fail to validate eval design

**Critical pitfall we have:**
- **Eval saturation**: Factory's 100% keep rate suggests evals may be too easy or not measuring the right things. Anthropic warns that near-100% pass rates make progress unmeasurable.

**[IBM/LXT agent evaluation guide](https://www.lxt.ai/blog/ai-agent-evaluation/):**
- Separate reasoning layer (planning/decision-making) from action layer (tool execution)
- Track self-improvement rate: is the agent getting better over time autonomously?
- Learning curve metrics: performance improvement with experience, generalization to new scenarios, recovery speed from mistakes

**What the factory could add:**
- Variance-aware acceptance: run evals multiple times, measure stability
- Decomposed scoring: separate planning quality from execution success
- Harder capability evals: current 100% keep rate suggests insufficient challenge

#### Prompt Evolution & Meta-Learning

**[Prompt Learning approach](https://arize.com/blog/claude-md-best-practices-learned-from-optimizing-claude-code-with-prompt-learning/)** (Arize AI):

The feedback loop:
1. Agent generates outputs on training examples
2. LLM evaluator explains **why** outputs succeeded/failed (not just binary pass/fail)
3. Meta-prompting LLM uses rich feedback to optimize system prompt
4. Test on holdout data, repeat until plateau

**Results on SWE Bench:**
- General coding: +5.19% accuracy
- Repository-specific: +10.87% accuracy

**Key insight**: "Specializing prompts to specific codebases actually enhances practical developer workflows, even if it appears as 'overfitting' from benchmarking perspectives."

**What the factory's ACE does well:**
- Reflects on experiment outcomes across projects
- Generates playbook bullets from patterns
- Injects evolved playbooks at runtime

**What ACE could improve:**
- No LLM explanation of *why* experiments succeed/fail (just keep/revert)
- No codebase-specific prompt specialization (all agents use same prompts across projects)
- No A/B testing of playbook variants
- No holdout validation of evolved playbooks

**[Claude Agent Skills architecture](https://medium.com/aimonks/claude-agent-skills-a-first-principles-deep-dive-into-prompt-based-meta-tools-022de66fc721):**
- Treats expertise as "reusable, versioned, composable instruction packages"
- Progressive disclosure layers: metadata → SKILL.md → resource loading
- On-demand capability loading (vs. monolithic system prompts that consume tokens always)

**How this maps to the factory:**
- Agent prompts (`factory/agents/prompts/*.md`) are like Skills
- Per-project overrides (`.factory/agents/*.md`) enable specialization
- Playbook injection is compositional

**Missing from the factory:**
- No versioning of agent prompts (git commit is the only version history)
- No metadata layer for selective loading
- No semantic packaging of capabilities

### Best Practices for Autonomous Software Evolution

#### Feature Growth vs Technical Debt Balance

**[Technical Debt Ratio](https://ltsgroup.tech/blog/how-to-measure-technical-debt/) (TDR):**

```
TDR = (Remediation Cost ÷ Development Cost) × 100
```

Healthy range: 10-20% of development time on debt reduction, 80-90% on features.

**[CTO Magazine finding](https://ctomagazine.com/tech-debt-vs-feature-velocity-balance/)**: "Technical debt and feature velocity aren't a tradeoff; they're a feedback loop. Teams that prioritize clean, maintainable systems sustain their speed over time."

**What the factory does:**
- 11 eval dimensions: 6 hygiene (tests, lint, type_check, coverage, guards, config) + 5 growth (capability_surface, experiment_diversity, observability, research_grounding, factory_effectiveness)
- 50/50 weighting between hygiene and growth in composite score
- FEEC priority (Fix > Exploit > Explore > Combine)

**What the factory should watch:**
- 100% keep rate suggests hygiene experiments are too easy or growth experiments aren't ambitious enough
- Current capability_surface score (0.604) indicates feature growth is lagging
- Strategist needs harder growth challenges

#### Measuring Agent Capabilities

**[METR's task length metric](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)**: AI performance measured by task completion time has doubled every 7 months. Claude Opus 4.5 can complete tasks with 50% success that take humans ~5 hours.

**[Anthropic's autonomy measurement](https://www.anthropic.com/research/measuring-agent-autonomy)**: Among longest-running Claude Code sessions, work time before stopping doubled in 3 months (25 min → 45 min).

**Implications for the factory:**
- **Capability surface** should track: number of tools/commands, API surface area, plugin/extension count
- **Task complexity** should be measured by estimated human-hours to complete
- **Autonomy duration** should track CEO cycle length before requiring human intervention

#### Self-Improvement Metrics

**[LXT self-improvement framework](https://www.lxt.ai/blog/ai-agent-evaluation/):**
- Learning curve: does performance improve with experience?
- Generalization: does agent apply learnings to new scenarios?
- Recovery speed: how quickly does agent adapt after mistakes?

**What the factory tracks:**
- Experiment keep rate (100% — suspiciously high)
- Cross-project pattern discovery (57 experiments → 3 playbook bullets)
- Stuck detection (3+ consecutive same-category reverts)

**What the factory doesn't track:**
- Generalization: does a hypothesis kept in project A work when applied to project B?
- Learning rate: is the CEO getting faster/better over cycles?
- Playbook effectiveness: do evolved playbooks actually improve keep rate?

## Specific Capability Gaps to Address

### 1. config_parser eval bug (asyncio.run inside event loop)

**Problem:** Line 332 of `eval/score.py` calls `asyncio.run(store.reparse_config())`, but the eval script runs inside the factory's async eval runner, which already has a running event loop.

**Error:** `RuntimeError: asyncio.run() cannot be called from a running event loop`

**Fix options** (from [Python docs](https://docs.python.org/3/library/asyncio-eventloop.html) and [community discussions](https://github.com/langchain-ai/langchain/issues/8494)):

1. **Use `await` directly** (preferred):
   ```python
   # Change eval_config_parser() to async
   async def eval_config_parser() -> dict:
       ...
       config = await store.reparse_config()
       ...
   
   # Update main() to run async evals
   async def main() -> None:
       results = [await fn() if asyncio.iscoroutinefunction(fn) else fn() for fn in EVALS]
       ...
   ```

2. **Get existing loop**:
   ```python
   loop = asyncio.get_event_loop()
   config = loop.run_until_complete(store.reparse_config())
   ```

3. **Use nest_asyncio** (hacky, avoid):
   ```python
   import nest_asyncio
   nest_asyncio.apply()
   config = asyncio.run(store.reparse_config())
   ```

**Recommendation:** Option 1 (async/await) is cleanest. Make `eval_config_parser()` async, update `main()` to handle mixed sync/async evals. This aligns with the factory's "async by default" style (CLAUDE.md line 29).

### 2. Research grounding (0.575) — low doc utilization

**Current measurement:**
- Counts markdown files in project
- Parses `# Research` sections, counts references
- Score = min(1.0, reference_count / (doc_count * 2))

**Problem:** Factory has comprehensive docs (README.md, CLAUDE.md, factory.md, agent prompts, ACE playbooks) but agents don't consistently cite or build on them.

**Improvements:**
- **Document linking graph**: Track which docs reference each other (wikilink analysis)
- **Citation enforcement**: Researcher agent should cite specific docs in observations.md
- **Reuse metrics**: Count how often prior experiment learnings inform new hypotheses
- **Obsidian vault utilization**: Measure how often agents read from `~/obsidian-vaults/factory/` during cycles

### 3. Capability surface (0.604) — 169 vs 280 target

**Current measurement:**
- CLI commands: 21
- Agent roles: 7
- Public API functions: 141
- **Total surface: 169** (target: 280 for score 1.0)

**Gap analysis:**

| Category | Current | Ideas for Expansion |
|----------|---------|---------------------|
| CLI commands | 21 | Add: `rollback`, `compare`, `replay`, `export`, `import`, `clone`, `fork` |
| Agent roles | 7 | Split Researcher (web vs local), add Optimizer, Tester, Deployer |
| API functions | 141 | Public APIs for: event streaming, playbook CRUD, experiment replay, cross-project queries |
| Integrations | 2 (Obsidian, Telegram) | Add: Slack, Discord, GitHub Actions, webhooks, Prometheus/Grafana |
| MCP tools | 0 (factory uses but doesn't provide) | Expose factory as MCP server: `get_project_score`, `run_experiment`, `list_projects` |
| Output formats | 1 (JSON) | Add: YAML, TOML, CSV export, HTML reports, Markdown digests |

**High-impact additions:**
1. **Experiment replay**: `factory replay <path> --experiment 5` — reapply a past experiment to current state
2. **Cross-project hypothesis transfer**: `factory clone-hypothesis wxo/001 --to erica-agent` — test if a winning hypothesis generalizes
3. **MCP server mode**: Expose factory data/operations as MCP tools for other Claude sessions
4. **Comparative eval**: `factory compare <path1> <path2>` — benchmark two projects side-by-side
5. **Streaming API**: WebSocket/SSE endpoint for live experiment progress (beyond current dashboard)

### 4. Observability (0.783) — 46% function coverage

**Current measurement:**
- Total functions: 173
- Functions with logging: 79 (46%)
- Log statement density: 191 total logs

**Uninstrumented modules** (from observations.md):
- `factory/models.py` (1 function, 0 logs)
- `factory/dashboard/app.py` (9 functions, 0 logs)
- `factory/obsidian/templates.py` (5 functions, 0 logs)
- `factory/ace/models.py` (6 functions, 0 logs)

**Targeted improvements:**
1. **Agent spawn/completion tracing**: Log every `factory agent <role>` invocation with task, duration, success/failure
2. **Eval run telemetry**: Structured logs for each dimension's execution time and score delta
3. **State transition logging**: Log every project state change with reason (detected via `state.py`)
4. **Hypothesis generation tracing**: Log Strategist's FEEC categorization and ranking logic
5. **Cross-project insights**: Log pattern discovery in `insights.py` with confidence scores

**Target:** 85%+ function coverage (140/173 functions instrumented) to match industry standards from [observability research](https://www.pwc.com/us/en/tech-effect/ai-analytics/ai-observability.html).

## Ideas for New Features (Capability Surface Expansion)

### High-Impact Features

1. **Experiment Replay & Time Travel**
   - `factory replay <path> --experiment N` — reapply past experiment to current state
   - `factory timeline <path>` — visual DAG of experiment lineage
   - `factory branch <path> --from 10` — create divergent experiment timeline from past point
   - **Enables:** A/B testing of hypotheses, "what if" exploration, regression debugging

2. **Cross-Project Knowledge Transfer**
   - `factory clone-hypothesis wxo/001 --to erica-agent` — test if winning hypothesis generalizes
   - `factory recommend <path>` — suggest hypotheses from similar projects' success patterns
   - `factory meta-insights` — mine vault for universal patterns across all managed projects
   - **Enables:** Faster improvement cycles, cross-pollination of ideas, meta-learning

3. **Factory as MCP Server**
   - Expose factory operations as MCP tools for other Claude Code sessions:
     - `get_project_score(path)` → current composite score
     - `run_experiment(path, hypothesis)` → spawn CEO cycle
     - `list_projects()` → all factory-managed projects
     - `get_experiment_history(path)` → full TSV data
   - **Enables:** Claude sessions can query project health, trigger improvements, integrate factory into broader workflows

4. **Comparative Evaluation**
   - `factory compare <path1> <path2>` — side-by-side eval scores, hypothesis strategies
   - `factory leaderboard --projects-dir ~/cursor-projects` — rank all projects by composite score
   - `factory diff-strategy <path1> <path2>` — compare FEEC distributions, category preferences
   - **Enables:** Competitive benchmarking, identification of underperforming projects, strategy analysis

5. **Streaming Progress API**
   - WebSocket/SSE endpoint for live experiment progress (beyond current dashboard polling)
   - `factory follow <path>` — tail-f style live event stream in terminal
   - **Enables:** Real-time monitoring of long-running cycles, better UX for interactive CEO mode

6. **Hypothesis Templates & Cookbook**
   - `factory templates list` — show pre-built hypothesis patterns
   - `factory generate-hypothesis <path> --pattern "add-logging"` — scaffold from template
   - Templates: "add-logging", "fix-type-errors", "increase-coverage", "add-api-endpoint", "refactor-module"
   - **Enables:** Faster strategy generation, lower cognitive load for Strategist, consistency

7. **Multi-Repo Orchestration**
   - `factory orchestrate --projects ~/cursor-projects/*` — run CEO on all projects in parallel
   - `factory sync-playbooks` — propagate ACE learnings across all managed projects
   - `factory health-check --all` — scan for stuck projects, low scores, pending reviews
   - **Enables:** Fleet management, consistent improvement across portfolio, early warning system

### Medium-Impact Features

8. **Experiment Cost Tracking**
   - Add `cost_usd` field to `ExperimentRecord`
   - Track token usage via Claude API metadata
   - `factory cost-report <path>` — total spend, cost per kept experiment, ROI per score point
   - **Enables:** Budget management, optimization of expensive cycles

9. **Guardrail Visualization**
   - `factory guards show <path>` — render scope globs, guards as interactive tree
   - `factory guards test <path> <file>` — check if file matches modifiable scope
   - **Enables:** Debugging guard violations, clearer understanding of allowed changes

10. **Playbook A/B Testing**
    - ACE generates variant playbooks, tests each on holdout experiments
    - Tracks which playbook bullets correlate with kept experiments
    - Auto-prunes low-confidence bullets after N cycles
    - **Enables:** Scientific validation of ACE evolution, reduced playbook bloat

11. **Natural Language Query Interface**
    - `factory ask <path> "why was experiment 12 reverted?"` → LLM reads verdicts, explains
    - `factory ask <path> "what should I work on next?"` → consults strategy, suggests focus areas
    - **Enables:** Conversational access to factory data, lower barrier to insight

12. **Integration: GitHub Actions**
    - `.github/workflows/factory.yml` — auto-run factory on PR merge, comment eval results
    - Fail CI if composite score drops below threshold
    - **Enables:** Continuous improvement as part of standard dev workflow

## Prior Knowledge (from Obsidian Vault)

### Cross-Project Patterns

From `~/obsidian-vaults/factory/00-Factory/Patterns.md` and insights.md:

**High-confidence patterns (100% keep rate):**
- `bugfix_reliable`: 12 bugfix experiments across 3 projects, all kept
- `observability_reliable`: 6 observability experiments, all kept
- `feature_reliable`: 16 feature experiments, all kept
- `infrastructure_reliable`: 3 infrastructure experiments across 2 projects, all kept

**Playbook bullets evolved by ACE:**
- `[strat-00001]` (12 helpful, 0 harmful): "Prioritize bugfix hypotheses — 5/5 kept (100% success rate)"

**Interpretation:**
- Bugfixes are safe bets (always improve code)
- Features have high success when well-scoped
- Hygiene work (observability, testing) is always worthwhile
- Infrastructure changes require careful planning but succeed when attempted

**What's missing:**
- No losing patterns identified (100% keep rate means we don't learn from failures)
- No category-specific failure modes documented
- No velocity metrics (how long do different experiment types take?)

### Project Learnings

From `~/obsidian-vaults/factory/10-Projects/`:

**erica-agent** (real estate inquiry bot):
- Build phase: scaffold → core → CLI → observability
- 5/5 experiments kept, composite 1.0
- Lesson: E2E testing before optimization is critical (user feedback: "don't optimize before verifying core works")

**wxo** (control plane for AI agents):
- 33 experiments, 100% keep rate
- Strong feature growth (UI components, optimization modes)
- Lesson: Playwright MCP for UI projects is mandatory (user feedback: "factory was making UI changes blind")

**test-idea** (synthetic test):
- Perfect score (1.0) achieved through methodical phases
- Demonstrates factory can build from scratch

**cp-agent** (Next.js control plane):
- Weak test_coverage (0.43), otherwise healthy
- Shows factory can manage modern web stacks

### Anti-Patterns Identified

From current.md and auto-memory:

1. **Don't ask for permission during factory work** — be autonomous (user feedback: "we always run in fully skipping permission mode")
2. **Prioritize features over hygiene** — factory historically biased toward hygiene, needs explicit feature push
3. **E2E before optimize** — prove core loop works before running hygiene experiments (user feedback from erica-agent)
4. **Visual testing for UI** — always use Playwright MCP to verify UI changes, never change blind
5. **Always archive** — Archivist must run after every phase, no exceptions
6. **11 eval dimensions are permanent** — 6 hygiene + 5 growth, applied to all projects, self-improvements must be permanent

## Recommended Focus Areas

### 1. Fix config_parser eval (CRITICAL)

**Why:** Currently at 0.0 due to asyncio.run() bug. Blocking accurate composite score calculation.

**How:** Convert `eval_config_parser()` to async, handle mixed sync/async evals in main(). 30-minute fix.

**Impact:** Immediate score correction, unblocks accurate measurement.

### 2. Challenge the 100% keep rate (HIGH PRIORITY)

**Why:** Anthropic warns that near-100% pass rates make progress unmeasurable. Factory may be selecting overly safe hypotheses or evals are too easy.

**How:**
- Add harder capability evals (new agent roles, complex integrations)
- Run variance testing: execute same eval 3x, measure stability
- Introduce "stretch hypotheses" category in FEEC (beyond Fix/Exploit/Explore/Combine)
- Track A/B playbook experiments where some must fail by design

**Impact:** Healthier learning signal, more ambitious improvements, real failure data to mine for anti-patterns.

### 3. Expand capability surface (MEDIUM PRIORITY)

**Why:** Currently 169/280 (60.4%), limiting factory's utility and discoverability.

**How:** Implement top 3 high-impact features:
1. **Experiment replay** (`factory replay <path> --experiment N`) — 1-2 days
2. **Factory as MCP server** (expose core operations to other Claude sessions) — 2-3 days
3. **Cross-project hypothesis transfer** (`factory clone-hypothesis`) — 1 day

**Impact:** +30-40 surface points, new integration paths, meta-learning acceleration.

### 4. Instrument uninstrumented modules (QUICK WIN)

**Why:** Observability at 78.3% due to 46% function coverage. Four modules have zero logging.

**How:** Add structured logging to:
- `factory/dashboard/app.py` (9 functions) — endpoint hits, SSE connections, project scans
- `factory/obsidian/templates.py` (5 functions) — template rendering, variable substitution
- `factory/ace/models.py` (6 functions) — playbook CRUD operations
- `factory/models.py` (1 function) — model validation failures

**Impact:** Observability score → 0.85+, better debugging, richer event stream for dashboard.

### 5. Codebase-specific prompt specialization (LONG-TERM)

**Why:** Arize AI showed +10.87% accuracy gain from repo-specific prompt tuning. Factory uses same agent prompts across all projects.

**How:**
- Extend ACE to generate per-project playbook variants
- Track which playbook bullets apply to which project types (Python vs Next.js vs mixed)
- Auto-specialize agent overrides (`.factory/agents/*.md`) based on project language/framework
- A/B test generic vs specialized prompts, measure keep rate delta

**Impact:** Higher quality hypotheses, faster convergence, better generalization vs specialization tradeoff.

## Sources

- [Cogent AI: Self-Evolving Software by 2026](https://cogentinfo.com/resources/ai-driven-self-evolving-software-the-rise-of-autonomous-codebases-by-2026)
- [Self-Evolving Agents: Open-Source Projects Redefining AI (Medium)](https://evoailabs.medium.com/self-evolving-agents-open-source-projects-redefining-ai-in-2026-be2c60513e97)
- [Awesome-Self-Evolving-Agents GitHub Survey](https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents)
- [CrewAI vs LangGraph vs AutoGen: Framework Comparison (DataCamp)](https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen)
- [Multi-Agent Frameworks for Enterprise AI (Adopt.ai)](https://www.adopt.ai/blog/multi-agent-frameworks)
- [Agent Orchestration 2026 Guide (Iterathon)](https://iterathon.tech/blog/ai-agent-orchestration-frameworks-2026)
- [Anthropic: Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [AI Agent Evaluation Frameworks (Online Inference / Medium)](https://medium.com/online-inference/ai-agent-evaluation-frameworks-strategies-and-best-practices-9dc3cfdf9890)
- [DeepEval: AI Agent Evaluation Guide](https://deepeval.com/guides/guides-ai-agent-evaluation)
- [AWS: Evaluating AI Agents — Real-World Lessons from Amazon](https://aws.amazon.com/blogs/machine-learning/evaluating-ai-agents-real-world-lessons-from-building-agentic-systems-at-amazon/)
- [LXT: AI Agent Evaluation Framework](https://www.lxt.ai/blog/ai-agent-evaluation/)
- [Claude Agent Skills: First-Principles Deep Dive (Medium)](https://medium.com/aimonks/claude-agent-skills-a-first-principles-deep-dive-into-prompt-based-meta-tools-022de66fc721)
- [Arize AI: Prompt Learning with Claude Code](https://arize.com/blog/claude-md-best-practices-learned-from-optimizing-claude-code-with-prompt-learning/)
- [Anthropic: Measuring AI Agent Autonomy](https://www.anthropic.com/research/measuring-agent-autonomy)
- [METR: Measuring AI Ability to Complete Long Tasks](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)
- [Technical Debt vs Feature Development: What to Prioritize (Metamindz)](https://www.metamindz.co.uk/post/technical-debt-vs-feature-development-what-to-prioritize)
- [Tech Debt vs Feature Velocity: Finding Balance (CTO Magazine)](https://ctomagazine.com/tech-debt-vs-feature-velocity-balance/)
- [How to Measure Technical Debt Metrics (LTS Group)](https://ltsgroup.tech/blog/how-to-measure-technical-debt/)
- [Python asyncio Event Loop Documentation](https://docs.python.org/3/library/asyncio-eventloop.html)
- [GitHub: Langchain asyncio.run() Issue](https://github.com/langchain-ai/langchain/issues/8494)
- [PWC: AI Observability Best Practices](https://www.pwc.com/us/en/tech-effect/ai-analytics/ai-observability.html)
