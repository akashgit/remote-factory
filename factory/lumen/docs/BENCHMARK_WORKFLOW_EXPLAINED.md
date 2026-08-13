╔══════════════════════════════════════════════════════════════╗
║    🎯 Benchmark Workflow 完整解析                          ║
║    - 一个 Workflow 如何解决整个 Benchmark                  ║
╚══════════════════════════════════════════════════════════════╝

你的问题非常关键！让我完整解释 SWE-bench workflow 如何应用到整个 benchmark。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🤔 核心疑问

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**你的观察**：
> SWE-bench workflow 看起来只是解决一个具体的 issue，
> 但 benchmark 通常有很多个 issue（成百上千个）

**关键问题**：
> 一个 workflow 定义如何应用到整个 benchmark？

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 答案：三层架构

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

整个系统是一个**三层架构**：

```
┌─────────────────────────────────────────────────────┐
│ Layer 3: Harbor Orchestrator                        │  ← 批量执行
│  - 遍历所有 benchmark 实例                          │
│  - 为每个实例创建隔离容器                           │
│  - 调用 Layer 2                                     │
└─────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│ Layer 2: Factory Harbor Agent                       │  ← 容器内适配
│  - 在容器内安装 factory CLI                         │
│  - 将 Harbor 的 instruction 转为 factory 命令      │
│  - 调用 Layer 1                                     │
└─────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│ Layer 1: SWE-bench Workflow                         │  ← 单任务解决
│  - 4 节点流程：study → builder → verify → merge    │
│  - 修复单个 issue                                   │
│  - 生成修复的代码                                   │
└─────────────────────────────────────────────────────┘
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 Layer 3: Harbor Orchestrator（批量执行）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 什么是 Harbor？

**Harbor** 是一个 benchmark 评测平台：
- 官方：https://github.com/akashgit/harbor
- 目的：标准化 AI 代码能力评测
- 支持：SWE-bench, TerminalBench, FeatureBench 等

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 的数据集格式

**SWE-bench 数据集示例**：
```json
{
  "dataset": "red-hat-ai/SWE-benchify-hard",
  "instances": [
    {
      "instance_id": "containers--image-90028",
      "repo": "containers/image",
      "base_commit": "a1b2c3d",
      "problem_statement": "Bug: podman push fails with...",
      "test_patch": "diff --git a/test.py ...",
      "golden_patch": "diff --git a/lib.go ..."
    },
    {
      "instance_id": "django--django-12345",
      "repo": "django/django",
      "base_commit": "e4f5g6h",
      "problem_statement": "QuerySet.filter() with Q objects...",
      "test_patch": "...",
      "golden_patch": "..."
    },
    ... // 几百到几千个 instances
  ]
}
```

**关键字段**：
- `instance_id` - 任务唯一标识
- `repo` - 目标仓库
- `base_commit` - 起始 commit
- `problem_statement` - 问题描述（issue 内容）
- `test_patch` - 测试补丁（用于验证修复）
- `golden_patch` - 正确答案（仅用于评分）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 的批量执行流程

**命令示例**：
```bash
# 运行整个 benchmark（所有实例）
uvx harbor run \
  --dataset "red-hat-ai/SWE-benchify-hard" \
  --agent-import-path factory_harbor_agent:SwebenchifyHardFactoryCeo \
  --model "anthropic/claude-opus-4-6" \
  --n-concurrent 10 \
  --jobs-dir /tmp/swebench-jobs

# 运行单个实例（测试）
uvx harbor run \
  --dataset "red-hat-ai/SWE-benchify-hard" \
  --agent-import-path factory_harbor_agent:SwebenchifyHardFactoryCeo \
  --include-task-name "*containers--image-90028" \
  --n-concurrent 1
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 的内部循环

