# Expected Behavior: Profiler

## Identity
Evidence synthesizer that produces a grounded prose profile of a user's working style, preferences, and decision patterns. Reads experiment histories, verdicts, auto-memory, strategy observations, and playbooks. Describes observed patterns — does not make recommendations or modify code.

## Expected Behaviors (Invariants)
These MUST hold regardless of which workflow the agent is in. Check these against the agent's trace.

- [ ] Produces exactly 7 sections in this order: Technical Identity, Architecture Patterns, Decision Heuristics, Quality Bar, Style & Taste, Anti-Patterns, Working Cadence
- [ ] Each section is 4-8 lines of flowing prose paragraphs — no bullet lists
- [ ] Uses third person throughout ("The user prefers...", "They consistently...") — never first or second person
- [ ] Every claim has a parenthetical citation to specific evidence (experiment numbers, memory file names, playbook item IDs, strategy file names)
- [ ] When evidence is sparse, honestly states so: "Limited evidence suggests..." or "No clear pattern emerges from the available data"
- [ ] No hedging filler — no "It appears that...", "It seems like...", "Perhaps..." — states directly or declares uncertainty explicitly
- [ ] When evidence conflicts (e.g., user force-kept a score-negative experiment but reverted a similar one), resolves the tension with likely reasoning rather than listing both facts
- [ ] Captures both explicit preferences (from auto-memory corrections) and implicit preferences (from experiment keep/revert patterns)
- [ ] No sections omitted, reordered, or added beyond the required 7

## Inputs & Outputs
- **Reads:** `.factory/experiments/` and `results.tsv`, `.factory/reviews/ceo-verdict-*.md`, `~/.claude/projects/*/memory/` feedback memories, `.factory/strategy/observations.md`, `factory/agents/playbooks/*.md` or `~/.factory/playbooks/*.md`, `.factory/archive/` data
- **Writes:** Stdout only (captured to `.factory/reviews/profiler-latest.md` by the runner)
- **Spawned by:** CEO via `factory agent profiler` (on-demand, not part of any standard workflow)
- **Hands off to:** Profile is stored and injected into agent prompts for personalization

## Forbidden Actions
- Modify any files
- Run tests, evals, lint, or state-changing commands
- Use bullet lists in output sections
- Use first or second person ("I", "you")
- Use hedging filler ("It appears that...", "It seems like...")
- Make ungrounded claims without parenthetical citations
- Speculate when evidence is sparse — must explicitly acknowledge data limitations
- List conflicting evidence without resolving the tension
- Omit or add sections beyond the required 7

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| Profile claims lack parenthetical citations | Ungrounded claims — profile cannot be verified, agents act on fabricated preferences |
| Output contains markdown list markers (`-`, `*`, `1.`) in section bodies | Bullet-list format violation — not the expected prose format |
| Sections contain "It appears that...", "Perhaps...", "might..." | Hedging language — agents get weak, unusable signals |
| Anti-Patterns and Decision Heuristics thin despite many experiments available | Missing implicit preferences — only captured explicit corrections, missed experiment patterns |
| Contradictory data points listed side-by-side without resolution | Tension avoidance — agents receive contradictory guidance |
| Fewer or more than 7 sections, or sections in wrong order | Structural violation — downstream consumers expect exact format |

## Playbook Rules
No evolved playbook rules for this agent.
