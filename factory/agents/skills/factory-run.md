# /factory-run — CEO Dispatch

Use this skill to launch, monitor, and manage factory CEO runs.

**Always use `factory tmux --tmux-persist`** for all dispatch. This runs the CEO interactively in a tmux window (not headless) so you or the user can attach and watch.

## Dispatch Modes

**Long-running improvement (preferred for multi-project):**
```bash
factory tmux <project_path> --tmux-persist --loop
factory tmux <project_path> --tmux-persist --loop --interval 1800  # custom interval (seconds)
```
Runs in a detached tmux session. Sessions persist and you can check back later.

**Single cycle:**
```bash
factory tmux <project_path> --tmux-persist
```

**Targeted single-item build:**
```bash
factory tmux <project_path> --tmux-persist --focus "<backlog item or issue>"
factory tmux <project_path> --tmux-persist --focus 42          # GitHub issue number
factory tmux <project_path> --tmux-persist --focus "owner/repo#42"
```

**Mode selection:**
```bash
factory tmux <project_path> --tmux-persist --mode improve   # default — score-driven improvement
factory tmux <project_path> --tmux-persist --mode design    # brainstorm what to work on first
factory tmux <project_path> --tmux-persist --mode research  # research-driven improvement
factory tmux <project_path> --tmux-persist --mode meta      # improve the factory itself + ACE evolution
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
| Managing 2+ projects simultaneously | `factory tmux <path> --tmux-persist --loop` for each |
| User asks "work on this project" | `factory tmux <path> --tmux-persist` |
| User asks to build one specific thing | `factory tmux <path> --tmux-persist --focus "<item>"` |
| User wants to discuss what to work on | `factory tmux <path> --tmux-persist --mode design` |

Always check `factory tmux-ls` before dispatching to avoid launching duplicate sessions for the same project.
