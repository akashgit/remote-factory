# /factory-run — CEO Dispatch

Use this skill to launch, monitor, and manage factory CEO runs.

**Always use `factory ceo <path> --tmux-persist`** for dispatch. This launches the CEO interactively in its own tmux session (not headless) so the user can attach and watch. Do NOT use `factory tmux --tmux-persist` — that creates double-nested tmux sessions.

## Dispatch Modes

**Single cycle (default):**
```bash
factory ceo <project_path> --tmux-persist
```
Launches in a detached tmux session. The user can attach to interact.

**Long-running improvement loop:**
```bash
factory ceo <project_path> --tmux-persist --loop
factory ceo <project_path> --tmux-persist --loop --interval 1800  # custom interval (seconds)
```

**Targeted single-item build:**
```bash
factory ceo <project_path> --tmux-persist --focus "<backlog item or issue>"
factory ceo <project_path> --tmux-persist --focus 42          # GitHub issue number
factory ceo <project_path> --tmux-persist --focus "owner/repo#42"
```

**Mode selection:**
```bash
factory ceo <project_path> --tmux-persist --mode improve   # default — score-driven improvement
factory ceo <project_path> --tmux-persist --mode design    # brainstorm what to work on first
factory ceo <project_path> --tmux-persist --mode research  # research-driven improvement
factory ceo <project_path> --tmux-persist --mode meta      # improve the factory itself + ACE evolution
```

## Monitor Running Sessions

```bash
factory tmux-ls
```
Lists all active factory tmux sessions with project paths and status.

## Stop a Session

```bash
factory tmux-stop --session <session_name>
factory tmux-stop --path <project_path>
```

## Check Results After Completion

1. Read `.factory/reviews/ceo-latest.md` in the project directory for the CEO's final output
2. Run `factory eval <project_path>` for the current composite score
3. Run `factory history <project_path>` for the full experiment log
4. Read `.factory/reviews/` for individual agent outputs (builder-latest.md, qa-latest.md, etc.)

## When to Use Which

| Scenario | Command |
|---|---|
| Managing 2+ projects simultaneously | `factory ceo <path> --tmux-persist --loop` for each |
| User asks "work on this project" | `factory ceo <path> --tmux-persist` |
| User asks to build one specific thing | `factory ceo <path> --tmux-persist --focus "<item>"` |
| User wants to discuss what to work on | `factory ceo <path> --tmux-persist --mode design` |

Always check `factory tmux-ls` before dispatching to avoid launching duplicate sessions for the same project.
