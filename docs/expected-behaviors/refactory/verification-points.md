# re:factory — Verification Points

## Expected Behaviors (Invariants)
These MUST hold regardless of the operational context. Check these against the agent's trace.

- [ ] Uses `factory tmux` for all CEO dispatch (not `factory ceo` in foreground)
- [ ] Monitors active sessions via `factory tmux-ls` and `factory status`
- [ ] Runs `factory discover` on uninitialized projects before dispatching CEO
- [ ] Checks `factory status <path>` before every dispatch — verifies project is initialized
- [ ] Handles compaction for long-running sessions — preserves context across CEO restarts
- [ ] Curates playbooks via `factory ace` — does not edit playbook files directly
- [ ] Reviews completed work by reading `.factory/reviews/ceo-latest.md` and running `factory eval`
- [ ] Persists across restarts via `--session-id` — resumes monitoring on restart
- [ ] Chooses correct dispatch mode based on user intent (`--loop`, `--focus`, `--mode design`, `--mode research`)
- [ ] Does not implement code, fix bugs, run tests, or edit source files

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| `Edit`/`Write` on source files (`.py`, `.ts`, `.go`, etc.) | Role violation — re:factory writing code directly |
| `factory ceo` without `factory tmux` wrapper | Foreground dispatch — blocks re:factory, no detached session |
| `factory agent builder/qa/researcher` calls | Hierarchy violation — re:factory spawning specialists directly |
| No `factory discover` before first CEO dispatch on new project | Uninitialized project — CEO will fail on missing config |
| No `factory tmux-ls` check before dispatching to same project | Possible duplicate CEO session on same project |
| Direct edits to `~/.factory/playbooks/*.md` without `factory ace` | Manual playbook edit — bypasses ACE evolution pipeline |

## Inputs & Outputs
- **Reads:** Session state (`~/.factory/refactory-session.json`), project paths, CEO transcripts, playbook files (`factory/agents/playbooks/*.md`, `~/.factory/playbooks/*.md`), `.factory/reviews/ceo-latest.md`, `.factory/events.jsonl`, project status and history
- **Writes:** CEO sessions (dispatched via `factory tmux`), compaction summaries, playbook updates (via `factory ace`)
- **Spawned by:** User directly (via `factory refactory` or `claude --session-id`)
- **Hands off to:** CEO (via `factory tmux` dispatch), ACE (via `factory ace` for playbook evolution)

## Forbidden Actions
- Writing source code or editing project source files directly
- Running evals directly (`factory eval` is allowed for monitoring, but not as a substitute for the CEO's eval lifecycle)
- Modifying project source files or `.factory/` internals (project state is owned by the CEO)
- Spawning specialist agents directly (Builder, QA, etc.) — only the CEO spawns specialists
- Using `factory ceo` in foreground mode for dispatch — always use `factory tmux` for detached sessions

## Playbook Rules
No evolved playbook rules for this agent.
