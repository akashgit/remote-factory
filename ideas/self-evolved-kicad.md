# Self-Evolved KiCad: Autonomous Improvement of Open-Source PCB Design

## Vision

Treat PCB design quality as a measurable optimization target and build an automated loop that iteratively improves it. Inspired by "Autonomous Evolution of EDA Tools: Multi-Agent Self-Evolved ABC" (Yu & Ren, DAC 2026) — not their specific multi-agent architecture, but their problem framing: define correctness gates, define QoR metrics, run a closed-loop that proposes changes, verifies correctness, measures quality, and keeps only what improves the score.

We apply this framing to KiCad, the dominant open-source PCB EDA suite.

## What We Take from the ABC Paper

### The Problem Framing (keep this)

The paper's core contribution is showing that EDA tool improvement can be treated as an automated optimization problem when you have:

1. **A correctness gate** — a fast, automated check that rejects any change that breaks semantics (ABC used formal equivalence checking)
2. **A QoR benchmark suite** — a diverse set of real designs with measurable quality metrics
3. **A composite score** — a single number that captures multi-dimensional quality (area, timing, depth)
4. **A closed feedback loop** — propose change → verify correctness → measure QoR → keep/revert

This framing is tool-agnostic. It works whether the "change" is a C source patch, a Python script, a configuration tweak, or a plugin.

### The Specific Approach (rethink this)

The paper modifies ABC's C source code directly — 3 agents editing non-overlapping directories, compiling, running CEC. This works for ABC because:
- ABC is pure C with a simple build system
- Formal equivalence checking gives a hard correctness guarantee
- Logic synthesis operates on well-defined graph structures (AIGs)

KiCad is different: C++ with heavy GUI dependencies, 20-minute builds, no formal equivalence notion for physical layout. Directly patching KiCad's C++ source is high-friction and fragile. The implementation approach should fit the tool, not be copied from the paper.

## KiCad: Why It's a Good Target

- **Open source (GPL v3)**, 1M+ lines of C++, backed by CERN and Raspberry Pi Foundation
- **Clear optimization targets** — routing, placement, and DRC produce measurable quality metrics
- **Biggest user pain point is autorouting** — removed before KiCad 5.x, users rely on clunky FreeRouting export/import
- **Python IPC API (KiCad 9+)** — `kicad-python` package enables programmatic board manipulation
- **S-expression file formats** — human-readable `.kicad_pcb` files, easy to parse and diff
- **Plugin system** — action plugins can manipulate board geometry, components, nets from Python
- **Rich existing benchmarks** — thousands of open-source KiCad projects on GitHub/GitLab

## The Evaluation Framework

This is the most important piece — get this right and the implementation approach becomes flexible.

### Correctness Gate

The analog to ABC's formal equivalence checking. Every proposed change must pass:

| Check | What It Verifies | How |
|-------|-----------------|-----|
| **Netlist integrity** | Routed board connectivity matches schematic | KiCad DRC netlist check |
| **DRC clean** | No new design rule violations | KiCad DRC engine (clearance, width, annular ring) |
| **Manufacturing compliance** | All fab constraints met | Custom rules via `.kicad_dru` |
| **No component drift** | Components remain at legal positions | Footprint boundary + courtyard checks |

These are all automatable through KiCad's CLI and Python API. A change that fails any gate is rejected immediately — no QoR evaluation needed.

### Benchmark Suite

Curate open-source KiCad designs spanning complexity:

| Tier | Characteristics | Examples |
|------|----------------|----------|
| **Simple** | 2-layer, <50 components | Arduino shields, breakout boards, sensor modules |
| **Medium** | 4-layer, 50-200 components | STM32 dev boards, sensor hubs, USB interfaces |
| **Complex** | 6+ layer, 200+ components | RPi CM4 carriers, SDR receivers, motor controllers |
| **Stress** | Dense routing challenges | BGA fanout, DDR memory buses, high-pin-count FPGAs |

### QoR Metrics

| Metric | Direction | Weight | Rationale |
|--------|-----------|--------|-----------|
| Route completion (%) | higher = better | high | Unusable if nets are unrouted |
| Total wire length (normalized) | lower = better | medium | Shorter traces = less delay, less crosstalk |
| Via count | lower = better | medium | Fewer vias = better signal integrity, lower fab cost |
| DRC violations | must be zero | gate | Binary pass/fail, not scored |
| Diff-pair skew (mils) | lower = better | medium | Critical for high-speed interfaces |
| Routing time (seconds) | lower = better | low | Efficiency matters but secondary to quality |

