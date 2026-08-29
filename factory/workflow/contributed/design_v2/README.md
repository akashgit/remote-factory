# design-v2 Workflow

Design mode with inference-time scaling: dynamic research, multi-strategy, user intent tracking, and parallel adversarial QA.

## Graph

```
init_user_intent (FnNode)
  → gate_has_factory (GateNode)
    → [PROCEED] graph_update → study → graph_explorer → concat_study
    → [HALT] discover → gate_factory_md_exists → factory_init → graph_update
  → concat_study → research_director (AgentNode/CEO)
    → strategy_director (AgentNode/CEO)
      → synthesize_strategy (AgentNode/STRATEGIST)
        → design_doc (AgentNode/STRATEGIST)
          → gate_strategy (GateNode/user)
            → [PROCEED] begin → builder → fork_qa
            → [RELOOP] strategy_director
  → fork_qa → health_checker, code_reviewer, qa_director → join_qa
    → synthesize_qa (FnNode) → gate_qa (GateNode/agent)
      → [PROCEED] gate_doc_freshness → gate_precheck → archivist_build
      → [RELOOP] builder
```

## Key differences from design (v1)

- **Research Director** replaces static fork/join research — dynamically decides N research directions
- **Strategy Director** replaces single strategist — spawns M strategy perspectives
- **QA Director** replaces single adversarial tester — spawns K tailored test approaches
- **User Intent Ledger** tracks the original idea and all feedback through the session
- **Design Doc** rewrites strategy into a human-readable design document
- **Synthesize QA** merges all adversarial reports with confidence scoring

## Usage

```bash
factory ceo /path/to/project --mode design-v2
```
