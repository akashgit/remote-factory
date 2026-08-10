# AutomationBench-Sales Workflow

10-node iterative pipeline for fine-tuning small LLMs on Zapier's Sales domain.

## Graph

```
research → data_prep → gate_data → train → gate_train → serve → gate_serve → eval_bench → verdict_gate
                ^                                                                              |
                |________________________________RELOOP (improving / not yet plateaued)________|
                                                                                               |
                                                                            archivist <--PROCEED-|
                                                                           (non-blocking)
```

- **research**: Analyzes Sales domain tasks, failure modes, and fine-tuning best practices
- **data_prep**: Extracts and formats training data as chat-ml JSONL
- **gate_data**: Validates >=50 training examples with valid JSON
- **train**: Runs LoRA/QLoRA fine-tuning on a remote GPU server via SSH
- **gate_train**: Checks adapter checkpoint and training log for errors
- **serve**: Deploys fine-tuned model as OpenAI-compatible API via vLLM/ollama
- **gate_serve**: HTTP health check on the model endpoint
- **eval_bench**: Runs auto-bench CLI locally against the served model
- **verdict_gate**: Plateau detection — RELOOP if improving, PROCEED after 3 consecutive no-improvement cycles
- **archivist**: Archives experiment results (non-blocking)

## Usage

```bash
factory ceo /path/to/project --mode automationbench-sales
```