```python
# Harbor 伪代码

dataset = load_dataset("red-hat-ai/SWE-benchify-hard")
agent_class = import_agent("factory_harbor_agent:SwebenchifyHardFactoryCeo")

results = []

for instance in dataset.instances:
    # 1. 创建隔离容器
    container = create_container(
        image=instance.docker_image,
        base_commit=instance.base_commit
    )
    
    # 2. 写入任务描述到标准位置
    container.write_file(
        "/tmp/task-instruction.md",
        instance.problem_statement
    )
    
    # 3. 实例化 agent（Factory Harbor Agent）
    agent = agent_class()
    
    # 4. 安装依赖（Claude Code + factory CLI）
    agent.install(container)
    
    # 5. 运行 agent
    agent.run(
        instruction=instance.problem_statement,
        environment=container
    )
    
    # 6. 应用测试补丁并验证
    container.apply_patch(instance.test_patch)
    test_result = container.run_tests()
    
    # 7. 评分（与 golden_patch 对比）
    score = evaluate(
        agent_patch=container.get_diff(),
        golden_patch=instance.golden_patch,
        test_passed=test_result.passed
    )
    
    # 8. 记录结果
    results.append({
        "instance_id": instance.instance_id,
        "resolved": test_result.passed,
        "score": score,
        "cost": agent.cost_usd
    })
    
    # 9. 清理容器
    container.cleanup()

# 10. 汇总统计
print(f"Resolved: {sum(r['resolved'] for r in results)}/{len(results)}")
print(f"Total cost: ${sum(r['cost'] for r in results):.2f}")
```

**关键点**：
- Harbor **循环所有 instances**
- 每个 instance 运行在**独立容器**中
- Agent 只负责**单个任务**
- Harbor 负责**批量 + 评分**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔧 Layer 2: Factory Harbor Agent（适配层）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 职责

**Factory Harbor Agent 是一个适配器**，连接：
- Harbor 的标准接口（`BaseInstalledAgent`）
- Factory 的 CLI 命令（`factory ceo`）

**文件**：`benchmarks/factory_harbor_agent.py`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 关键代码解析

#### 1. Install 阶段

```python
class FactoryCeo(BaseInstalledAgent):
    async def install(self, environment: BaseEnvironment) -> None:
        # Step 1: 安装系统依赖
        await self.exec_as_root(
            environment,
            command=(
                "apt-get update && "
                "apt-get install -y curl procps git"
            )
        )
        
        # Step 2: 安装 Claude Code
        await self.exec_as_agent(
            environment,
            command=(
                "curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh"
                " | bash -s -- && "
                "claude --version"
            )
        )
        
        # Step 3: 安装 factory CLI（从 git）
        await self.exec_as_agent(
            environment,
            command=(
                "curl -LsSf https://astral.sh/uv/install.sh | sh && "
                "uv tool install "
                "'remote-factory @ git+https://github.com/akashgit/remote-factory.git' && "
                "which factory"
            )
        )
```

**作用**：
- 在 Harbor 容器内准备 factory 运行环境
- 每个容器都独立安装（隔离）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 2. Run 阶段

```python
class FactoryCeo(BaseInstalledAgent):
    async def run(
        self,
        instruction: str,  ← Harbor 传入的问题描述
        environment: BaseEnvironment,  ← 容器环境
        context: AgentContext,
    ) -> None:
        # Harbor 已经将 instruction 写入 /tmp/task-instruction.md
        
        # 构建 factory 命令
        command = (
            'factory ceo . '
            '--headless '                               # Headless mode
            '--no-github '                              # 不创建 PR
            '--focus "$(cat /tmp/task-instruction.md)"' # 读取任务描述
        )
        
        # 在容器内执行
        await self.exec_as_agent(environment, command=command)
```

**关键转换**：
```
Harbor instruction → /tmp/task-instruction.md → factory --focus
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 为什么需要这个适配层？

**Harbor 的通用接口**：
```python
class BaseInstalledAgent:
    def install(environment) -> None
    def run(instruction, environment) -> None
```

**Factory 的实际命令**：
```bash
factory ceo /path --headless --focus "..."
```

**适配器的价值**：
1. 将 Harbor 的 Python API 转为 factory CLI 调用
2. 处理容器内的安装依赖
3. 传递环境变量（API keys, 配置等）
4. 捕获和报告执行结果

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ⚙️ Layer 1: SWE-bench Workflow（单任务执行）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 职责

**只负责解决单个 issue**：
- 读取 `/tmp/task-instruction.md`（Harbor 写入）
- 执行 4 节点流程
- 提交修复代码到当前分支

**触发方式**：
```bash
# Factory Harbor Agent 调用
factory ceo . --headless --focus "$(cat /tmp/task-instruction.md)"
```

**Workflow 内部**：
```
study → builder → gate_verify → auto_merge
```

**详细分析**：见 `lumen_docs/SWEBENCH_WORKFLOW_ANALYSIS.md`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔄 完整流程示例

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 假设 SWE-bench 有 1000 个实例

**Step 1: Harbor 加载数据集**
```python
dataset = {
  "instances": [
    {"instance_id": "django-12345", "problem": "..."},
    {"instance_id": "flask-67890", "problem": "..."},
    ... # 998 more
  ]
}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Step 2: Harbor 循环执行（伪代码）**

