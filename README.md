<p align="center">
  <img src="https://raw.githubusercontent.com/akashgit/remote-factory/main/docs/assets/refactory_logo.png" alt="re:factory" width="480">
</p>

<h1 align="center">re:factory</h1>

<p align="center">
  <b>A harness for agentic software evolution — detect, delegate, evaluate, archive</b>
</p>

<p align="center">
  <a href="https://github.com/akashgit/remote-factory/actions/workflows/ci.yml"><img src="https://github.com/akashgit/remote-factory/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/akashgit/remote-factory"><img src="https://codecov.io/gh/akashgit/remote-factory/graph/badge.svg" alt="codecov"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
  <a href="https://github.com/akashgit/remote-factory/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT"></a>
  <a href="https://akashgit.github.io/remote-factory/"><img src="https://img.shields.io/badge/docs-akashgit.github.io-blue" alt="Docs"></a>
</p>

---

**Describe what you want — re:factory designs and builds it.** Brainstorm an idea from scratch, refine a plan for an existing project, or create entirely new factory modes. A CEO agent orchestrates eight specialists — Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, Refiner, and Failure Analyst — each running as an independent Claude Code subprocess.

## Install

```bash
pip install remote-factory
# or
uv pip install remote-factory
```

**Development install:**

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
```

## Quick Start

**Prerequisites:** Python 3.11+, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated.

```bash
# Design — brainstorm an idea, refine it, then build
factory ceo "distributed eval runner" --mode design

# Build — have a fleshed-out idea? Pass the file
factory ceo ~/ideas/weather-dashboard.md

# Improve — point it at any codebase
factory ceo ~/my-project

# Focus — build exactly one thing
factory ceo ~/my-project --focus "add WebSocket support"

# See all commands
factory --help
```

## Documentation

Full documentation is available at **[akashgit.github.io/remote-factory](https://akashgit.github.io/remote-factory/)**.

| Guide | Description |
|-------|-------------|
| [Getting Started](https://akashgit.github.io/remote-factory/docs/getting-started) | Lifecycle walkthrough, research mode, factory.md config |
| [Setup](https://akashgit.github.io/remote-factory/docs/setup) | Installation, authentication, environment variables |
| [Architecture](https://akashgit.github.io/remote-factory/docs/concepts/architecture) | Three-layer system, agent roles, state machine |
| [Configuration](https://akashgit.github.io/remote-factory/docs/configuration) | `factory.md` reference — all sections and options |
| [Contributing](https://akashgit.github.io/remote-factory/docs/contributing) | Dev setup, code style, testing, PR workflow |

## License

[MIT](https://github.com/akashgit/remote-factory/blob/main/LICENSE) — Akash Srivastava
