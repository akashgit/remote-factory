# Design: `--model` flag for factory CLI

## Problem

The factory spawns `claude` subprocesses without a `--model` flag, so all agents use Claude Code's default model (Sonnet) regardless of the user's preferred model. Users who want Opus or a specific model version have no way to control this.

## Solution

Add `--model` CLI flag and `FACTORY_MODEL` env var support. Resolution order: **CLI flag > env var > omitted** (claude CLI picks its own default).

## Changes

### `factory/agents/runner.py`

- `invoke_agent()`: add `model: str | None = None` parameter. If set, append `["--model", model]` to the `cmd` list before subprocess execution.
- `invoke_agents_parallel()`: add `model: str | None = None` parameter, pass through to each `invoke_agent()` call.

### `factory/cli.py`

- Add `_resolve_model(args) -> str | None` helper: returns `args.model` if set, else `os.environ.get("FACTORY_MODEL")`, else `None`.
- `cmd_ceo()`: resolve model. For interactive mode, add `["--model", model]` to the exec cmd. For headless mode, pass `model=` to `invoke_agent()`.
- `cmd_run()`: resolve model, pass through to `_run_single_cycle()`.
- `_run_single_cycle()`: add `model: str | None = None` parameter, pass to `invoke_agent()`.
- `cmd_agent()`: resolve model, pass to `invoke_agent()`.
- `cmd_tmux()`: resolve model, include in the tmux'd command string.
- `_chain_modes()`: add `model: str | None = None` parameter, pass through to `_run_single_cycle()`.

### Argparse

Add `--model` argument to `ceo`, `run`, `agent`, and `tmux` subparsers:

```python
p.add_argument("--model", default=None,
               help="Claude model to use for agent subprocesses (default: FACTORY_MODEL env var, "
                    "or claude CLI default)")
```

### Tests

- Verify `invoke_agent` includes `--model` in subprocess command when model is set.
- Verify `invoke_agent` omits `--model` when model is `None`.
- Verify `_resolve_model` precedence: flag > env var > None.

## Out of scope

- No changes to `factory/models.py`, `factory/store.py`, or agent prompts.
- No model validation — invalid model names are passed through and claude CLI will report the error.
- No per-agent model overrides (all agents in a run use the same model).
