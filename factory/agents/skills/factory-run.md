# /factory-run — CEO Dispatch

Use this skill to launch, monitor, and manage factory CEO runs.

**Always use `factory tmux`** for dispatch. This creates a detached tmux session with an interactive CEO inside — the user can attach and watch. The CEO runs as a normal `claude` session (not headless).

## Dispatch Modes

**Single cycle (default):**
```bash
factory tmux <project_path>
```
Launches in a detached tmux session. The user can attach to interact.

**Long-running improvement loop:**
```bash
factory tmux <project_path> --loop
factory tmux <project_path> --loop --interval 1800  # custom interval (seconds)
```

**Targeted single-item build:**
```bash
factory tmux <project_path> --focus "<backlog item or issue>"
factory tmux <project_path> --focus 42          # GitHub issue number
factory tmux <project_path> --focus "owner/repo#42"
```

**Mode selection:**
```bash
factory tmux <project_path> --mode improve   # default — score-driven improvement
factory tmux <project_path> --mode design    # brainstorm what to work on first
factory tmux <project_path> --mode research  # research-driven improvement
factory tmux <project_path> --mode meta      # improve the factory itself + ACE evolution
factory tmux <factory_project_path> --mode create --focus "mode description"  # create new factory mode
```

## Post-Dispatch Verification

After every `factory tmux <path>` dispatch, always verify the session started successfully before reporting to the user:

```bash
tmux has-session -t <session_name> 2>/dev/null && echo "alive" || echo "dead"
factory tmux-capture <path>     # or: tmux capture-pane -t <session_name> -p | tail -20
```

If the session exited or shows error output (`Error:`, `exited`, `no server`), report the failure immediately. Never assume a dispatch succeeded without checking.

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
| Managing 2+ projects simultaneously | `factory tmux <path> --loop` for each |
| User asks "work on this project" | `factory tmux <path>` |
| User asks to build one specific thing | `factory tmux <path> --focus "<item>"` |
| User wants to discuss what to work on | `factory tmux <path> --mode design` |
| User wants to create a new factory mode | `factory tmux /path/to/factory --mode create --focus "description"` |

Always check `factory tmux-ls` before dispatching to avoid launching duplicate sessions for the same project.