Composite score: weighted geometric mean, normalized against baseline KiCad on same designs.

### Baseline Comparisons

- KiCad interactive router (manual-equivalent baseline)
- FreeRouting (current best external autorouter for KiCad)
- Any commercial tool results available for same designs

## Implementation Approach (Open)

The evaluation framework above is the fixed scaffold. The implementation — how changes are actually proposed and applied — is deliberately left open. Several approaches are worth exploring, possibly in combination:

### Option A: Evolve Python Scripts/Plugins

Build PCB optimization as KiCad action plugins or standalone scripts using the `kicad-python` IPC API. The agent proposes and iterates on Python code that manipulates board layout programmatically.

- **Pro**: Fast iteration (no C++ compilation), easy to experiment, leverages KiCad's existing plugin ecosystem
- **Con**: Limited by what the Python API exposes; can't change core router behavior
- **Best for**: Placement optimization, post-route cleanup, design rule generation

### Option B: Evolve FreeRouting Configuration/Heuristics

FreeRouting is Java-based and more tractable than KiCad's C++. Evolve its routing parameters, cost functions, or even its Java source.

- **Pro**: Directly targets the routing problem; FreeRouting is simpler (~50K lines Java)
- **Con**: Indirect — requires DSN export/import round-trip; FreeRouting development is sporadic
- **Best for**: Routing quality improvements specifically

### Option C: Build a Standalone Optimizer

Write a purpose-built placement/routing optimizer that reads `.kicad_pcb` files, optimizes, and writes them back. Evolve this optimizer's algorithms.

- **Pro**: Full control, no dependency on KiCad's plugin API limitations, fast iteration
- **Con**: Duplicates effort; must handle KiCad's file format correctly
- **Best for**: Exploring novel algorithms (RL-based routing, simulated annealing placement)

### Option D: Modify KiCad Source (ABC-style)

Directly evolve KiCad's C++ router/placer code.

- **Pro**: Highest potential impact — changes the tool itself
- **Con**: Slow builds, complex C++ codebase, hard to isolate changes
- **Best for**: If we find specific bottlenecks in the router's cost functions or heuristics

## Phased Plan

### Phase 1: Evaluation Harness (weeks 1-3)

Build the benchmark and scoring infrastructure first — this is useful regardless of which implementation approach we choose.

- Curate benchmark suite (10-15 designs across tiers)
- Build headless evaluation pipeline: load board → run optimization → DRC check → score
- Establish baseline QoR numbers for stock KiCad and FreeRouting
- Package as a reproducible scoring tool (Docker or similar)

### Phase 2: First Optimization Loop (weeks 4-7)

Pick the lowest-friction implementation approach (likely Option A or C) and close the loop.

- Get one end-to-end cycle working: propose change → apply → verify → score → keep/revert
- Focus on a single sub-problem first (e.g., component placement on simple boards)
- Run 10-20 iterations, verify the loop finds real improvements
- Instrument to understand what the agent discovers

### Phase 3: Scale and Compare (weeks 8-12)

- Expand to harder benchmarks and more complex sub-problems
- Try alternative implementation approaches if Phase 2 hits limits
- Run extended evolution (50+ cycles)
- Compare against baselines, analyze cost-effectiveness
- Document what types of improvements are discoverable vs. not

## Success Criteria

- **Minimum**: Working evaluation harness that produces reproducible QoR scores across the benchmark suite
- **Target**: Measurable QoR improvement (3%+ composite) over baseline on medium-complexity boards
- **Stretch**: Results competitive with or exceeding FreeRouting on routing quality

## Risks

- **KiCad Python API coverage**: The IPC API is new (KiCad 9) and may not expose everything needed. Mitigation: prototype early, fall back to file-based manipulation.
- **Benchmark representativeness**: Open-source designs may not represent real production complexity. Mitigation: include stress-test boards designed to be hard.
- **Correctness gate gaps**: DRC may not catch all regressions (e.g., subtle signal integrity issues). Mitigation: add custom checks for high-speed design rules.
- **Evaluation cost**: Each scoring run must route entire boards. Mitigation: start with small boards, parallelize.

## References

- Yu & Ren, "Autonomous Evolution of EDA Tools: Multi-Agent Self-Evolved ABC", DAC 2026 (arXiv:2604.15082)
- KiCad project: https://gitlab.com/kicad/code/kicad
- kicad-python IPC API: https://pypi.org/project/kicad-python/
- FreeRouting: https://github.com/freerouting/freerouting
- DeepPCB (RL-based routing): https://deeppcb.ai/
