# Contributing

## Development Setup

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync --all-groups    # Install all deps including dev
```

## Running Tests

```bash
uv run pytest -v                  # Full suite (1350+ tests)
uv run pytest tests/test_cli.py   # Single file
uv run pytest -k "test_detect"    # By name pattern
uv run pytest --cov               # With coverage
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` — async test functions run without the `@pytest.mark.asyncio` decorator.

Shared fixtures (`tmp_project`, `sample_config`, `python_project`) live in `tests/conftest.py`. An autouse `_isolate_registry` fixture redirects the global registry to a temp directory during tests.

## Linting and Type Checking

```bash
uv run ruff check .               # Lint
uv run ruff check --fix .         # Lint with autofix
uv run mypy factory/              # Type check
```

## Code Style

- **Python 3.11+** — use `X | Y` unions, not `Union[X, Y]`
- **snake_case** everywhere
- **100 char** line length (enforced by ruff)
- **Pydantic v2** — all models use `ConfigDict(strict=True, extra="forbid")`
- **Async/await** by default — library functions in `store.py` and `eval/runner.py` are async
- **Structured logging** via `structlog` — use `log = structlog.get_logger()` at module level
- **No comments** unless the "why" is non-obvious

## PR Workflow

1. Create a feature branch from `main`
2. Make your changes
3. Run tests and lint: `uv run pytest -v && uv run ruff check .`
4. Push and open a PR
5. PRs require 1 approving review (branch protection is enabled)
6. Linear history enforced — no merge commits

## Contribution Ideas

We welcome contributions at all levels. Here are some ideas to get started.

**Use the factory to build your contribution.** Write your idea as a `factory.md` goal, point the factory at the repo, and let it do the implementation work. Every idea below can be expressed as a one-line goal — the factory will hypothesize, build, test, and iterate:

```bash
# Fork the repo, then:
factory ceo ~/remote-factory --focus "shell completions for the factory CLI"
```

### Good First Issues

| Idea | Description |
|------|-------------|
| **Better error messages** | Improve CLI error output when prerequisites are missing (no Claude Code, no API key, etc.) |
| **`factory status` enrichment** | Add last experiment date, current score, and active branch to the status output |
| **Eval dimension: security** | Add a hygiene dimension that runs `bandit` (Python) or `npm audit` (JS) |
| **Markdown export** | `factory history --format md` to export experiment history as a markdown table |
| **Shell completions** | Add bash/zsh/fish completions for the `factory` CLI |

### Medium Complexity

| Idea | Description |
|------|-------------|
| **GSD mode** | A "Get Shit Done" workflow that skips or streamlines the Researcher phase — go straight from observations to Builder for speed-focused iterations |
| **Notifications (Telegram, Slack, etc.)** | Real-time push notifications on keep/revert decisions, cycle completions, and score regressions. A basic `TelegramNotifier` skeleton exists in `factory/notify/telegram.py` but isn't wired into the CEO loop — needs proper integration and multi-provider support |
| **Parallel experiments** | Run multiple hypotheses concurrently on separate branches, evaluate in parallel |
| **GitHub Actions integration** | Run the factory as a GitHub Action on push/PR events |
| **Custom agent roles** | Allow users to define new specialist agents beyond the built-in roles |
| **Dashboard auth** | Add basic authentication to the live dashboard for shared deployments |

### Hard / Research

| Idea | Description |
|------|-------------|
| **Multi-backend support** | Extend the factory to work with other AI code agents — [Codex](https://openai.com/index/codex/), [Jules](https://jules.google.com/), [Amp](https://ampcode.com/), or any agent that accepts a prompt and produces code changes |
| **Distributed execution** | Run specialist agents across multiple machines with a message queue (Redis, NATS) instead of local subprocesses |
| **Learning-to-search** | Use experiment history to train a lightweight model that predicts which hypothesis categories will succeed for a given project state |
| **Multi-project orchestration** | A meta-CEO that manages a portfolio of projects, allocating factory cycles based on expected improvement |
| **Formal verification integration** | Add eval dimensions that use property-based testing (Hypothesis) or formal methods to verify invariants |

If you're interested in any of these, open an issue to discuss the approach before starting.

## Project Structure

```
factory/
├── cli.py                  # CLI entry point (argparse subcommands)
├── models.py               # Pydantic v2 domain models
├── state.py                # Project state detection
├── store.py                # .factory/ filesystem store
├── events.py               # Event system (JSONL log)
├── strategy.py             # FEEC priority heuristic
├── study.py                # Code analysis + observations
├── insights.py             # Cross-project patterns
├── checkpoint.py            # CEO checkpoint save/load (legacy, debugging)
├── analysis.py             # Experiment comparison
├── agents/
│   ├── runner.py           # Agent subprocess spawner
│   ├── prompts/            # Agent role prompts (8 specialists + CEO)
│   └── playbooks/          # ACE-evolved playbooks
├── registry.py             # Global project registry
├── report.py               # Performance report generation
├── ace/                    # Autonomous Context Engineering
├── dashboard/              # FastAPI live dashboard
├── discovery/              # Project introspection
├── eval/                   # Three-tier eval system
└── notify/                 # Telegram notifications

tests/                      # 1350+ tests mirroring factory/ structure
```

## Adding a New CLI Command

1. Add `cmd_<name>` function in `factory/cli.py`
2. Register it in the `COMMANDS` handler dict
3. Add argument parsing in the `build_parser` function
4. Write tests in `tests/test_cli.py`

## Adding a New Agent Role

1. Create prompt at `factory/agents/prompts/<role>.md`
2. Add the role to `AgentRole` literal type in `factory/agents/runner.py`
3. Update the CEO prompt to spawn the new agent at the right point in the workflow
4. Write tests in `tests/test_agents.py`
