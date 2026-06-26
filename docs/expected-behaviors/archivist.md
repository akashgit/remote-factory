# Expected Behavior: Archivist

## Identity
The Archivist is the institutional memory keeper. It records experiment outcomes as dual-format notes (markdown + JSON sidecar), maintains cross-cycle CEO memory, proposes playbook improvements, and regenerates the performance report. It writes ONLY to `.factory/archive/` and never modifies source code.

## Expected Behaviors (Invariants)
These MUST hold regardless of which workflow the agent is in. Check these against the agent's trace.

### Experiment Notes (Dual Output)
- [ ] Produces BOTH markdown (`.factory/archive/experiments/{project}-{NNN}.md`) AND JSON sidecar (`.factory/archive/experiments/{NNN}.json`) for every experiment
- [ ] Markdown has frontmatter: tags, project, experiment_id, verdict, score_delta, date, source
- [ ] JSON sidecar is valid JSON (`json.loads()` succeeds — no trailing commas, proper escaping)
- [ ] `dimensions_changed` includes only dimensions where score moved >= 0.05
- [ ] `learned` field is exactly one sentence
- [ ] `anti_patterns` is a list (empty list if none — not omitted)
- [ ] `playbook_proposals` only present for high-impact experiments (score_delta >= 0.03 or clear pattern); empty list otherwise

### CEO Memory (`memory.json`)
- [ ] Reads existing `.factory/archive/memory.json` before appending (creates with `[]` if missing)
- [ ] New entries have >= 2 experiments as evidence
- [ ] Checks for duplicates before adding
- [ ] Keeps array under 50 entries — evicts oldest/weakest if over

### Report Regeneration
- [ ] Runs `factory report-update "$PROJECT_PATH"` after every archival (`Bash` call visible in trace)

### Write Boundaries
- [ ] All `Write` tool calls target paths under `.factory/archive/` — no writes elsewhere
- [ ] Does NOT modify source code, `eval/score.py`, `.factory/strategy/`, or `.factory/reviews/`

## Inputs & Outputs
- **Reads:** experiment verdicts, `.factory/reviews/builder-latest.md`, `.factory/reviews/qa-latest.md`, `.factory/archive/memory.json`, `.factory/strategy/current.md`
- **Writes:** `.factory/archive/experiments/{project}-{NNN}.md`, `.factory/archive/experiments/{NNN}.json`, `.factory/archive/memory.json`, `.factory/archive/patterns/patterns.md`, `.factory/archive/sources/*.md`, performance report (via `factory report-update`)
- **Spawned by:** CEO (`factory agent archivist --model haiku`)
- **Hands off to:** nobody — Archivist is always the last agent in any workflow phase

## Forbidden Actions
- Write to any directory outside `.factory/archive/`
- Produce only markdown OR only JSON for experiment notes (both are mandatory)
- Add `memory.json` entries with fewer than 2 experiments as evidence
- Let `memory.json` exceed 50 entries without eviction
- Include `playbook_proposals` for low-impact experiments (score_delta < 0.03, no clear pattern)
- Skip `factory report-update` after writing archive notes
- Fall back to user's personal Obsidian vault when `$FACTORY_VAULT_PATH` is unset — use `.factory/` instead
- Produce invalid JSON (trailing commas, unescaped quotes)

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| `.factory/archive/experiments/{NNN}.json` missing after archival | Missing JSON sidecar |
| `json.loads()` throws on JSON sidecar | Invalid JSON |
| `memory.json` array length > 50 | memory.json overflow — no eviction |
| Near-identical `text` fields in `memory.json` | Duplicate memory entries |
| No `factory report-update` `Bash` call in trace | Skipped report regeneration |
| `Write` calls target paths outside `.factory/archive/` | Write boundary violation |
| No `&` in spawn command during mid-cycle archival | Blocking when should be async |

## Playbook Rules
- **DO [arch-00001]:** Record at all checkpoints — archival compliance is non-negotiable
- **DON'T [arch-00002]:** Don't fall back to user's personal Obsidian vault when `$FACTORY_VAULT_PATH` is unset — use `.factory/` instead
