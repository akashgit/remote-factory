# Getting Started

This guide walks you through creating your first project with the Factory, from a one-line idea to a running, self-improving codebase.

## Prerequisites

Make sure you've completed the [Setup](setup.md) steps:

- Python 3.11+
- Claude Code installed and authenticated
- The Factory installed (`factory --help` should work)

## Build — Start from an Idea

The Factory accepts several types of input. Pick whichever matches where you are.

### From a prompt

The simplest path. Describe what you want and the Factory handles everything else:

```bash
factory ceo "Build a CLI that converts CSV to JSON with streaming support"
```

This will:

1. Create a project directory at `~/factory-projects/build-a-cli-that-converts-csv-to-json-with-streami/`
2. Initialize a git repo
3. Save your prompt as the build spec (`.factory/strategy/current.md`)
4. Launch the CEO agent in build mode

The directory name is derived from your prompt (lowercased, slugified, truncated to 50 chars). Set `FACTORY_PROJECTS_DIR` to change the parent directory:

```bash
export FACTORY_PROJECTS_DIR=~/my-projects
factory ceo "Build a weather dashboard"
# creates ~/my-projects/build-a-weather-dashboard/
```

### From an idea file

If you have a longer spec written up in a markdown file:

```bash
factory ceo ~/ideas/weather-dashboard.md
```

The Factory reads the file contents as the build spec and creates a project directory named after the file. This is useful when your idea needs more than a one-liner — write out the requirements, constraints, and examples in the file.

### From a GitHub repo

Clone and improve an existing repo:

```bash
factory ceo https://github.com/user/repo
```

The Factory clones the repo to a temporary directory, discovers what it does, sets up evaluation dimensions, and starts improving it. If you plan to keep the results, clone the repo yourself first and use the local directory path instead — temp directories don't survive reboots.

### Interactive ideation

If you have a rough idea but want to brainstorm before building:

```bash
factory ceo "distributed eval runner" --mode interactive
```

The CEO researches the space via the Researcher, then iteratively refines the idea with you through the Distiller agent. Once you approve the final spec, it proceeds to build.

## Improve — Make an Existing Codebase Better

Point the Factory at a local codebase:

```bash
factory ceo ~/my-project
```

If the project already has a `.factory/` directory, the Factory resumes where it left off. If not, it runs discovery first — detecting the language, framework, and test setup — then starts improvement cycles.

### What happens in a cycle

1. **Observe** — the Researcher analyzes the project and searches for best practices
2. **Hypothesize** — the Strategist generates ranked hypotheses from the backlog
3. **Build** — the Builder implements one hypothesis on an experiment branch
4. **Guard** — the Reviewer checks for guard violations
5. **Measure** — the Evaluator scores before and after
6. **Decide** — the CEO keeps (score went up) or reverts (score went down)
7. **Record** — the Archivist records the outcome for future learning

### Continuous improvement

`factory run` is equivalent to `factory ceo` but designed for unattended operation — run it in a loop so the Factory keeps improving your project:

```bash
factory run ~/my-project --loop                    # every 30 min (default)
factory run ~/my-project --loop --interval 900     # every 15 min
factory run ~/my-project --loop --max-cycles 5     # stop after 5 cycles
```

For long-running sessions, use tmux:

```bash
factory tmux ~/my-project --loop              # launches in a detached tmux session
factory tmux-ls                               # list active factory sessions
factory tmux-stop --path ~/my-project         # stop a session
```

### Interactive vs headless

By default, `factory ceo` launches an interactive Claude Code session — you can see what the agents are doing and intervene if needed:

```bash
factory ceo ~/my-project              # interactive (default)
factory ceo ~/my-project --headless   # pipe mode, no interaction
```

## Focus — Build Exactly One Thing

When you know exactly what you want built, `--focus` pins a single item from the backlog, generates one hypothesis, runs one experiment, and exits:

```bash
factory ceo ~/my-project --focus "add authentication middleware"
factory ceo ~/my-project --focus "fix the CSV export bug"
factory ceo ~/my-project --focus "add structured logging"
```

If the item isn't already in the backlog, it gets added automatically. The entire pipeline is scoped to that single target:

- The **Researcher** focuses its web research on the target item
- The **Strategist** generates exactly one hypothesis — no other backlog items are touched
- The **Builder** implements it on an experiment branch
- After the keep/revert decision, the cycle ends — no looping back for more hypotheses

`--focus` requires the project to already be built (improve mode). It's mutually exclusive with `--loop`.

## Interactive — Brainstorm Before Building

When you have a rough idea but want to explore the space before committing to a design:

```bash
factory ceo "distributed eval runner" --mode interactive
factory ceo "personal finance tracker" --mode interactive
```

Interactive mode runs a three-step loop before any code is written:

1. **Research** — the Researcher surveys similar projects, tech stacks, architecture patterns, and pitfalls
2. **Distill** — the Distiller synthesizes the research into a structured project spec (features, architecture, non-goals)
3. **Iterate** — the CEO presents the draft to you. Give feedback, ask for changes, or request more research on a specific topic. The Distiller revises until you approve.

Once you sign off, the spec is persisted and the Factory proceeds to Build mode. Phase 0 research is broad ("what should we build?"); Build mode does a second, focused research pass ("how do we build it?").

`--mode interactive` is incompatible with `--headless` and `--focus`.

## Writing a `factory.md`

Once the CEO creates your project, it auto-generates a `factory.md` configuration file. You can also write one manually for more control:

```markdown
## Goal
A CLI tool that converts CSV files to JSON with streaming support.

## Scope
### Modifiable
- src/**
- tests/**

## Guards
- Do not delete existing tests
- Do not modify files outside scope

## Eval
### Command
pytest --tb=short -q

### Threshold
0.8
```

See the [Configuration Reference](configuration.md) for all available sections.

## Next Steps

- [Configuration Reference](configuration.md) — all `factory.md` options
- [Architecture](architecture.md) — how the CEO and specialist agents work
- [Eval System](eval.md) — how projects are scored
- [Self-Improvement Loop](self-improvement.md) — how agents evolve over time
