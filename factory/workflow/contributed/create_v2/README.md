# create-v2 Workflow

Create mode with inference-time scaling: dynamic research directors, multi-strategy synthesis, user intent tracking, QA Director with workflow-specific testing, and Overwatch verification.

## Graph

```
init_user_intent (FnNode)
  → gate_has_factory (GateNode)
    → [PROCEED] graph_update → study → graph_explorer → concat_study
    → [HALT] discover → gate_factory_md_exists → factory_init → graph_update
  → concat_study → research_director (AgentNode/CEO)
    → strategy_director (AgentNode/CEO)
      → synthesize_strategy (AgentNode/STRATEGIST)
        → gate_strategy (GateNode/user)
          → [PROCEED] begin → builder → fork_qa
          → [RELOOP] strategy_director
  → fork_qa → health_checker, code_reviewer, qa_director → join_qa
    → synthesize_qa (FnNode) → gate_qa (GateNode/agent)
      → [PROCEED] gate_overwatch → gate_precheck → archivist_build
      → [RELOOP] builder
  → gate_overwatch (AgentNode/CEO) → overwatch verification
```

## Key differences from create (v1)

- **Research Director** dynamically decides N research directions
- **Strategy Director** spawns M strategy perspectives
- **QA Director** spawns K tailored test approaches for workflow-specific testing
- **Overwatch** verifies factory integration and mode registration
- **User Intent Ledger** tracks the original idea and all feedback

## Usage

```bash
factory ceo /path/to/factory --mode create --focus "mode description"
```