```python
for i, instance in enumerate(dataset.instances):
    print(f"[{i+1}/1000] Processing {instance.instance_id}")
    
    # 创建容器
    container = Docker.create(
        image="python:3.11",
        repo=instance.repo,
        commit=instance.base_commit
    )
    
    # 写任务描述
    container.write("/tmp/task-instruction.md", instance.problem)
    
    # 实例化 agent
    agent = FactoryCeo()
    
    # 安装（只在第一次，后续使用镜像缓存）
    agent.install(container)
    
    # 运行
    agent.run(
        instruction=instance.problem,
        environment=container
    )
    # 这一步内部调用：
    # factory ceo . --headless --focus "$(cat /tmp/task-instruction.md)"
    #   ↓
    # WorkflowExecutor 运行 swebench workflow
    #   ↓
    # study → builder → gate_verify → auto_merge
    
    # 验证
    container.apply_patch(instance.test_patch)
    result = container.run_tests()
    
    # 记录
    results[i] = {
        "instance_id": instance.instance_id,
        "resolved": result.passed,
        "patch": container.get_diff()
    }
    
    # 清理
    container.cleanup()

# 最终统计
print(f"Resolved: {sum(r['resolved'] for r in results)}/1000")
# 示例输出：Resolved: 347/1000 (34.7%)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Step 3: 单个实例的内部执行**

以 `django-12345` 为例：

```
Harbor Container (django-12345):
  ├─ /workspace/django/  (cloned at base_commit)
  ├─ /tmp/task-instruction.md  (问题描述)
  └─ Running: factory ceo . --headless --focus "..."
      ↓
      Factory CEO 启动
      ↓
      WorkflowExecutor 运行 swebench workflow
      ↓
      ┌─────────────────────────────────────┐
      │ Node 1: Study (FnNode)              │
      │ - find . -name '*.py' | head -200   │
      │ - cat /tmp/task-instruction.md      │
      │ → .factory/reviews/study-output.md  │
      └─────────────────────────────────────┘
      ↓
      ┌─────────────────────────────────────┐
      │ Node 2: Builder (AgentNode)         │
      │ - Spawn: factory agent builder      │
      │ - Read: /tmp/task-instruction.md    │
      │ - Fix: django/db/models/query.py    │
      │ - Test: pytest tests/               │
      │ - Commit: "Fix Q object issue"      │
      └─────────────────────────────────────┘
      ↓
      ┌─────────────────────────────────────┐
      │ Node 3: Gate Verify (GateNode-fn)   │
      │ - Check: git diff HEAD~1            │
      │ - Parse: builder output             │
      │ - Result: "tests PASSED"            │
      │ → Verdict: PROCEED                  │
      └─────────────────────────────────────┘
      ↓
      ┌─────────────────────────────────────┐
      │ Node 4: Auto Merge (FnNode)         │
      │ - Update main branch ref            │
      │ - Copy files to parent worktree     │
      └─────────────────────────────────────┘
      ↓
      Factory 完成
      ↓
