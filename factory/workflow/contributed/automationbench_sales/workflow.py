"""AutomationBench-Sales workflow — fine-tune small LLMs on Zapier's Sales domain.

10-node pipeline with convergence loop:
  research → data_prep → gate_data → train → gate_train →
  serve → gate_serve → eval_bench → verdict_gate → archivist

RELOOP from verdict_gate back to data_prep when score is still improving.
PROCEED from verdict_gate to archivist when plateaued (3 consecutive no-improvement).

Designed for remote GPU training via SSH (agent-driven) with local evaluation
via the auto-bench CLI.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "automationbench-sales",
    "description": (
        "AutomationBench-Sales fine-tuning mode — 10-node iterative pipeline "
        "for fine-tuning small LLMs on Zapier's Sales domain. "
        "research → data_prep → train → serve → eval → verdict loop "
        "with plateau detection. Remote GPU training via SSH, local eval "
        "via auto-bench CLI."
    ),
}


def workflow() -> Workflow:
    """Build the AutomationBench-Sales workflow."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Research ──────────────────────────────────────────
    nodes["research"] = AgentNode(
        id="research",
        role=AgentRole.RESEARCHER,
        timeout=900,
        prompt_template=(
            "You are researching the AutomationBench Sales domain and fine-tuning "
            "best practices for small (<10B parameter) LLMs.\n\n"
            "## Tasks\n\n"
            "1. **Clone AutomationBench** — If not already present, clone the "
            "AutomationBench repository. Locate the Sales domain task definitions.\n\n"
            "2. **Analyze Sales tasks** — Read task definitions, tool schemas, and "
            "assertion patterns for the Sales domain. Identify:\n"
            "   - What tools the model needs to call (CRM, email, calendar, etc.)\n"
            "   - What input/output formats are expected\n"
            "   - Common assertion patterns (field matching, sequence matching)\n"
            "   - How partial_credit scoring works\n\n"
            "3. **Identify failure modes** — What do small models (<10B) typically "
            "get wrong on tool-use tasks? Common issues:\n"
            "   - Wrong tool selection\n"
            "   - Missing required parameters\n"
            "   - Incorrect parameter formatting\n"
            "   - Failing to chain multi-step tool calls\n\n"
            "4. **Fine-tuning best practices** — WebSearch for:\n"
            "   - LoRA/QLoRA guides for tool-use fine-tuning\n"
            "   - Chat-ML and ShareGPT formatting for tool-call training data\n"
            "   - Recommended hyperparameters for 3B-8B models\n"
            "   - System prompt templates for tool-use models\n\n"
            "5. **Read prior learnings** — Check .factory/archive/ for any prior "
            "fine-tuning experiment notes.\n\n"
            "## Output\n\n"
            "Write a structured research report to .factory/strategy/research.md "
            "covering all findings above. Include specific recommendations for "
            "training data format, base model selection, and LoRA configuration.\n"
        ),
        writes={".factory/strategy/research.md"},
    )

    # ── Node 2: Data Prep ─────────────────────────────────────────
    nodes["data_prep"] = AgentNode(
        id="data_prep",
        role=AgentRole.BUILDER,
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "You are preparing training data for fine-tuning a small LLM on "
            "AutomationBench Sales tasks.\n\n"
            "## Context\n\n"
            "Read the research report at .factory/strategy/research.md for domain "
            "analysis and formatting recommendations.\n\n"
            "## Tasks\n\n"
            "1. **Read Sales task definitions** from the AutomationBench repo\n"
            "2. **Extract training examples** — Convert each task into chat-ml "
            "format with:\n"
            "   - System prompt (role, available tools, constraints)\n"
            "   - User message (task description)\n"
            "   - Assistant response (correct tool calls and reasoning)\n"
            "3. **Generate synthetic examples** — Create variations:\n"
            "   - Paraphrase user instructions\n"
            "   - Vary tool parameter values\n"
            "   - Add multi-step reasoning chains\n"
            "   - Include negative examples (wrong tool → correction)\n"
            "4. **Format as JSONL** — Each line is a complete conversation:\n"
            "   ```json\n"
            '   {"messages": [{"role": "system", "content": "..."}, '
            '{"role": "user", "content": "..."}, '
            '{"role": "assistant", "content": "..."}]}\n'
            "   ```\n"
            "5. **Split train/val** — 90/10 split, stratified by task type\n"
            "6. **Write dataset card** — Stats, format description, examples\n\n"
            "## Output\n\n"
            "- .factory/training-data/sales-train.jsonl\n"
            "- .factory/training-data/sales-val.jsonl\n"
            "- .factory/training-data/README.md\n\n"
            "## Rules\n\n"
            "- Minimum 50 training examples (more is better)\n"
            "- Valid JSON on every line\n"
            "- No duplicate examples\n"
            "- Include tool schemas in system prompts\n"
        ),
        reads={".factory/strategy/research.md"},
        writes={
            ".factory/training-data/sales-train.jsonl",
            ".factory/training-data/sales-val.jsonl",
            ".factory/training-data/README.md",
        },
    )

    # ── Node 3: Gate Data ─────────────────────────────────────────
    nodes["gate_data"] = GateNode(
        id="gate_data",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "if [ ! -f .factory/training-data/sales-train.jsonl ]; then "
            "echo 'fail: training data file not found'; exit 0; fi && "
            "LINES=$(wc -l < .factory/training-data/sales-train.jsonl) && "
            "if [ \"$LINES\" -lt 50 ]; then "
            "echo \"fail: only $LINES training examples (need >=50)\"; exit 0; fi && "
            "INVALID=$(python3 -c \""
            "import json, sys; "
            "bad=0; "
            "for line in open('.factory/training-data/sales-train.jsonl'): "
            "    try: json.loads(line); "
            "    except: bad+=1; "
            "print(bad)\" 2>/dev/null || echo 999) && "
            "if [ \"$INVALID\" -gt 0 ]; then "
            "echo \"fail: $INVALID invalid JSON lines in training data\"; exit 0; fi && "
            "echo \"pass: $LINES valid training examples\""
        ),
        reads={".factory/training-data/sales-train.jsonl"},
    )

    # ── Node 4: Train ─────────────────────────────────────────────
    nodes["train"] = AgentNode(
        id="train",
        role=AgentRole.BUILDER,
        timeout=3600,
        max_iterations=2,
        prompt_template=(
            "You are running LoRA/QLoRA fine-tuning on a remote GPU server.\n\n"
            "## Context\n\n"
            "- Read factory.md for remote server config (host, user, ssh_key)\n"
            "- Read .factory/strategy/research.md for hyperparameter recommendations\n"
            "- Training data is at .factory/training-data/sales-train.jsonl\n"
            "- Validation data is at .factory/training-data/sales-val.jsonl\n\n"
            "## Tasks\n\n"
            "1. **SSH to remote server** — Use credentials from factory.md:\n"
            "   ```bash\n"
            "   ssh -o StrictHostKeyChecking=no <user>@<host>\n"
            "   ```\n\n"
            "2. **Check GPU availability** — Run `nvidia-smi` to verify GPU access. "
            "If no GPU is available, report and halt.\n\n"
            "3. **Set up environment** — Create or activate a conda env with:\n"
            "   transformers, peft, bitsandbytes, datasets, accelerate, trl\n\n"
            "4. **Transfer training data** — Use scp or rsync:\n"
            "   ```bash\n"
            "   rsync -avz .factory/training-data/ <user>@<host>:~/abs-training/data/\n"
            "   ```\n\n"
            "5. **Run fine-tuning** — Create and execute a training script:\n"
            "   - Base model from factory.md (default: meta-llama/Llama-3.2-3B-Instruct)\n"
            "   - LoRA rank=8, alpha=16 (or from factory.md config)\n"
            "   - Learning rate 2e-4, batch size 4\n"
            "   - 1-3 epochs (from factory.md or default 2)\n"
            "   - Save checkpoints to ~/abs-training/checkpoints/\n\n"
            "6. **Pull results back** — Download adapter files and training log:\n"
            "   ```bash\n"
            "   TIMESTAMP=$(date +%Y%m%d_%H%M%S)\n"
            "   mkdir -p .factory/models/$TIMESTAMP\n"
            "   rsync -avz <user>@<host>:~/abs-training/checkpoints/latest/ "
            ".factory/models/$TIMESTAMP/\n"
            "   ```\n\n"
            "## Output\n\n"
            "- .factory/models/<timestamp>/adapter_config.json\n"
            "- .factory/models/<timestamp>/adapter_model.safetensors\n"
            "- .factory/models/<timestamp>/training_log.txt\n\n"
            "## Rules\n\n"
            "- Run training in a tmux session (detachable) on the remote server\n"
            "- Monitor for CUDA OOM — if it occurs, reduce batch size and retry\n"
            "- Training must complete (loss should decrease across steps)\n"
            "- Never store SSH keys in code or logs\n"
        ),
        reads={
            ".factory/training-data/sales-train.jsonl",
            ".factory/training-data/sales-val.jsonl",
            ".factory/strategy/research.md",
        },
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 5: Gate Train ────────────────────────────────────────
    nodes["gate_train"] = GateNode(
        id="gate_train",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "LATEST=$(ls -td .factory/models/*/ 2>/dev/null | head -1) && "
            "if [ -z \"$LATEST\" ]; then "
            "echo 'fail: no model checkpoint directory found'; exit 0; fi && "
            "if [ ! -f \"${LATEST}adapter_config.json\" ]; then "
            "echo 'fail: adapter_config.json not found in checkpoint'; exit 0; fi && "
            "if [ ! -f \"${LATEST}training_log.txt\" ]; then "
            "echo 'reloop: training_log.txt missing — cannot verify loss decrease'; "
            "exit 0; fi && "
            "CUDA_OOM=$(grep -ci 'out of memory\\|CUDA OOM\\|RuntimeError.*CUDA' "
            "\"${LATEST}training_log.txt\" 2>/dev/null || echo 0) && "
            "if [ \"$CUDA_OOM\" -gt 0 ]; then "
            "echo 'reloop: CUDA OOM errors detected in training log'; exit 0; fi && "
            "echo 'pass: adapter checkpoint exists and training log looks clean'"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Node 6: Serve ─────────────────────────────────────────────
    nodes["serve"] = AgentNode(
        id="serve",
        role=AgentRole.BUILDER,
        timeout=900,
        prompt_template=(
            "You are deploying the fine-tuned model as an OpenAI-compatible API "
            "on the remote GPU server.\n\n"
            "## Context\n\n"
            "- Read factory.md for remote server config\n"
            "- The latest adapter checkpoint is in .factory/models/ (most recent dir)\n"
            "- Base model name from factory.md config\n\n"
            "## Tasks\n\n"
            "1. **SSH to remote server**\n\n"
            "2. **Stop previous serving** — Kill any existing vLLM/ollama processes:\n"
            "   ```bash\n"
            "   pkill -f 'vllm serve' 2>/dev/null || true\n"
            "   ```\n\n"
            "3. **Launch model server** — Use vLLM with LoRA support:\n"
            "   ```bash\n"
            "   vllm serve <base_model> \\\n"
            "     --lora-modules sales=<adapter_path> \\\n"
            "     --port 8000 \\\n"
            "     --max-model-len 4096 &\n"
            "   ```\n"
            "   Or ollama if vLLM is unavailable:\n"
            "   ```bash\n"
            "   # Create Modelfile with adapter\n"
            "   ollama serve &\n"
            "   ```\n\n"
            "4. **Set up SSH port forwarding** — Forward remote:8000 to local:8000:\n"
            "   ```bash\n"
            "   ssh -N -L 8000:localhost:8000 <user>@<host> &\n"
            "   ```\n\n"
            "5. **Health check** — Wait for model to load (up to 120s):\n"
            "   ```bash\n"
            "   for i in $(seq 1 24); do\n"
            "     curl -s http://localhost:8000/v1/models && break\n"
            "     sleep 5\n"
            "   done\n"
            "   ```\n\n"
            "## Output\n\n"
            "Write endpoint info to:\n"
            "- .factory/serving/endpoint.txt (e.g., http://localhost:8000/v1)\n"
            "- .factory/serving/model_name.txt (model name as served)\n\n"
            "## Rules\n\n"
            "- Launch server in a tmux session on the remote\n"
            "- Verify the model responds before declaring success\n"
            "- If health check fails after 120s, report the error\n"
        ),
        reads={".factory/strategy/research.md"},
        writes={
            ".factory/serving/endpoint.txt",
            ".factory/serving/model_name.txt",
        },
    )

    # ── Node 7: Gate Serve ────────────────────────────────────────
    nodes["gate_serve"] = GateNode(
        id="gate_serve",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "if [ ! -f .factory/serving/endpoint.txt ]; then "
            "echo 'fail: endpoint.txt not found — serving did not complete'; "
            "exit 0; fi && "
            "ENDPOINT=$(cat .factory/serving/endpoint.txt) && "
            "RESPONSE=$(curl -s -o /dev/null -w '%{http_code}' "
            "\"${ENDPOINT}/models\" 2>/dev/null || echo 000) && "
            "if [ \"$RESPONSE\" = '200' ]; then "
            "echo 'pass: model endpoint is responding (HTTP 200)'; "
            "else "
            "echo \"fail: model endpoint returned HTTP $RESPONSE\"; "
            "fi"
        ),
        reads={".factory/serving/endpoint.txt"},
    )

    # ── Node 8: Eval Bench ────────────────────────────────────────
    nodes["eval_bench"] = FnNode(
        id="eval_bench",
        command=(
            "cd {project_path} && "
            "mkdir -p .factory/experiments && "
            "ENDPOINT=$(cat .factory/serving/endpoint.txt 2>/dev/null || "
            "echo 'http://localhost:8000/v1') && "
            "MODEL_NAME=$(cat .factory/serving/model_name.txt 2>/dev/null || "
            "echo 'sales') && "
            "EXP_ID=$(date +%Y%m%d_%H%M%S) && "
            "mkdir -p .factory/experiments/$EXP_ID && "
            "echo \"Running auto-bench eval at $(date)...\" && "
            "( cd $(find ~ -maxdepth 3 -name 'AutomationBench' -type d 2>/dev/null | "
            "head -1 || echo '.') && "
            "uv run auto-bench "
            "--model \"$MODEL_NAME\" "
            "--domains sales "
            "--base-url \"$ENDPOINT\" "
            "--num-examples 10 "
            "--api-key dummy "
            "--output {project_path}/.factory/experiments/$EXP_ID/eval.json "
            "2>&1 ) && "
            "python3 -c \""
            "import json, pathlib, glob; "
            "exp_dirs = sorted(glob.glob('{project_path}/.factory/experiments/*/eval.json')); "
            "latest = exp_dirs[-1] if exp_dirs else None; "
            "if not latest: print('0.0'); exit(); "
            "data = json.loads(pathlib.Path(latest).read_text()); "
            "tasks = data.get('results', data.get('tasks', [])); "
            "scores = [t.get('partial_credit', 0.0) for t in tasks if isinstance(t, dict)]; "
            "avg = sum(scores)/len(scores) if scores else 0.0; "
            "exp_dir = pathlib.Path(latest).parent; "
            "(exp_dir / 'score.txt').write_text(f'{avg:.4f}'); "
            "print(f'{avg:.4f}')\" && "
            "echo \"Eval complete: score saved to .factory/experiments/$EXP_ID/score.txt\""
        ),
        reads={".factory/serving/endpoint.txt", ".factory/serving/model_name.txt"},
        writes={".factory/experiments/eval.json"},
        notes="Runs auto-bench CLI locally against the served model, parses partial_credit scores.",
    )

    # ── Node 9: Verdict Gate ──────────────────────────────────────
    nodes["verdict_gate"] = GateNode(
        id="verdict_gate",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "LATEST_SCORE_FILE=$(ls -t .factory/experiments/*/score.txt 2>/dev/null | head -1) && "
            "if [ -z \"$LATEST_SCORE_FILE\" ]; then "
            "echo 'fail: no score.txt found'; exit 0; fi && "
            "CURRENT=$(cat \"$LATEST_SCORE_FILE\") && "
            "BASELINE=0.0 && "
            "if [ -f .factory/baseline.txt ]; then "
            "BASELINE=$(cat .factory/baseline.txt); fi && "
            "COUNTER=0 && "
            "if [ -f .factory/strategy/plateau-counter.txt ]; then "
            "COUNTER=$(cat .factory/strategy/plateau-counter.txt); fi && "
            "IMPROVED=$(python3 -c \""
            "c, b = float('$CURRENT'), float('$BASELINE'); "
            "print('yes' if c > b + 0.001 else 'no')\" 2>/dev/null) && "
            "if [ \"$IMPROVED\" = 'yes' ]; then "
            "echo \"$CURRENT\" > .factory/baseline.txt && "
            "echo 0 > .factory/strategy/plateau-counter.txt && "
            "echo \"reloop: score improved ($BASELINE -> $CURRENT) — continuing iteration\"; "
            "else "
            "NEW_COUNTER=$((COUNTER + 1)) && "
            "echo $NEW_COUNTER > .factory/strategy/plateau-counter.txt && "
            "if [ $NEW_COUNTER -ge 3 ]; then "
            "echo \"pass: plateaued after $NEW_COUNTER consecutive no-improvement cycles "
            "(best=$BASELINE, current=$CURRENT)\"; "
            "else "
            "echo \"reloop: no improvement cycle $NEW_COUNTER/3 "
            "(best=$BASELINE, current=$CURRENT) — trying again\"; "
            "fi; fi"
        ),
        reads={".factory/experiments/eval.json"},
        writes={".factory/baseline.txt", ".factory/strategy/plateau-counter.txt"},
    )

    # ── Node 10: Archivist ────────────────────────────────────────
    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        blocking=False,
        prompt_template=(
            "You are archiving the results of an AutomationBench-Sales fine-tuning "
            "experiment.\n\n"
            "## Tasks\n\n"
            "1. Read the research report at .factory/strategy/research.md\n"
            "2. Read the latest eval results in .factory/experiments/\n"
            "3. Read .factory/baseline.txt for the best score achieved\n"
            "4. Read .factory/strategy/plateau-counter.txt for iteration count\n"
            "5. Summarize:\n"
            "   - Base model used\n"
            "   - Training data stats (examples, format)\n"
            "   - Training config (LoRA rank, lr, epochs)\n"
            "   - Eval scores per iteration\n"
            "   - Best score achieved\n"
            "   - Plateau detection outcome\n"
            "   - Key learnings and recommendations for next run\n\n"
            "## Output\n\n"
            "Write the archive note to .factory/archive/experiments/abs-latest.md\n"
        ),
        reads={
            ".factory/strategy/research.md",
            ".factory/baseline.txt",
            ".factory/strategy/plateau-counter.txt",
        },
        writes={".factory/archive/experiments/abs-latest.md"},
    )

    # ── Edges ─────────────────────────────────────────────────────

    edges = [
        # Linear flow: research → data_prep → gate_data → train → gate_train
        Edge(source="research", target="data_prep"),
        Edge(source="data_prep", target="gate_data"),
        Edge(source="gate_data", target="train", condition=VerdictType.PROCEED),
        Edge(source="train", target="gate_train"),
        Edge(source="gate_train", target="serve", condition=VerdictType.PROCEED),
        # serve → gate_serve → eval_bench → verdict_gate
        Edge(source="serve", target="gate_serve"),
        Edge(source="gate_serve", target="eval_bench", condition=VerdictType.PROCEED),
        Edge(source="eval_bench", target="verdict_gate"),
        # Convergence loop
        Edge(source="verdict_gate", target="data_prep", condition=VerdictType.RELOOP),
        Edge(source="verdict_gate", target="archivist", condition=VerdictType.PROCEED),
        # Terminal edge from archivist (non-blocking, workflow exits)
    ]

    # ── Trigger ───────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "automationbench-sales"

    return Workflow(
        name="automationbench-sales",
        nodes=nodes,
        edges=edges,
        start_node="research",
        terminal=True,
        trigger=trigger,
    )
