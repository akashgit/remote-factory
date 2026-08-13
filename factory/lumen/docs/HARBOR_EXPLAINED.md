╔══════════════════════════════════════════════════════════════╗
║    🚢 Harbor 完整介绍                                      ║
║    - 什么是 Harbor？与 Benchmark 集成的关系               ║
╚══════════════════════════════════════════════════════════════╝

## 🤔 Harbor 是什么？

**Harbor** 是一个开源的 **AI 代码 Agent 评测平台**。

**官方仓库**：https://github.com/akashgit/harbor

**核心定位**：
- **标准化评测框架**：为 AI 代码 Agent 提供统一的评测接口
- **容器编排系统**：在隔离容器中运行每个任务
- **Benchmark 托管平台**：支持 SWE-bench, FeatureBench, TerminalBench 等主流 benchmark
- **对比评测工具**：公平对比不同 Agent 的性能（Factory, Claude Code, Devin 等）

**类比**：
- **MLflow** 之于机器学习模型 = **Harbor** 之于 AI 代码 Agent
- **Docker Compose** 之于容器编排 = **Harbor** 之于 Benchmark 容器编排

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 为什么需要 Harbor？

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 问题：AI Agent Benchmark 很难标准化

**挑战 1: 环境差异**
```
Agent A: 在 macOS + Python 3.11 + 特定依赖版本下运行
Agent B: 在 Ubuntu + Python 3.12 + 不同依赖下运行
→ 结果无法公平对比！
```

**挑战 2: 接口差异**
```
Agent A: 通过 CLI 调用：agent solve "task.md"
Agent B: 通过 API 调用：POST /solve {task: "..."}
Agent C: 通过 Python SDK：agent.run(task="...")
→ 每个 Agent 都要写不同的评测脚本！
```

**挑战 3: 验证标准不统一**
```
Benchmark A: 运行单元测试验证
Benchmark B: 对比输出文件
Benchmark C: 手动检查代码
→ 评分标准不一致！
```

**挑战 4: 并发和成本控制**
```
手动运行：1000 个任务串行，需要几天
批量并发：容器冲突、资源耗尽
→ 需要调度系统！
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 的解决方案

**1. 容器隔离 + 标准镜像**
```
每个任务运行在独立 Docker 容器中
→ 环境一致、可复现
```

**2. 统一 Agent 接口**
```python
class BaseInstalledAgent:
    async def install(environment) -> None:
        # 安装依赖
        
    async def run(instruction, environment) -> None:
        # 运行任务
```

**3. 统一数据集格式**
```json
{
  "instances": [
    {
      "instance_id": "...",
      "problem_statement": "...",
      "test_patch": "...",
      "golden_patch": "..."
    }
  ]
}
```

**4. 自动并发编排**
```bash
harbor run --dataset <name> --agent <agent> --n-concurrent 10
→ 自动管理 10 个并发容器
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🏗️ Harbor 的核心概念

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1️⃣ Dataset（数据集）

**定义**：一组相关的评测任务

**来源**：
- **远程数据集**（HuggingFace）：
  ```
  - swe-bench/swe-bench-verified
  - red-hat-ai/SWE-benchify-hard
  - featurebench
  - terminal-bench@2.0
  ```

- **本地数据集**（repo 内）：
  ```
  benchmarks/tomswe-harbor/
  benchmarks/programbench-harbor/
  ```

**格式**：
- 远程：HuggingFace Datasets 格式
- 本地：Harbor 目录结构（见下文）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2️⃣ Instance（实例）

**定义**：数据集中的单个任务

**示例**：
```json
{
  "instance_id": "django--django-12345",
  "repo": "django/django",
  "base_commit": "abc123",
  "problem_statement": "QuerySet.filter() with Q objects...",
  "test_patch": "diff --git a/tests/...",
  "golden_patch": "diff --git a/django/..."
}
```

**关键字段**：
- `instance_id` - 唯一标识符
- `problem_statement` - 问题描述（给 Agent 看的）
- `test_patch` - 测试补丁（验证修复是否正确）
- `golden_patch` - 正确答案（仅用于评分对比）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3️⃣ Agent（代理）

**定义**：实现 Harbor 接口的 AI Agent

