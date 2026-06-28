# /sessions — Active Session Tracking

Use this skill to track, health-check, and review factory CEO sessions.

## List Active Sessions

```bash
factory tmux-ls
```
Shows all active factory tmux sessions. Each entry includes the session name and project path. Run this frequently while CEO sessions are active.

## Health Check a Session

Verify a tmux session is alive and the CEO process is running:
```bash
tmux has-session -t <session_name> 2>/dev/null && echo "alive" || echo "dead"
tmux list-panes -t <session_name> -F '#{pane_pid}' 2>/dev/null
```
If the session exists but the CEO process has exited, the session is stale — stop it and dispatch a fresh one if needed.

## User Attach Guidance

If the user wants to watch or interact with a running CEO session:
```
tmux attach -t <session_name>
```
- `Ctrl-b d` to detach without stopping the session
- `Ctrl-c` inside the session will interrupt the CEO — warn the user

## Post-Completion Review

When a CEO session finishes:

1. **Read agent outputs:** Check `.factory/reviews/` in the project directory — `ceo-latest.md`, `builder-latest.md`, `qa-latest.md` contain the latest agent outputs
2. **Check scores:** `factory eval <project_path>` for the current composite score
3. **Check history:** `factory history <project_path>` for the experiment log — look at the latest entry for the verdict (KEEP/REVERT) and score delta
4. **Check strategy:** Read `.factory/strategy/current.md` for what the CEO planned and `.factory/strategy/observations.md` for what was observed

Summarize findings to the user: what was attempted, what was the verdict, what's the score delta.

## Concurrent Multi-Project Management

You can have multiple CEO sessions running simultaneously across different projects. Best practices:

- Track which projects have active sessions to avoid duplicate launches
- Use `factory tmux-ls` as your dashboard — run it periodically
- When a session completes, review results before deciding whether to launch another cycle
- Stagger launches to avoid resource contention on the host machine
- If multiple sessions are running, check each project's results systematically — don't let completed sessions go unreviewed
