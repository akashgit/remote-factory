# Einstein Arena Project Setup 建议

## 核心问题

Einstein Arena 的 `/workspace` (project) 应该包含什么？

---

## 推荐方案：空项目 + 依赖

### 理由

1. **不需要源代码** — 这是数学优化，不是 bug 修复
2. **输出是数据** — `solution.json`，不是代码修改
3. **简单明了** — agent 专注于优化算法，不被模板代码干扰

---

## Dockerfile 模板

```dockerfile
FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /workspace

# 安装 Python 数学库
RUN pip install --no-cache-dir numpy

# 创建 agent 用户
RUN useradd -m -s /bin/bash agent && chown -R agent:agent /workspace

# 配置 Git（推荐，用于 factory workflow）
RUN git config --system safe.directory /workspace && \
    git config --system safe.directory '*'

# 切换到 agent 用户并初始化 Git
USER agent
WORKDIR /workspace

RUN git config --global user.name "Factory Agent" && \
    git config --global user.email "factory@agent.local" && \
    git config --global init.defaultBranch main && \
    git init && \
    git commit --allow-empty -m "initial state"

# 切回 root（Harbor 需要）
USER root
```

---

## 容器内部结构

```
/workspace/
├── .git/              # Git 仓库（空，只有 initial commit）
└── (空目录，agent 自己创建文件)
```

**Agent 的工作流：**
1. 读取 `/tmp/task-instruction.md`
2. 编写优化代码（可选，如 `solver.py`）
3. 运行优化算法
4. 生成 `/workspace/solution.json`

---

## 变体：提供辅助函数模板（可选）

如果想给 agent 提供一些帮助，可以预置辅助函数：

```dockerfile
# ... 上面的步骤 ...

# 提供辅助函数模板（可选）
RUN printf 'import numpy as np\nimport json\n\n# Helper functions\ndef save_solution(data, filename="solution.json"):\n    """Save solution to JSON file."""\n    with open(filename, "w") as f:\n        json.dump(data, f, indent=2)\n    print(f"Solution saved to {filename}")\n\ndef load_instruction():\n    """Load task instruction from standard location."""\n    with open("/tmp/task-instruction.md", "r") as f:\n        return f.read()\n' > /workspace/helpers.py

# 将 helpers.py 加入 Git
USER agent
RUN git add helpers.py && git commit -m "add helper functions"
USER root
```

**好处：**
- Agent 可以快速保存 solution：`from helpers import save_solution`
- 减少样板代码
- 仍然不强制使用

---

## 与现有抽取脚本的集成

### 当前 extract_einstein_arena.py 生成的 Dockerfile

```python
def generate_dockerfile(self, problem: dict) -> str:
    verifier = problem["verifier"]
    needs_decimal = "from decimal import" in verifier or "Decimal" in verifier

    base = "FROM python:3.11-slim\n\n"
    deps = "RUN pip install --no-cache-dir numpy\n"
    if needs_decimal:
        deps += "# Note: decimal module is built-in\n"
    deps += "\n"
    workdir = "WORKDIR /workspace\n"

    return base + deps + workdir
```

### 应该改进为：

```python
def generate_dockerfile(self, problem: dict) -> str:
    """生成 environment/Dockerfile"""
    verifier = problem["verifier"]
    needs_decimal = "from decimal import" in verifier or "Decimal" in verifier

    dockerfile = """FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git \\
    curl \\
    procps \\
    ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install Python dependencies
RUN pip install --no-cache-dir numpy
"""

    if needs_decimal:
        dockerfile += "# Note: decimal module is built-in\n"

    dockerfile += """
# Create agent user
RUN useradd -m -s /bin/bash agent && chown -R agent:agent /workspace
RUN git config --system safe.directory /workspace && \\
    git config --system safe.directory '*'

# Initialize Git repository
USER agent
WORKDIR /workspace
RUN git config --global user.name "Factory Agent" && \\
    git config --global user.email "factory@agent.local" && \\
    git config --global init.defaultBranch main && \\
    git init && \\
    git commit --allow-empty -m "initial state"

USER root
"""

    return dockerfile
```

---

## study node 建议

