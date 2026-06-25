# sop-compact — re:factory agent

Standard operating procedure for the sop-compact PreCompact sidecar when running
inside a re:factory workspace. The PreCompact hook reads this file to know what to
promote and what to snapshot before context compaction.

## Promotion targets

Durable learnings go here (direct file edits by the sidecar):

- `.refactory/CLAUDE.md` — workspace-level instructions, validated patterns, recurring
  gotchas discovered during supervision. Append to the existing content; do not
  overwrite the preamble.

## Snapshot conventions

The handoff snapshot should capture non-reconstructable in-flight state:

- **Active CEO sessions**: run `factory tmux-ls` to list running factory sessions and
  their current status. Record which projects have active loops and their last cycle.
- **Project score trajectory**: recent score changes, whether scores are trending up or
  down, and any plateau/regression patterns observed this session.
- **Backlog state**: items recently added, removed, or reprioritized. Note any items
  the user explicitly deferred or promoted.
- **In-flight decisions**: what the user and agent were mid-discussing — open questions,
  half-formed directions, rejected approaches and why.

## Live-state checks

Before writing the snapshot, check these for current ground truth:

- `factory tmux-ls` — which factory sessions are running
- `factory status .` — project status if inside a project
- `git status` — uncommitted changes in the workspace

## In-flight work locations

These files contain ephemeral state that may be lost in compaction:

- `.factory/strategy/current.md` — the current hypothesis or focus area
- `.factory/reviews/` — recent agent review outputs and CEO verdicts
- `.factory/strategy/backlog.md` — the working backlog
