---
name: implement
description: "Build a specific feature or improvement using Factory's multi-agent system. Runs the Factory CEO in focus mode to study the codebase, generate a hypothesis, build the change, review it, and evaluate the result. Use when the user says 'implement X', 'build X', 'add X feature', or wants autonomous multi-agent development on a specific task."
disable-model-invocation: true
argument-hint: "<what to build>"
---

# /factory:implement

Build a specific feature or improvement using the Factory's multi-agent system.

The user wants to build: **$ARGUMENTS**

## Prerequisites

The `factory` CLI must be installed. Check and install from the plugin's bundled source:

```bash
command -v factory >/dev/null 2>&1 || uv tool install "${CLAUDE_PLUGIN_ROOT}"
```

## Execution

The project must have `.factory/` initialized (run `factory ceo "$(pwd)"` or `/factory:study` first). Focus mode requires improve mode — it will error if the project hasn't been set up yet.

Run the Factory CEO in **focus mode** on the current project:

```bash
factory ceo "$(pwd)" --focus "$ARGUMENTS"
```

This will:

1. **Study** the codebase and generate observations
2. **Strategize** a single targeted hypothesis for "$ARGUMENTS"
3. **Build** the change on a feature branch with a PR
4. **Review** the change against guard rules and scope constraints
5. **Evaluate** before/after scores to verify improvement
6. **Keep or revert** based on eval results and precheck gates
7. **Archive** learnings for future cycles

## If the CLI is not available

If `factory` cannot be installed (e.g. no `uv` available), tell the user:

```
To use /factory:implement, install the Factory CLI:

  uv tool install /path/to/remote-factory
  # or from git
  uv tool install git+https://github.com/akashgit/remote-factory

Then re-run this command.
```