**接口**：
```python
class BaseInstalledAgent:
    async def install(self, environment: BaseEnvironment) -> None:
        """在容器内安装 Agent 和依赖"""
        
    async def run(
        self,
        instruction: str,           # 任务描述
        environment: BaseEnvironment, # 容器环境
        context: AgentContext,       # 上下文信息
    ) -> None:
        """运行任务并产生修复"""
```

**Factory 的实现**：`benchmarks/factory_harbor_agent.py`
```python
class SwebenchFactoryCeo(BaseInstalledAgent):
    async def install(self, environment):
        # 安装 Claude Code
        # 安装 factory CLI
        
    async def run(self, instruction, environment, context):
        # factory ceo . --headless --focus "..."
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4️⃣ Environment（环境）

**定义**：隔离的 Docker 容器环境

**提供的能力**：
```python
class BaseEnvironment:
    async def exec_as_agent(command: str) -> str:
        """以 agent 用户执行命令"""
        
    async def exec_as_root(command: str) -> str:
        """以 root 执行命令（安装依赖）"""
        
    async def write_file(path: str, content: str):
        """写入文件到容器"""
        
    async def read_file(path: str) -> str:
        """从容器读取文件"""
```

**实际映射**：
- 每个 instance → 1 个独立容器
- 容器镜像：`task.toml` 中指定
- 工作目录：`/workspace`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 5️⃣ Verifier（验证器）

**定义**：验证 Agent 的修复是否正确

**验证流程**：
```
1. Agent 完成修复（产生代码变更）
2. Harbor 应用 test_patch
3. 运行测试（pytest, npm test, ./verify.sh）
4. 对比结果：
   - resolved: true/false（测试通过/失败）
   - score: 0-1（与 golden_patch 的相似度）
```

**示例**（SWE-bench）：
```bash
# Harbor 自动执行
git apply test.patch
pytest tests/

# 如果测试通过 → resolved: true
# 如果测试失败 → resolved: false
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔄 Harbor 的完整工作流程

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 命令示例

```bash
uvx harbor run \
    --dataset "swe-bench/swe-bench-verified" \
    --agent-import-path benchmarks/factory_harbor_agent.py:SwebenchFactoryCeo \
    --model "anthropic/claude-opus-4-6" \
    --include-task-name "*django--django-12345" \
    --n-concurrent 1 \
    --jobs-dir /tmp/harbor-jobs
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 内部执行流程（15 步）

```
┌──────────────────────────────────────────────────────────┐
│ Step 1: 加载数据集                                        │
└──────────────────────────────────────────────────────────┘
Harbor 从 HuggingFace 下载 "swe-bench/swe-bench-verified"
过滤出 instance_id = "django--django-12345"

↓

┌──────────────────────────────────────────────────────────┐
│ Step 2: 准备容器环境                                      │
└──────────────────────────────────────────────────────────┘
创建 Docker 容器：
  - 镜像: python:3.11-slim
  - 工作目录: /workspace
  - 网络: 隔离或受限

克隆代码仓库到容器：
  git clone https://github.com/django/django.git /workspace
  git checkout abc123  # base_commit

↓

┌──────────────────────────────────────────────────────────┐
│ Step 3: 导入 Agent                                        │
└──────────────────────────────────────────────────────────┘
import benchmarks.factory_harbor_agent
agent = SwebenchFactoryCeo()

↓

┌──────────────────────────────────────────────────────────┐
│ Step 4: 安装 Agent（调用 install()）                      │
└──────────────────────────────────────────────────────────┘
在容器内执行：
  1. apt-get update && apt-get install -y curl git procps
  2. curl -fsSL https://.../bootstrap.sh | bash  # 安装 Claude Code
  3. uv tool install remote-factory               # 安装 factory CLI

验证：
  claude --version
  factory --help

↓

┌──────────────────────────────────────────────────────────┐
│ Step 5: 写入任务描述                                      │
└──────────────────────────────────────────────────────────┘
Harbor 将 problem_statement 写入标准位置：
  echo "QuerySet.filter() with Q objects..." > /tmp/task-instruction.md

这是 Harbor 和 Agent 的约定接口！

↓

┌──────────────────────────────────────────────────────────┐
│ Step 6: 运行 Agent（调用 run()）                          │
└──────────────────────────────────────────────────────────┘
Agent 在容器内执行：
  factory ceo . --headless --no-github \
      --focus "$(cat /tmp/task-instruction.md)"

