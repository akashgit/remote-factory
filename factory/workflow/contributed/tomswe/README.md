# ToM-SWE Benchmark Workflow

Preference-aware task solving under deliberately vague instructions.

[ToM-SWE](https://github.com/All-Hands-AI/ToM-SWE) (ICML 2026, OpenHands) evaluates
stateful SWE agents via 15 developer profiles. Tasks are deliberately vague — the agent
must infer user intent from context clues and follow the user's coding preferences
(naming conventions, testing approach, git workflow, documentation habits) as described
in an embedded user profile.

## Pipeline

```
study ──► builder ──► gate_verify ──► auto_merge
              ▲            │
              └── RELOOP ──┘
```

- **study**: Discover repo structure, read task instruction with embedded user profile
- **builder**: Opus agent (7200s, 3 iterations) — infer intent, apply preferences, implement, test, commit
- **gate_verify**: fn evaluator — check commits exist + test pass/fail signals
- **auto_merge**: Fast-forward main to the working branch

## Usage

```bash
factory workflow run tomswe .
```

## What Makes ToM-SWE Different

| Aspect | SWE-bench | ToM-SWE |
|--------|-----------|---------|
| Instructions | Explicit bug description | Deliberately vague |
| User context | None | Embedded developer profile |
| Agent behavior | Fix the described bug | Infer intent + follow preferences |
| Evaluation | Patch correctness | Task resolution + preference alignment |

## MVP Approach

The user profile is embedded directly in `/tmp/task-instruction.md` as a `## User Profile`
section. The builder reads both the vague task description and the profile as static context.
No sidecar services, no LLM-powered simulator, no session management.
