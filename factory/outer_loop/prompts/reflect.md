# Contrastive Reflection Prompt

## Context

You are analyzing generation {generation} of an evolutionary workflow search.
The search is optimizing workflow DAGs against benchmarks.

## Top-K Performers (Winners)

{top_k_data}

## Bottom-K Performers (Losers)

{bottom_k_data}

## Task

Compare the winners and losers. Identify:

1. **Failure patterns**: What went wrong in the losers? Which agents failed? What errors occurred?
2. **Success patterns**: What did the winners do right? Which agent sequences led to success?
3. **Structural differences**: How do the DAG topologies differ between winners and losers?
4. **Mutation suggestions**: What specific changes (add/remove nodes, redirect edges, change params) would improve the losers?

## Output Format

```json
{
  "failure_patterns": ["..."],
  "success_patterns": ["..."],
  "mutation_suggestions": ["NODE_INSERT: ...", "PARAM_MUTATE: ...", "KNOB_MUTATE: ..."],
  "prompt_improvements": ["..."],
  "structural_recommendations": ["..."]
}
```
