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

### Sending Input to Sessions

Always use `C-m` (not `Enter`) when sending keys to tmux sessions running Claude Code:
```bash
tmux send-keys -t <session_name> "your input" C-m
```
`Enter` is unreliable inside Claude Code sessions — `C-m` is the canonical carriage return.

### Capturing Output

Use `factory tmux-capture` to inspect session output:
```bash
factory tmux-capture <project_path>                 # last 100 lines
factory tmux-capture --session <name> --lines -200  # custom line count
```

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

## Proactive Monitoring

After dispatching CEO sessions, set up periodic monitoring using `ScheduleWakeup` to detect completion and report results without being asked:

```
ScheduleWakeup({
  delaySeconds: 300,
  reason: "checking CEO session status for <project>",
  prompt: "<the /loop prompt>"
})
```

Each monitoring check should:
1. Run `factory tmux-ls` to see if the session is still active
2. If active, use `factory tmux-capture <path>` to check for progress or errors
3. If completed, review results via `.factory/reviews/` and `factory eval <path>`
4. Report findings proactively to the user