Harbor 验证:
  - Apply test_patch
  - Run tests
  - ✅ PASSED → resolved = True
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 数据流图

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```
┌─────────────────────────────────────────────────┐
│ SWE-bench Dataset (HuggingFace)                 │
│ - 1000 instances                                │
│ - Each: repo + commit + problem + test          │
└──────────────────┬──────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────┐
│ Harbor Orchestrator                             │
│                                                 │
│ for instance in dataset:                        │
│   container = create_container()                │
│   container.write("/tmp/task-instruction.md")  │
│   agent.run(instruction, container)             │
│   verify(container)                             │
└──────────────────┬──────────────────────────────┘
                   ↓ (1000x, 并发 N)
┌─────────────────────────────────────────────────┐
│ Factory Harbor Agent (每个容器)                 │
│                                                 │
│ install():                                      │
│   - apt-get install curl git                    │
│   - install Claude Code                         │
│   - uv tool install remote-factory              │
│                                                 │
│ run(instruction, environment):                  │
│   - factory ceo . --headless --focus "..."      │
└──────────────────┬──────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────┐
│ Factory CEO (Headless Mode)                    │
│ WorkflowExecutor                                │
│                                                 │
│ workflow = swebench_workflow()                  │
│ executor.run(workflow)                          │
└──────────────────┬──────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────┐
│ SWE-bench Workflow (4 nodes)                    │
│                                                 │
│ study → builder → gate_verify → auto_merge      │
│                                                 │
│ Input:  /tmp/task-instruction.md                │
│ Output: Committed fix on current branch         │
└──────────────────┬──────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────┐
│ Harbor Verification                             │
│                                                 │
│ - Apply test_patch                              │
│ - Run pytest                                    │
│ - Compare with golden_patch                     │
│ → resolved: true/false                          │
└─────────────────────────────────────────────────┘
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 关键设计理念

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1️⃣ 单一职责原则

每层只做一件事：

- **Workflow**: 解决单个任务（one issue）
- **Agent**: 适配接口（Harbor ↔ Factory）
- **Harbor**: 批量执行和评分（many issues）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2️⃣ 标准化接口

**任务描述的标准位置**：
```
/tmp/task-instruction.md
```

所有 benchmark workflows 都读这个文件：
- SWE-bench: 读取 issue 描述
- TerminalBench: 读取终端任务
- FeatureBench: 读取功能需求

**好处**：
- Workflow 无需知道 Harbor 的存在
- Harbor 无需知道 Workflow 的内部结构
- 解耦！

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3️⃣ 容器隔离

**每个 instance 独立容器**：

好处：
- 并行执行（10 个容器同时运行）
- 环境隔离（不同 repo 不互相影响）
- 失败隔离（一个崩溃不影响其他）
- 可复现（每次都是干净环境）

示例：
```bash
# Harbor 同时运行 10 个容器
Container 1: django-12345  (修复 QuerySet bug)
Container 2: flask-67890   (修复路由问题)
Container 3: numpy-11111   (修复矩阵计算)
...
Container 10: pytorch-9999 (修复梯度下降)

# 每个容器独立运行 factory ceo
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4️⃣ Headless 优先

**为什么 benchmark workflows 都是 headless**：

```python
# Benchmark workflow 特征
- ❌ 无 CEO GateNode
- ❌ 无 User GateNode
- ✅ 只有 fn gates
- ✅ 完全自动化

# 原因
- Benchmark 需要无人值守批量运行
- 1000 个 instances 无法手动审查
- 追求速度和并发度
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📈 实际运行案例

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 1: 单实例测试

**命令**：
```bash
./benchmarks/run-swebenchifyhard.sh "containers--image-90028"
```

**执行流程**：
```
1. Harbor 加载 SWE-benchify-hard 数据集
2. 过滤：只包含 instance_id 匹配 "*containers--image-90028"
3. 创建 1 个容器
4. 安装 Claude Code + factory
5. 运行 factory ceo . --headless --focus "..."
6. Workflow 执行：study → builder → verify → merge
7. Harbor 验证：apply test_patch → run tests
8. 输出结果：resolved: true/false
```

**耗时**：~30-60 分钟（单个实例）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 2: 完整 Benchmark

**命令**：
```bash
# 运行整个 SWE-benchify-hard（~500 实例）
uvx harbor run \
  --dataset "red-hat-ai/SWE-benchify-hard" \
  --agent-import-path factory_harbor_agent:SwebenchifyHardFactoryCeo \
  --n-concurrent 10  # 10 个容器并行
```

**执行流程**：
```
1. Harbor 加载 500 个实例
2. 创建容器池（10 个并发）
3. 批量执行：
   - 第 1 批：实例 1-10（并行）
   - 第 2 批：实例 11-20（并行）
   - ...
   - 第 50 批：实例 491-500（并行）
4. 每个容器独立运行 workflow
5. Harbor 汇总结果
6. 输出统计：
   - Resolved: 172/500 (34.4%)
   - Total cost: $2,345.67
   - Avg time per instance: 42 min
```

**总耗时**：~3.5 小时（10 并发）

**如果串行**：~350 小时（14.6 天）

**并发的价值**：100x 加速！

━━━━━━━━━━━━━━━━━━━━━━━━━━━━