Factory CEO 启动：
  - 检测项目状态
  - 运行 swebench_workflow()
  - study → builder → gate_verify → auto_merge

↓

┌──────────────────────────────────────────────────────────┐
│ Step 7: Builder Agent 修复代码                            │
└──────────────────────────────────────────────────────────┘
Factory Builder 分析问题：
  - 读取 /tmp/task-instruction.md
  - 理解 QuerySet.filter() 的问题
  - 定位到 django/db/models/query.py
  - 编辑代码修复 bug
  - 运行测试验证

提交修复：
  git add django/db/models/query.py
  git commit -m "Fix Q object issue"

↓

┌──────────────────────────────────────────────────────────┐
│ Step 8: Agent 完成                                        │
└──────────────────────────────────────────────────────────┘
factory ceo 退出
Agent.run() 返回
容器中现在有了修复的代码（已提交到 git）

↓

┌──────────────────────────────────────────────────────────┐
│ Step 9: Harbor 提取修复                                   │
└──────────────────────────────────────────────────────────┘
在容器内执行：
  git diff base_commit HEAD > agent_patch.diff

agent_patch.diff 包含了 Agent 的所有代码变更

↓

┌──────────────────────────────────────────────────────────┐
│ Step 10: 应用测试补丁                                     │
└──────────────────────────────────────────────────────────┘
Harbor 应用 test_patch（来自数据集）：
  git apply test.patch

test.patch 包含验证修复的测试用例

↓

┌──────────────────────────────────────────────────────────┐
│ Step 11: 运行测试验证                                     │
└──────────────────────────────────────────────────────────┘
在容器内执行：
  pytest tests/queries/test_q_object.py -v

捕获输出：
  - 退出码: 0 (成功) 或 非0 (失败)
  - 测试结果: PASSED / FAILED

↓

┌──────────────────────────────────────────────────────────┐
│ Step 12: 计算评分                                         │
└──────────────────────────────────────────────────────────┘
基础评分：
  resolved = (测试退出码 == 0)

详细评分（可选）：
  对比 agent_patch 和 golden_patch：
    - 文件匹配度
    - 行变更相似度
    - 语义等价性
  → score: 0.0 - 1.0

↓

┌──────────────────────────────────────────────────────────┐
│ Step 13: 记录结果                                         │
└──────────────────────────────────────────────────────────┘
Harbor 写入结果：
{
  "instance_id": "django--django-12345",
  "resolved": true,
  "score": 0.95,
  "duration_seconds": 1847,
  "cost_usd": 2.34,
  "agent_patch": "diff --git a/...",
  "test_output": "..."
}

↓

┌──────────────────────────────────────────────────────────┐
│ Step 14: 清理容器                                         │
└──────────────────────────────────────────────────────────┘
docker rm -f container_id
清理临时文件

↓

┌──────────────────────────────────────────────────────────┐
│ Step 15: 返回结果                                         │
└──────────────────────────────────────────────────────────┘
Harbor 输出 JSON 结果到 stdout
Shell 脚本捕获并写入 results/ 目录
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📦 Harbor 数据集格式

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 远程数据集格式（HuggingFace）

**示例：SWE-bench**

```python
from datasets import load_dataset

dataset = load_dataset("swe-bench/swe-bench-verified")

# dataset['train'][0]
{
  "instance_id": "django__django-11099",
  "repo": "django/django",
  "base_commit": "419a78300f7cd27611196e1e464d50fd0385ff27",
  "patch": "diff --git a/django/...",  # golden_patch
  "test_patch": "diff --git a/tests/...",
  "problem_statement": "Description:\n\nThe issue occurs when...",
  "hints_text": "",
  "created_at": "2019-03-23T15:00:00Z",
  "version": "3.0",
  "FAIL_TO_PASS": ["tests.queries.tests.TestQ::test_..."],
  "PASS_TO_PASS": [...]
}
```

**Harbor 自动解析**：
- 从 `base_commit` 克隆代码
- 将 `problem_statement` 传给 Agent
- 用 `test_patch` 验证
- 用 `patch` (golden_patch) 评分

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 本地数据集格式（Harbor 目录结构）