```python
nodes["study"] = FnNode(
    id="study",
    command=(
        "mkdir -p {project_path}/.factory/reviews && "
        "cd {project_path} && "
        "("
        "echo '=== Task Instruction ===' && "
        "cat /tmp/task-instruction.md && "
        "echo '' && "
        "echo '=== Available Libraries ===' && "
        "python3 -c 'import numpy; print(f\"numpy {numpy.__version__}\")' && "
        "echo '' && "
        "echo '=== Working Directory ===' && "
        "pwd && "
        "ls -la"
        ") > .factory/reviews/study-output.md 2>&1"
    ),
    writes={".factory/reviews/study-output.md"},
)
```

**输出示例：**
```
=== Task Instruction ===
## Problem
Pack 26 circles to maximize the sum of radii...

=== Available Libraries ===
numpy 1.24.3

=== Working Directory ===
/workspace
total 12
drwxr-xr-x  3 agent agent 4096 Aug 11 03:00 .
drwxr-xr-x 18 root  root  4096 Aug 11 03:00 ..
drwxr-xr-x  7 agent agent 4096 Aug 11 03:00 .git
```

---

## Builder node prompt 建议

```python
prompt_template=(
    "You are solving a mathematical optimization problem for the Einstein Arena benchmark.\n\n"
    "## Your Task\n\n"
    "1. **Read the problem** — Read /tmp/task-instruction.md for the full problem description.\n\n"
    "2. **Understand the objective** — The instruction specifies whether to MINIMIZE or MAXIMIZE, "
    "and shows the solution format (JSON schema).\n\n"
    "3. **Choose an optimization approach** — Based on the problem type:\n"
    "   - Geometry problems (circle packing, sphere packing): simulated annealing, basin-hopping\n"
    "   - Discrete problems (difference bases, Erdos): genetic algorithms, local search\n"
    "   - Function optimization (polynomials, autocorrelation): gradient descent, spectral methods\n\n"
    "4. **Implement your solution** — Write Python code in /workspace to:\n"
    "   - Implement the optimization algorithm\n"
    "   - Run the optimization\n"
    "   - Generate solution.json in the EXACT format specified\n\n"
    "5. **Verify locally** — The verifier code is embedded in tests/test.sh. "
    "You can extract and run it locally to check your solution before submitting.\n\n"
    "## Output Format\n\n"
    "You MUST create /workspace/solution.json with the structure specified in the instruction.\n"
    "The Harbor verifier will read this file and compute your score.\n\n"
    "## Minimum Improvement\n\n"
    "If the instruction mentions a 'minimum improvement' threshold, your solution must "
    "improve upon the current best by at least that amount to be considered meaningful.\n"
)
```

---

## 验证流程

### Verifier (tests/test.sh) 已包含在抽取脚本中

当前 `generate_test_sh()` 已经正确：

```bash
#!/bin/bash
set -euo pipefail

SOLUTION_FILE="/workspace/solution.json"
SCORE_FILE="/workspace/score.txt"

if [ ! -f "$SOLUTION_FILE" ]; then
    echo "ERROR: solution.json not found" >&2
    exit 1
fi

# Run verifier
cat > /tmp/verifier.py << 'VERIFIER_EOF'
<embedded verifier code>
VERIFIER_EOF

python3 /tmp/verifier.py
```

**流程：**
1. Agent 生成 `/workspace/solution.json`
2. Harbor 调用 `tests/test.sh`
3. Verifier 读取 solution.json，计算 score
4. 写入 `/workspace/score.txt`
5. Harbor 读取 score，判断成功/失败

---

## 总结

### Einstein Arena 的 project 应该是：

**空的 Git 仓库 + numpy**

不需要：
- ❌ 预置源代码（这不是 bug 修复）
- ❌ 测试文件在 /workspace（verifier 在 tests/test.sh）
- ❌ 复杂的项目结构

需要：
- ✅ Git 仓库（factory workflow 要求）
- ✅ numpy（大部分问题需要）
- ✅ 干净的 /workspace（agent 自由创建文件）

### 下一步

1. 更新 `extract_einstein_arena.py` 的 `generate_dockerfile()`
2. 重新抽取所有任务
3. 实现 `einsteinarena` workflow（Phase 2）
4. 测试单个任务：`benchmarks/run.sh einsteinarena circle-packing`

---

**创建日期**: 2026-08-11  
**版本**: 1.0
