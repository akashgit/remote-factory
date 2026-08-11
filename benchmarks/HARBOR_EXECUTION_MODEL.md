# Harbor Benchmark 执行模型详解

## 核心问题

当 Harbor 运行 agent 时：
1. **project** 具体指什么？
2. 应该在 **project** 里包括哪些东西？

---

## TL;DR（快速答案）

### project 是什么？

**`/workspace`** — 容器内的工作目录，包含：
- 待修复的代码（buggy code）
- 测试文件
- Git 仓库（已初始化）

### 应该包括什么？

- ✅ **buggy 源代码** — agent 要修改的文件
- ✅ **测试文件** — 验证修复是否正确
- ✅ **Git 仓库** — 必须初始化（Harbor 检查 main 分支）
- ✅ **依赖** — `pip install pytest` 等
- ❌ **不包括** `.factory/` 目录 — Harbor mode 不需要

---

## 详细复盘

### 1. Harbor 容器的目录结构

```
容器内部目录布局：
/
├── workspace/              ← project_path（agent 工作目录）
│   ├── .git/               ← Git 仓库（必须）
│   ├── exporter.py         ← buggy 源代码
│   ├── test_exporter.py    ← 测试文件
│   └── .gitignore          ← Git 配置
├── tmp/
│   └── task-instruction.md ← 任务描述（Harbor 写入）
└── logs/
    ├── agent/
    │   └── factory-ceo.txt ← Agent 输出日志
    └── verifier/
        └── reward.json     ← 验证结果
```

### 2. tomswe-harbor 示例详解

#### Dockerfile 做了什么？

```dockerfile
FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y git curl procps

# 设置工作目录
WORKDIR /workspace

# 1. 写入 buggy 源代码
RUN printf 'def to_csv(rows, headers):\n...' > /workspace/exporter.py

# 2. 写入测试文件
RUN printf 'import pytest\n...' > /workspace/test_exporter.py

# 3. 安装依赖
RUN pip install pytest

# 4. 创建 agent 用户
RUN useradd -m -s /bin/bash agent && chown -R agent:agent /workspace

# 5. 初始化 Git 仓库（CRITICAL！）
USER agent
WORKDIR /workspace
RUN git config --global user.name "Factory Agent" && \
    git config --global user.email "factory@agent.local" && \
    git init && \
    git add -A && \
    git commit -m "initial state"
```

**关键点：**
- `/workspace` 就是 `{project_path}`
- 必须是一个 **Git 仓库**（Harbor 检查 main 分支的变更）
- buggy code 已经提交到 Git（initial state）

---

### 3. Workflow 的 study node

#### tomswe workflow 的 study 定义

```python
nodes["study"] = FnNode(
    id="study",
    command=(
        "mkdir -p {project_path}/.factory/reviews && "
        "cd {project_path} && "
        "("
        "echo '=== Repository Structure ===' && "
        "find . -type f -name '*.py' | head -200 && "
        "echo '\\n=== Test Files ===' && "
        "find . -type f -name 'test_*.py' | head -50 && "
        "echo '\\n=== Task Instruction ===' && "
        "cat /tmp/task-instruction.md"
        ") > .factory/reviews/study-output.md 2>&1"
    ),
    writes={".factory/reviews/study-output.md"},
)
```

**{project_path} 的值：** `/workspace`

**study 做了什么？**
1. 创建 `.factory/reviews/` 目录（临时，用于存放 workflow 输出）
2. 扫描 `/workspace` 的结构（Python 文件、测试文件）
3. 读取 `/tmp/task-instruction.md`（Harbor 写入的任务描述）
4. 输出到 `.factory/reviews/study-output.md`

**注意：**
- `.factory/` 目录是 **workflow 临时创建的**，不是 project 的一部分
- project 本身（`/workspace`）只包含源代码和测试

---

### 4. Harbor harness 如何准备环境

#### factory_harbor_agent.py 的执行流程

```python
async def run(self, instruction: str, environment, context):
    # 1. 写入任务描述到 /tmp/task-instruction.md
    await self.exec_as_agent(
        environment,
        command=f"cat > /tmp/task-instruction.md << 'INSTREOF'\n{instruction}\nINSTREOF"
    )

    # 2. 调用 factory CLI
    await self.exec_as_agent(
        environment,
        command=(
            'factory ceo . --headless --no-github '
            '--focus "$(cat /tmp/task-instruction.md)"'
        )
    )
```

**流程：**
1. Harbor 读取 `benchmarks/tomswe-harbor/csv-export/instruction.md`
2. 写入容器的 `/tmp/task-instruction.md`
3. 调用 `factory ceo .`（当前目录 = `/workspace`）
4. Factory workflow 开始执行（study → builder → verify → merge）

---

### 5. Agent 看到了什么？

#### Agent 的视角

```
当前目录: /workspace
可见文件:
  exporter.py          (buggy 源代码)
  test_exporter.py     (测试文件)
  .git/                (Git 仓库)

任务描述: /tmp/task-instruction.md
内容:
  The export feature is broken for some records.
  When users download their data, certain rows come out garbled.
  
  ## User Profile
  You are working with a data engineer who has these preferences:
  - Testing: pytest, always test edge cases
  - Code style: use the csv module from stdlib
  ...
```

**Agent 的工作：**
1. 读取 `/tmp/task-instruction.md` 理解任务
2. 读取 `exporter.py` 找到 bug
3. 修改 `exporter.py` 修复 bug
4. 运行 `pytest test_exporter.py` 验证修复
5. `git add` + `git commit` 提交修复

