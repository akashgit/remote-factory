# Getting Started

This guide walks you through creating your first project with the Factory, from a one-line idea to a running, self-improving codebase.

## Prerequisites

Make sure you've completed the [Setup](setup.md) steps:

- Python 3.11+
- Claude Code installed and authenticated
- The Factory installed (`factory --help` should work)

## Creating a Project

The Factory accepts four types of input. Pick whichever matches where you are.

### From a prompt

The simplest path. Describe what you want and the Factory handles everything else:

```bash
factory ceo "Build a CLI that converts CSV to JSON with streaming support"
```

This will:

1. Create a project directory at `~/cursor-projects/build-a-cli-that-converts-csv-to-json-with-streami/`
2. Initialize a git repo
3. Save your prompt as the build spec (`.factory/strategy/current.md`)
4. Launch the CEO agent in interactive mode

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

The Factory clones the repo to a temp directory, discovers what it does, sets up evaluation dimensions, and starts improving it.

### From an existing directory

Point the Factory at a local codebase:

```bash
factory ceo ~/my-project
```

If the project already has a `.factory/` directory, the Factory resumes where it left off. If not, it runs discovery first — detecting the language, framework, and test setup — then starts improvement cycles.

## What Happens Next

Once the CEO agent launches, it follows a cycle:

1. **Detect** project state (new, discovered, initialized, etc.)
2. **Research** best practices and patterns
3. **Strategize** — generate ranked hypotheses for improvement
4. **Build** — implement one hypothesis on an experiment branch
5. **Evaluate** — score before and after
6. **Decide** — keep (score went up) or revert (score went down)
7. **Archive** — record the outcome for future learning

For a brand-new project from a prompt, the first cycle scaffolds the project, sets up tests and eval, and then starts iterating.

## Interactive vs Headless

By default, `factory ceo` launches an interactive Claude Code session — you can see what the agents are doing and intervene if needed:

```bash
factory ceo ~/my-project              # interactive (default)
factory ceo ~/my-project --headless   # pipe mode, no interaction
```

Headless mode is useful for scripting and automation.

## Continuous Improvement

Run the Factory in a loop so it keeps improving your project unattended:

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

## Focusing on a Specific Area

Narrow the Factory's efforts to a particular part of your codebase:

```bash
factory ceo ~/my-project --focus "authentication"
factory ceo ~/my-project --focus "dashboard UI"
```

At least 2 of the 3 generated hypotheses will target the focused area.

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