**示例：tomswe-harbor/csv-export/**

```
csv-export/
├── environment/              # 环境配置
│   ├── Dockerfile           # 容器镜像定义
│   ├── requirements.txt     # Python 依赖
│   └── setup.sh            # 初始化脚本
│
├── instruction.md           # 任务描述（给 Agent 看）
│
├── task.toml               # Harbor 任务元数据
│
└── tests/                  # 验证脚本
    ├── verify.sh           # 主验证脚本
    └── test_csv.py        # 测试文件
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `task.toml` 格式

```toml
schema_version = "1.3"

[task]
name = "tomswe/csv-export"
description = "Fix CSV export to handle fields with commas and quotes"
authors = []
keywords = ["tomswe", "csv", "quoting"]

[metadata]
difficulty = "medium"
category = "programming"

[environment]
network_mode = "public"          # 网络访问：public / private / limited
build_timeout_sec = 900.0       # 构建超时
cpus = 2                        # CPU 核心数
memory_mb = 4096                # 内存限制
storage_mb = 10240              # 磁盘限制
gpus = 0                        # GPU 数量
mcp_servers = []                # MCP 服务器列表

[agent]
timeout_sec = 3600.0            # Agent 运行超时

[verifier]
timeout_sec = 300.0             # 验证超时
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `instruction.md` 格式

```markdown
The export feature is broken for some records. When users download
their data, certain rows come out garbled. It works fine most of the
time though.

## User Profile
You are working with a data engineer who has these preferences:
- **Verbosity:** verbose — likes to understand the full picture
- **Testing:** pytest, always test edge cases with special characters
- **Code style:** use csv module from stdlib, type hints
- **Git:** descriptive commit messages explaining the why
- **Data handling:** never silently drop or modify data
```

**特点**：
- 故意模糊（像真实用户报告）
- 包含用户偏好（影响 Agent 行为）
- 不给具体文件位置（Agent 需自己探索）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `tests/verify.sh` 格式

```bash
#!/bin/bash
set -e

# 运行单元测试
pytest tests/test_csv.py -v

# 自定义验证
python -c "
import csv
import io

# 测试修复是否正确
data = [['Name', 'Description'], ['Test', 'Value with, comma']]
output = io.StringIO()
writer = csv.writer(output)
writer.writerows(data)

result = output.getvalue()
assert '\"Value with, comma\"' in result, 'CSV quoting not working'
print('✅ CSV export fix verified')
"

echo "All tests passed"
```

**Harbor 执行**：
```bash
# 在容器内运行
cd /workspace
./tests/verify.sh

# 退出码 0 → resolved: true
# 退出码 非0 → resolved: false
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔗 Harbor 与 Factory 的集成

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 三层架构回顾

```
┌─────────────────────────────────────────┐
│ Layer 3: Harbor Orchestrator            │  ← 批量执行
│  - 遍历数据集                            │
│  - 创建容器                              │
│  - 调用 Agent                            │
│  - 验证结果                              │
│  - 汇总评分                              │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│ Layer 2: Factory Harbor Agent           │  ← 适配层
│  - 实现 BaseInstalledAgent              │
│  - install(): 安装 Claude Code + factory │
│  - run(): 调用 factory ceo               │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│ Layer 1: Factory Workflow                │  ← 单任务执行
│  - study → builder → verify → merge      │
│  - 读取 /tmp/task-instruction.md        │
│  - 修复代码并提交                        │
└─────────────────────────────────────────┘
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Factory 的 Harbor Agent 实现

**文件**：`benchmarks/factory_harbor_agent.py`

**8 个 Agent 类**：
```python
# 每个 benchmark 一个 Agent 类

class SwebenchFactoryCeo(BaseInstalledAgent):
    """SWE-bench 适配器"""
    
class FeaturebenchFactoryCeo(BaseInstalledAgent):
    """FeatureBench 适配器"""
    
class TerminalbenchFactoryCeo(BaseInstalledAgent):
    """TerminalBench 适配器"""
    
class ProgramBenchFactoryCeo(BaseInstalledAgent):
    """ProgramBench 适配器"""
    
class LegacybenchFactoryCeo(BaseInstalledAgent):
    """LegacyBench 适配器"""
    
class HarborIndexFactoryCeo(BaseInstalledAgent):
    """Harbor Index 适配器"""
    
class TomsweFactoryCeo(BaseInstalledAgent):
    """TomSWE 适配器（本地测试）"""
    
class SalitrapFactoryCeo(BaseInstalledAgent):
    """Salitrap 适配器"""
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 典型实现模式

```python
class SwebenchFactoryCeo(BaseInstalledAgent):
    
    async def install(self, environment: BaseEnvironment) -> None:
        """在容器内安装依赖"""
        
        # Step 1: 安装系统依赖
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl git procps"
        )
        
        # Step 2: 安装 Claude Code
        await self.exec_as_agent(
            environment,
            command=(
                "curl -fsSL https://downloads.claude.ai/.../bootstrap.sh | bash && "
                "claude --version"
            )
        )
        
        # Step 3: 安装 factory CLI
        await self.exec_as_agent(
            environment,
            command=(
                "curl -LsSf https://astral.sh/uv/install.sh | sh && "
                "uv tool install 'remote-factory @ git+https://github.com/akashgit/remote-factory.git' && "
                "which factory"
            )
        )
    
    async def run(
        self,
        instruction: str,              # Harbor 传入的任务描述
        environment: BaseEnvironment,  # 容器环境
        context: AgentContext,         # 上下文信息
    ) -> None:
        """运行任务"""
        
        # Harbor 已经将 instruction 写入 /tmp/task-instruction.md
        # Factory 约定从这个位置读取
        
        # 构建 factory 命令
        command = (
            'factory ceo . '
            '--headless '                               # Headless 模式
            '--no-github '                              # 不创建 PR
            '--focus "$(cat /tmp/task-instruction.md)"' # 读取任务描述
        )
        
        # 在容器内执行
        await self.exec_as_agent(environment, command=command)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 关键约定

#### 1️⃣ 任务描述的标准位置

**Harbor → Agent 约定**：
```
/tmp/task-instruction.md
```

**Harbor 写入**：
```python
# Harbor 内部
await environment.write_file(
    "/tmp/task-instruction.md",
    instance["problem_statement"]
)
```

**Factory 读取**：
```bash
# factory_harbor_agent.py
factory ceo . --focus "$(cat /tmp/task-instruction.md)"
```

**为什么这样设计？**
- 标准化：所有 benchmark workflows 都从这里读
- 解耦：Harbor 不需要知道 Factory 的内部结构
- 灵活：可以轻松换成其他 Agent

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 2️⃣ 工作目录

**Harbor 约定**：
```
/workspace
```

所有代码仓库都克隆到 `/workspace`：
```bash
# Harbor 内部
git clone https://github.com/django/django.git /workspace
cd /workspace
```

Factory 在 `/workspace` 中运行：
```bash
cd /workspace
factory ceo .  # 当前目录就是项目根目录
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 3️⃣ Git 提交

**Harbor 期望**：
- Agent 将修复提交到 git
- Harbor 通过 `git diff base_commit HEAD` 提取修复

**Factory 实现**：
```python
# Factory Builder 自动提交
git add <modified_files>
git commit -m "Fix issue"
```

**Harbor 提取**：
```bash
git diff abc123 HEAD > agent_patch.diff
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🆕 添加新 Benchmark 时 Harbor 的角色

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 1: 使用现有远程数据集

**假设**：你想支持一个新的 benchmark，它已经有 HuggingFace 数据集

**步骤**：

#### 1. 在 `config.sh` 中添加配置

```bash
mynewbench)
    BENCH_DATASET="huggingface-org/mynewbench"
    BENCH_AGENT_CLASS="factory_harbor_agent:MynewbenchFactoryCeo"
    BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
    BENCH_FILTER_STYLE="exact"
    ;;
```

#### 2. 在 `factory_harbor_agent.py` 中添加 Agent

```python
class MynewbenchFactoryCeo(BaseInstalledAgent):
    async def install(self, environment: BaseEnvironment) -> None:
        # 标准安装流程
        await self.exec_as_root(environment, command="apt-get update && ...")
        await self.exec_as_agent(environment, command="install claude code")
        await self.exec_as_agent(environment, command="install factory")
    
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # 可选：添加额外指令
        extra = Path("mynewbench-extra-instructions.md").read_text()
        full_instruction = f"{instruction}\n\n{extra}"
        await environment.write_file("/tmp/task-instruction.md", full_instruction)
        
        # 运行 factory
        command = 'factory ceo . --headless --no-github --focus "$(cat /tmp/task-instruction.md)"'
        await self.exec_as_agent(environment, command=command)
```

#### 3. 测试

```bash
# Harbor 会自动处理：
# - 从 HuggingFace 下载数据集
# - 创建容器
# - 克隆代码
# - 调用你的 Agent
# - 验证结果

./benchmarks/run.sh mynewbench "test-instance-001"
```

**Harbor 在这里做什么？**
- ✅ 下载和解析数据集
- ✅ 创建和管理容器
- ✅ 克隆代码仓库
- ✅ 应用测试补丁
- ✅ 运行验证
- ✅ 计算评分

**你需要做什么？**
- ✅ 实现 Agent 接口（install + run）
- ✅ 确保 Factory 从 `/tmp/task-instruction.md` 读取任务
- ✅ 确保修复被提交到 git

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 2: 创建本地数据集

**假设**：你想创建一个自定义 benchmark，没有现成的数据集

**步骤**：

#### 1. 创建目录结构

```bash
mkdir -p benchmarks/mynewbench-harbor/task-001/{environment,tests}
```

#### 2. 编写任务描述

```bash
# benchmarks/mynewbench-harbor/task-001/instruction.md
cat > benchmarks/mynewbench-harbor/task-001/instruction.md <<'EOF'
The login feature is broken. Users report getting "Invalid credentials"
even with correct passwords.

## Context
This is a Flask app with SQLAlchemy ORM.

## User Profile
- Prefers verbose error messages
- Uses pytest for testing
- Follows PEP 8 strictly
EOF
```

#### 3. 创建任务配置

```bash
# benchmarks/mynewbench-harbor/task-001/task.toml
cat > benchmarks/mynewbench-harbor/task-001/task.toml <<'EOF'
schema_version = "1.3"

[task]
name = "mynewbench/task-001"
description = "Fix login authentication bug"
keywords = ["auth", "bug"]

[metadata]
difficulty = "medium"
category = "programming"

[environment]
network_mode = "public"
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096

[agent]
timeout_sec = 3600.0

[verifier]
timeout_sec = 300.0
EOF
```

#### 4. 编写验证脚本

```bash
# benchmarks/mynewbench-harbor/task-001/tests/verify.sh
cat > benchmarks/mynewbench-harbor/task-001/tests/verify.sh <<'EOF'
#!/bin/bash
set -e

# 运行测试
pytest tests/test_auth.py -v

# 自定义验证
python -c "
from app import login
assert login('user', 'password') == True
print('✅ Login fix verified')
"
EOF

chmod +x benchmarks/mynewbench-harbor/task-001/tests/verify.sh
```

#### 5. 配置环境（可选）

```bash
# benchmarks/mynewbench-harbor/task-001/environment/Dockerfile
cat > benchmarks/mynewbench-harbor/task-001/environment/Dockerfile <<'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y git curl

WORKDIR /workspace
COPY . .

RUN pip install -r requirements.txt
EOF
```

#### 6. 在 `config.sh` 中添加配置

```bash
mynewbench)
    BENCH_LOCAL_PATH="${HARNESS_DIR}/benchmarks/mynewbench-harbor"
    BENCH_AGENT_CLASS="factory_harbor_agent:MynewbenchFactoryCeo"
    BENCH_AGENT_IMPORT_FLAG="--agent"
    BENCH_FILTER_STYLE="none"
    ;;
```

#### 7. 测试

```bash
# Harbor 会：
# - 读取本地目录
# - 使用 task.toml 创建容器
# - 读取 instruction.md 作为任务描述
# - 运行 Agent
# - 执行 tests/verify.sh 验证

./benchmarks/run.sh mynewbench task-001
```

**Harbor 在这里做什么？**
- ✅ 读取本地目录结构
- ✅ 根据 task.toml 创建容器
- ✅ 读取 instruction.md
- ✅ 调用 Agent
- ✅ 运行 tests/verify.sh
- ✅ 记录结果

**你需要做什么？**
- ✅ 创建目录结构
- ✅ 编写 instruction.md（任务描述）
- ✅ 编写 task.toml（配置）
- ✅ 编写 tests/verify.sh（验证）
- ✅ 实现 Agent 类

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 Harbor 的价值总结

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 对于 Factory 来说

**没有 Harbor 之前**：
```bash
# 你需要手动：
for instance in dataset:
    docker run ...  # 创建容器
    git clone ...   # 克隆代码
    docker exec ... "factory ceo ..."  # 运行 factory
    docker exec ... "pytest tests/"    # 运行测试
    # 手动对比结果
    # 手动计算评分
    docker rm ...   # 清理容器
```

**有 Harbor 之后**：
```bash
# 一行命令
uvx harbor run \
    --dataset "swe-bench/swe-bench-verified" \
    --agent-import-path factory_harbor_agent:SwebenchFactoryCeo \
    --n-concurrent 10

# Harbor 自动处理所有繁琐的事情
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 提供的核心价值

| 功能 | 手动实现 | Harbor 提供 |
|------|---------|------------|
| **容器管理** | 手写 docker 命令 | 自动创建、销毁 |
| **并发控制** | 自己写调度器 | `--n-concurrent 10` |
| **数据集加载** | 自己解析 JSON/CSV | 自动下载和解析 |
| **代码克隆** | 手动 git clone | 自动克隆到正确版本 |
| **测试验证** | 手动运行测试 | 自动应用补丁并验证 |
| **结果记录** | 自己写 JSON | 自动记录结构化结果 |
| **评分计算** | 手动对比 patch | 自动计算相似度 |
| **错误处理** | 容器泄漏、僵尸进程 | 自动清理和超时 |
| **成本跟踪** | 手动计算 token | 自动记录 cost_usd |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 对于 Benchmark 社区

**标准化**：
- 所有 AI Agent 用同样的接口
- 公平对比不同 Agent 的性能
- 可复现的评测结果

**数据集共享**：
- HuggingFace 托管 → 任何人可用
- 统一格式 → 轻松添加新任务
- 版本控制 → 可追溯变更

**对比评测**：
```bash
# 对比 Factory vs Claude Code vs Devin
harbor run --agent factory ...
harbor run --agent claude-code ...
harbor run --agent devin ...

# 自动生成对比报告
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📝 总结

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 是什么？

**一句话**：Harbor 是 AI 代码 Agent 的标准化评测平台

**核心功能**：
- 🐳 容器编排（创建、管理、清理）
- 📊 数据集托管（远程 + 本地）
- 🔌 Agent 接口（统一的 install/run）
- ✅ 自动验证（测试、评分）
- 📈 结果记录（结构化输出）
- 🚀 并发控制（批量执行）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 与新 Benchmark 集成的关系

**Harbor 负责**：
- ✅ 基础设施（容器、并发、验证）
- ✅ 数据集管理（加载、解析）
- ✅ 结果记录（评分、统计）

**你需要做**：
- ✅ 实现 Agent 接口（2 个方法：install, run）
- ✅ 配置 benchmark（config.sh 中 3 行）
- ✅ （可选）创建本地数据集（目录结构 + task.toml）

**关系**：
```
你的 Benchmark = Harbor（基础设施）+ Agent 实现（10-50 行代码）
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 为什么需要 Harbor？

**没有 Harbor**：
- ❌ 每个 benchmark 都要重新实现容器、并发、验证
- ❌ 不同 Agent 的结果无法公平对比
- ❌ 数据集格式不统一
- ❌ 手动管理容器容易出错

**有了 Harbor**：
- ✅ 统一基础设施（专注业务逻辑）
- ✅ 公平对比（标准化环境）
- ✅ 数据集共享（HuggingFace 生态）
- ✅ 自动化一切（一行命令运行）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔗 相关资源

- **Harbor 官方仓库**：https://github.com/akashgit/harbor
- **Harbor 文档**：https://harborframework.com
- **SWE-bench 数据集**：https://huggingface.co/datasets/swe-bench/swe-bench-verified
- **Factory Harbor Agent**：`benchmarks/factory_harbor_agent.py`
- **本地数据集示例**：`benchmarks/tomswe-harbor/`
- **三层架构解析**：`lumen_docs/BENCHMARK_WORKFLOW_EXPLAINED.md`
- **Benchmarks 目录指南**：`lumen_docs/BENCHMARKS_DIRECTORY.md`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