---

### 6. Verifier 如何验证？

#### tests/test.sh 的逻辑

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /workspace
pip install pytest -q

# 运行测试
RESULT=$(python -m pytest test_exporter.py -v 2>&1) || true

# 检查是否通过
if echo "$RESULT" | grep -q 'passed' && ! echo "$RESULT" | grep -q 'failed'; then
    echo '{"reward": 1.0}' > /logs/verifier/reward.json
else
    echo '{"reward": 0.0}' > /logs/verifier/reward.json
fi

echo "$RESULT"
```

**验证步骤：**
1. 切换到 `/workspace`
2. 运行 pytest
3. 根据测试结果写入 `reward.json`
4. Harbor harness 读取 `reward.json` 判断成功/失败

---

## Einstein Arena 应该如何设计？

### 关键区别

| 特性 | tomswe-harbor | einsteinarena-harbor |
|------|---------------|----------------------|
| **任务类型** | Bug 修复 | 数学优化 |
| **project 内容** | buggy 源代码 + 测试 | **空目录**（或示例代码） |
| **Agent 输出** | 修复后的源代码 | `solution.json` |
| **验证方式** | pytest | Python verifier 函数 |
| **Git 需求** | 必须（检查 diff） | 可选（只需要 solution.json） |

### Einstein Arena 的 project 应该包含什么？

#### 选项 1：空项目（推荐）

```dockerfile
FROM python:3.11-slim

WORKDIR /workspace

# 安装依赖
RUN pip install numpy

# 初始化 Git（可选，但推荐）
RUN git config --global user.name "Agent" && \
    git init && \
    git add -A && \
    git commit -m "initial" --allow-empty

USER agent
```

**Agent 任务：**
1. 读取 `/tmp/task-instruction.md`（问题描述）
2. 编写优化算法
3. 生成 `/workspace/solution.json`

#### 选项 2：包含示例代码

```dockerfile
FROM python:3.11-slim

WORKDIR /workspace

# 提供示例代码/模板
RUN printf 'import numpy as np\n\n# TODO: implement optimization\n' > /workspace/solver.py

# 安装依赖
RUN pip install numpy

# 初始化 Git
RUN git init && git add -A && git commit -m "initial"
```

**Agent 任务：**
1. 读取 instruction.md
2. 实现 `solver.py`
3. 运行 solver 生成 `solution.json`

---

### Einstein Arena workflow 的 study node

```python
nodes["study"] = FnNode(
    id="study",
    command=(
        "mkdir -p {project_path}/.factory/reviews && "
        "cd {project_path} && "
        "("
        "echo '=== Task Instruction ===' && "
        "cat /tmp/task-instruction.md && "
        "echo '\\n=== Current Directory ===' && "
        "ls -la && "
        "echo '\\n=== Available Libraries ===' && "
        "pip list | grep -E '(numpy|scipy|sympy)'"
        ") > .factory/reviews/study-output.md 2>&1"
    ),
    writes={".factory/reviews/study-output.md"},
)
```

**与 tomswe 的区别：**
- 不需要扫描源代码（project 是空的）
- 重点是读取任务描述
- 列出可用的数学库

---

### Einstein Arena 的 verifier

#### tests/test.sh 示例（circle-packing）

```bash
#!/usr/bin/env bash
set -euo pipefail

SOLUTION_FILE="/workspace/solution.json"
SCORE_FILE="/workspace/score.txt"

# 检查 solution.json 存在
if [ ! -f "$SOLUTION_FILE" ]; then
    echo "ERROR: solution.json not found" >&2
    exit 1
fi

# 运行 verifier
cat > /tmp/verifier.py << 'EOF'
import numpy as np
import json

def evaluate(data):
    circles = np.array(data["circles"], dtype=np.float64)
    # ... 验证逻辑 ...
    return float(np.sum(radii))

with open("/workspace/solution.json", "r") as f:
    data = json.load(f)

score = evaluate(data)

with open("/workspace/score.txt", "w") as f:
    f.write(str(score))

print(f"Score: {score}")
EOF

python3 /tmp/verifier.py
```

**验证流程：**
1. 检查 `/workspace/solution.json` 存在
2. 运行 verifier 函数
3. 写入 score 到 `/workspace/score.txt`
4. Harbor harness 读取 score

---

## 总结

### project 的定义

**project = 容器内的 `/workspace` 目录**

包含：
1. **待解决的问题**（源代码 或 空目录）
2. **Git 仓库**（推荐，用于跟踪变更）
3. **依赖**（numpy, pytest 等）

### Einstein Arena 的 project 应该包含：

```
/workspace/
├── .git/              (Git 仓库，推荐)
└── (空目录，或可选的示例代码)
```

**Agent 工作流：**
1. 读取 `/tmp/task-instruction.md`
2. 编写优化算法
3. 生成 `/workspace/solution.json`
4. Verifier 验证 solution.json 并打分

### 与 tomswe 的对比

| 特性 | tomswe | einsteinarena |
|------|--------|---------------|
| project 初始状态 | buggy 源代码 + 测试 | 空目录 |
| Agent 输出 | 修复的源代码（git diff） | solution.json |
| Verifier 输入 | pytest 测试套件 | solution.json |
| Verifier 输出 | pass/fail (reward.json) | numerical score (score.txt) |
| Git 是否必须 | 是（Harbor 检查 diff） | 否（只需要 solution.json） |

---

**创建日期**: 2026-08-11  
**版本**: 1.0
