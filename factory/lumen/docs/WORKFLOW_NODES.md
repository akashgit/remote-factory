# Workflow Node 类型完整指南

本文档详细说明 remote-factory 中所有可用的 workflow node 类型。

> 定义文件：`factory/workflow/primitives.py`  
> 最后更新：2026-08-07

---

## 📊 概览

remote-factory 的 workflow 是由 **节点（Nodes）** 和 **边（Edges）** 构成的有向无环图（DAG）。

- **节点数量**：8 种类型
- **基础类型**：`Node` (所有节点的基类)
- **联合类型**：`NodeType = AgentNode | FnNode | GateNode | ForkNode | JoinNode | SubgraphForkNode | SelectionNode | Study`

---

## 🎯 Node 类型分类

### 1️⃣ 执行节点 (Execution Nodes)
- **AgentNode** - 调用 Claude Code agent
- **FnNode** - 运行 shell 命令或 Python 函数
- **Study** - 特殊的 FnNode，运行 `factory study`

### 2️⃣ 控制流节点 (Control Flow Nodes)
- **GateNode** - 决策门，产生 Verdict (PROCEED/RELOOP/HALT)
- **ForkNode** - 并行启动多个分支
- **JoinNode** - 等待所有并行分支完成（barrier）

### 3️⃣ 高级节点 (Advanced Nodes)
- **SubgraphForkNode** - 在隔离的 worktree 中并行运行 N 个子图副本
- **SelectionNode** - 从多个实验分支中选择最佳结果

---

## 📖 详细说明

### 🔹 Node (基类)

所有节点的共同基类，定义了通用属性。

**属性：**
```python
id: str                    # 节点唯一标识符
reads: set[str]           # 节点读取的文件路径集合
writes: set[str]          # 节点写入的文件路径集合
blocking: bool = True     # 是否阻塞（默认 True）
```

**示例：**
```python
Node(
    id="example",
    reads={".factory/config.json"},
    writes={".factory/output.md"},
    blocking=True
)
```

---

### 1️⃣ AgentNode

调用 Claude Code specialist agent 执行任务。

**用途：**
- 需要 AI 推理、分析、生成代码的任务
- 例如：研究、策略制定、代码实现、QA review

**属性：**
```python
role: AgentRole                      # Agent 角色（必需）
model: str = ""                      # 模型覆盖（可选，默认使用 agent pool 配置）
prompt_template: str = ""            # 自定义 prompt 模板（可选）
tools: list[str] = []                # 可用工具列表（可选）
timeout: int | None = None           # 超时时间（秒）
max_iterations: int = 1              # 最大迭代次数
post_checks: list[ArtifactCheck] = []  # 产物验证规则
```

**AgentRole 枚举值：**
```python
class AgentRole(str, Enum):
    RESEARCHER = "researcher"              # 研究和信息收集
    STRATEGIST = "strategist"              # 策略制定和假设生成
    BUILDER = "builder"                    # 代码实现
    HEALTH_CHECKER = "health_checker"      # 健康检查（测试、构建）
    CODE_REVIEWER = "code_reviewer"        # 代码审查
    ADVERSARIAL_TESTER = "adversarial_tester"  # 对抗性 QA 测试
    FAILURE_ANALYST = "failure_analyst"    # 失败分析（research 模式）
    CEO = "ceo"                            # CEO 协调者
    ARCHIVIST = "archivist"                # 归档和知识管理
    REFINER = "refiner"                    # 变更请求分类和范围界定
    SKILL_REVIEWER = "skill_reviewer"      # Skill 审查
```

**默认 Agent Pool 配置：**
```python
DEFAULT_AGENT_POOL = {
    "researcher": AgentConfig(role=RESEARCHER, model="sonnet", timeout=600),
    "strategist": AgentConfig(role=STRATEGIST, model="opus", timeout=600),
    "builder": AgentConfig(role=BUILDER, model="opus", timeout=1200),
    "health_checker": AgentConfig(role=HEALTH_CHECKER, model="opus", timeout=600),
    "code_reviewer": AgentConfig(role=CODE_REVIEWER, model="opus", timeout=900),
    "adversarial_tester": AgentConfig(role=ADVERSARIAL_TESTER, model="opus", timeout=1800),
    "failure_analyst": AgentConfig(role=FAILURE_ANALYST, model="opus", timeout=600),
    "ceo": AgentConfig(role=CEO, model="opus", timeout=3600),
    "archivist": AgentConfig(role=ARCHIVIST, model="haiku", timeout=300),
    "refiner": AgentConfig(role=REFINER, model="opus", timeout=600),
    "skill_reviewer": AgentConfig(role=SKILL_REVIEWER, model="opus", timeout=600),
}
```

**示例：**
```python
# 基础 researcher（使用默认 agent pool 配置）
AgentNode(
    id="researcher_similar",
    role=AgentRole.RESEARCHER,
    writes={".factory/strategy/research-local.md"}
)

# 自定义 prompt 的 researcher
AgentNode(
    id="researcher_tokens",
    role=AgentRole.RESEARCHER,
    prompt_template=(
        "Design token research. "
        "Find the project's main CSS/theme files..."
    ),
    writes={".factory/design-system/token-audit.md"}
)

# 带验证规则的 builder
AgentNode(
    id="builder",
    role=AgentRole.BUILDER,
    timeout=1800,
    post_checks=[
        ArtifactCheck(
            path=".factory/reviews/builder-latest.md",
            must_exist=True,
            min_size=100
        )
    ]
)
```

**产物验证 (ArtifactCheck)：**
```python
class ArtifactCheck(BaseModel):
    path: str                      # 文件路径
    must_exist: bool = True        # 必须存在
    min_size: int = 0              # 最小文件大小（字节）
    must_contain: list[str] = []   # 必须包含的字符串列表
```

---

### 2️⃣ FnNode

运行确定性的 shell 命令或 Python 可调用对象。

**用途：**
- 执行工具命令（git, npm, pytest 等）
- 运行 factory CLI 命令
- 执行脚本或自动化任务

**属性：**
```python
command: str = ""              # Shell 命令字符串
callable_name: str | None = None  # Python 可调用对象名称（高级）
notes: str = ""                # 节点说明文档
```

**命令模板变量：**
- `{project_path}` - 项目根目录路径
- `$VERDICT`, `$PR_NUMBER`, `$SCORE_BEFORE`, `$SCORE_AFTER` - 环境变量

**示例：**
```python
# 运行 eval
FnNode(
    id="eval_before",
    command="factory eval {project_path}",
    writes={".factory/experiments/eval_before.json"}
)

# Git 操作
FnNode(
    id="commit",
    command="cd {project_path} && git add -A && git commit -m 'checkpoint'",
    notes="Commit current changes before experiment"
)

# 运行测试
FnNode(
    id="run_tests",
    command="cd {project_path} && npm test",
    notes="Run test suite to verify health"
)

# 条件命令
FnNode(
    id="check_file",
    command="[ -f {project_path}/.factory/config.json ] && echo PROCEED || echo HALT"
)
```

---

### 3️⃣ Study

特殊的 FnNode，用于运行 `factory study` 命令。

**用途：**
- 分析代码库并生成 observations.md
- Workflow 中的代码理解和分析步骤

**属性：**
```python
focus: str | None = None   # 可选的聚焦主题
```

**示例：**
```python
# 基础 study
Study(
    id="study",
    command="factory study {project_path}",
    writes={".factory/strategy/observations.md"}
)

# 带聚焦主题的 study
Study(
    id="study_auth",
    command="factory study {project_path}",
    focus="authentication system",
    writes={".factory/strategy/observations.md"}
)
```

---

### 4️⃣ GateNode

决策节点，产生 Verdict (PROCEED/RELOOP/HALT) 来控制 workflow 流程。

**用途：**
- 条件分支决策
- 人工批准关卡（user gate）
- CEO review 关卡（agent gate）
- 自动化检查（fn gate）

**属性：**
```python
evaluator_type: Literal["agent", "fn", "user"]  # 评估器类型
evaluator_role: AgentRole | None = None         # Agent 角色（agent 类型）
evaluator_command: str | None = None            # Shell 命令（fn 类型）
gate_prompt: str = ""                           # Gate 提示（agent/user 类型）
```

**三种 Gate 类型：**

#### 4.1 Agent Gate (CEO Review)
由 CEO agent 审查并做出决策。

```python
GateNode(
    id="gate_builder",
    evaluator_type="agent",
    evaluator_role=AgentRole.CEO,
    gate_prompt=(
        "Review Builder output at .factory/reviews/builder-latest.md. "
        "PROCEED if implementation matches hypothesis. "
        "REDIRECT if off-track. ABORT if critical bugs."
    ),
    reads={".factory/reviews/builder-latest.md"}
)
```

#### 4.2 User Gate (Human Approval)
需要人类用户批准才能继续。

```python
GateNode(
    id="gate_strategy",
    evaluator_type="user",
    gate_prompt="Review hypothesis at .factory/strategy/current.md. Approve?",
    reads={".factory/strategy/current.md"}
)
```

#### 4.3 Fn Gate (Automated Check)
运行 shell 命令，根据退出码或输出决策。

```python
# 检查文件是否存在
GateNode(
    id="gate_has_factory",
    evaluator_type="fn",
    evaluator_command=(
        "[ -f {project_path}/.factory/config.json ] && "
        "echo PROCEED || echo HALT"
    )
)

# 运行 precheck
GateNode(
    id="gate_precheck",
    evaluator_type="fn",
    evaluator_command=(
        "factory precheck {project_path} "
        "--score-before 0.7 --score-after 0.85"
    )
)
```

**Verdict 类型：**
```python
class VerdictType(str, Enum):
    PROCEED = "proceed"  # 继续执行下一个节点
    RELOOP = "reloop"    # 回到指定节点重新执行
    HALT = "halt"        # 停止执行

# Verdict 对象
class Verdict:
    type: VerdictType
    target: str | None           # RELOOP 的目标节点
    feedback: str | None         # RELOOP 的反馈信息
    max_iterations: int = 3      # RELOOP 最大迭代次数
    reason: str | None           # HALT 的原因
```

---

### 5️⃣ ForkNode

并行执行节点 - 同时启动多个目标分支。

**用途：**
- 并行运行多个独立的研究任务
- 同时执行多个不相互依赖的步骤

**属性：**
```python
targets: list[str]   # 目标节点 ID 列表
```

**示例：**
```python
# 并行启动 3 个 researcher
ForkNode(
    id="fork_research",
    targets=[
        "researcher_similar",
        "researcher_techstack",
        "researcher_pitfalls"
    ]
)

# 并行启动 4 个设计研究
ForkNode(
    id="fork_design_research",
    targets=[
        "researcher_tokens",
        "researcher_components",
        "researcher_patterns",
        "researcher_ux"
    ]
)
```

**配合 JoinNode 使用：**
```python
# Fork
nodes["fork"] = ForkNode(id="fork", targets=["task_a", "task_b", "task_c"])

# Join (等待所有任务完成)
nodes["join"] = JoinNode(id="join", sources=["task_a", "task_b", "task_c"])

edges = [
    Edge(source="fork", target="task_a"),
    Edge(source="fork", target="task_b"),
    Edge(source="fork", target="task_c"),
    Edge(source="task_a", target="join"),
    Edge(source="task_b", target="join"),
    Edge(source="task_c", target="join"),
]
```

---

### 6️⃣ JoinNode

Barrier 节点 - 等待所有源节点完成后才继续。

**用途：**
- 同步并行分支
- 确保所有前置任务完成后再执行下一步

**属性：**
```python
sources: list[str]   # 源节点 ID 列表
```

**示例：**
```python
JoinNode(
    id="join_research",
    sources=[
        "researcher_similar",
        "researcher_techstack",
        "researcher_pitfalls"
    ]
)
```

**完整的 Fork-Join 模式：**
```python
# 1. Fork：启动 3 个并行任务
fork → [task_a, task_b, task_c]

# 2. 并行执行
task_a ┐
task_b ┼→ join
task_c ┘

# 3. Join：等待所有任务完成
join → next_step
```

---

### 7️⃣ SubgraphForkNode

在隔离的 git worktree 中并行运行 N 个子图副本。

**用途：**
- 并行实验（每个实验独立修改代码）
- 避免并行分支之间的文件冲突
- Parallel-improve mode 的核心节点

**属性：**
```python
subgraph_entry: str         # 子图入口节点 ID
subgraph_exit: str          # 子图出口节点 ID
parallelism: int = 3        # 并行度（运行几个副本）
worktree_isolated: bool = True  # 是否使用 worktree 隔离
```

**工作原理：**
1. 创建 N 个独立的 git worktree（基于同一个 commit）
2. 在每个 worktree 中运行完整的子图（entry → ... → exit）
3. 每个子图有自己的 WorkflowExecutor 实例
4. 子图可以修改文件而不互相冲突

**示例：**
```python
# Parallel-improve 模式中的并行实验
SubgraphForkNode(
    id="subgraph_fork_improve",
    subgraph_entry="strategist",
    subgraph_exit="gate_precheck",
    parallelism=3,              # 同时运行 3 个实验
    worktree_isolated=True
)

# 子图范围：
# strategist → builder → health_checker → ... → gate_precheck
```

**典型流程：**
```
study
  ↓
SubgraphForkNode (parallelism=3)
  ├─→ Worktree 1: strategist → builder → eval → gate
  ├─→ Worktree 2: strategist → builder → eval → gate
  └─→ Worktree 3: strategist → builder → eval → gate
  ↓
SelectionNode (选择最佳)
  ↓
finalize
```

---

### 8️⃣ SelectionNode

从多个完成的实验分支中选择最佳结果。

**用途：**
- 在 SubgraphForkNode 后选择最佳实验
- 基于评分或其他指标做出选择

**属性：**
```python
strategy: Literal["best_score"] = "best_score"  # 选择策略
```

**当前支持的策略：**
- `"best_score"` - 选择 eval 分数最高的分支

**示例：**
```python
SelectionNode(
    id="select_best",
    strategy="best_score",
    reads={
        ".factory/experiments/branch_1/eval.json",
        ".factory/experiments/branch_2/eval.json",
        ".factory/experiments/branch_3/eval.json"
    }
)
```

**典型使用场景：**
```python
# 1. SubgraphForkNode 运行 N 个并行实验
SubgraphForkNode(id="fork", parallelism=3, ...)

# 2. SelectionNode 选择最佳实验
SelectionNode(id="select", strategy="best_score")

# 3. 继续使用最佳结果
FnNode(id="finalize", command="factory finalize ...")
```

---

## 🔗 Edge (边)

连接节点的有向边，可选择性地附加 verdict 条件。

**属性：**
```python
source: str                    # 源节点 ID
target: str                    # 目标节点 ID
condition: VerdictType | None  # 可选的 verdict 条件
```

**示例：**
```python
# 无条件边
Edge(source="study", target="strategist")

# 条件边（基于 gate verdict）
Edge(source="gate_builder", target="health_checker", condition=VerdictType.PROCEED)
Edge(source="gate_builder", target="builder", condition=VerdictType.RELOOP)
Edge(source="gate_builder", target="finalize", condition=VerdictType.HALT)
```

**条件边的工作原理：**

当一个 GateNode 产生 Verdict 时：
- `PROCEED` → 沿着 `condition=PROCEED` 的边前进
- `RELOOP(target, feedback)` → 回到指定的 target 节点
- `HALT(reason)` → 沿着 `condition=HALT` 的边前进（或终止）

```python
# 完整的 gate 模式
nodes["gate_tests"] = GateNode(
    id="gate_tests",
    evaluator_type="fn",
    evaluator_command="cd {project_path} && npm test"
)

edges = [
    # 测试通过 → 继续
    Edge(source="gate_tests", target="code_reviewer", condition=VerdictType.PROCEED),
    
    # 测试失败 → 回到 builder
    Edge(source="gate_tests", target="builder", condition=VerdictType.RELOOP),
    
    # 致命错误 → 中止
    Edge(source="gate_tests", target="abort", condition=VerdictType.HALT),
]
```

---

## 🎯 常见 Workflow 模式

### 模式 1: 线性流程
```
study → strategist → builder → eval → finalize
```

### 模式 2: Fork-Join 并行
```
fork_research
  ├─→ researcher_a ┐
  ├─→ researcher_b ┼→ join_research → strategist
  └─→ researcher_c ┘
```

### 模式 3: Gate 条件分支
```
builder → gate_tests
            ├─ PROCEED → code_reviewer
            ├─ RELOOP → builder (重试)
            └─ HALT → abort
```

### 模式 4: 并行实验 + 选择
```
study → SubgraphForkNode (parallelism=3)
          ├─→ exp_1 ┐
          ├─→ exp_2 ┼→ SelectionNode → finalize
          └─→ exp_3 ┘
```

### 模式 5: 深度 QA 子图
```
builder → health_checker → code_reviewer → gate_review
            ↓ PROCEED
          adversarial_tester → gate_precheck → finalize
```

---

## 📚 实际 Workflow 示例

### Founder Mode (最简单)
```python
nodes = {
    "study": Study(id="study", ...),
    "strategist": AgentNode(id="strategist", role=STRATEGIST, ...),
    "builder": AgentNode(id="builder", role=BUILDER, ...),
    "gate_tests": GateNode(id="gate_tests", evaluator_type="fn", ...),
    "finalize": FnNode(id="finalize", ...),
}

edges = [
    Edge(source="study", target="strategist"),
    Edge(source="strategist", target="builder"),
    Edge(source="builder", target="gate_tests"),
    Edge(source="gate_tests", target="finalize", condition=PROCEED),
]
```

### Improve Mode (标准流程)
```python
nodes = {
    "study": Study(id="study", ...),
    "researcher": AgentNode(id="researcher", role=RESEARCHER, ...),
    "strategist": AgentNode(id="strategist", role=STRATEGIST, ...),
    "gate_strategy": GateNode(id="gate_strategy", evaluator_type="agent", ...),
    "builder": AgentNode(id="builder", role=BUILDER, ...),
    # ... deep-qa subgraph nodes ...
    "gate_precheck": GateNode(id="gate_precheck", evaluator_type="fn", ...),
    "archivist": AgentNode(id="archivist", role=ARCHIVIST, ...),
}
```

### Build Mode (Fork-Join)
```python
# Fork: 3 个并行研究者
fork_research → [researcher_similar, researcher_techstack, researcher_pitfalls]

# Join: 等待所有研究完成
join_research ← [researcher_similar, researcher_techstack, researcher_pitfalls]

# 继续流程
join_research → strategist → ...
```

---

## 🔍 节点查找技巧

### 1. 查看所有 node 类型
```bash
grep "^class.*Node" factory/workflow/primitives.py
```

### 2. 查看某个 workflow 的所有节点
```bash
factory workflow show improve --format json | jq '.nodes | keys'
```

### 3. 查看某个节点的完整定义
```bash
factory workflow show improve --format json | jq '.nodes.strategist'
```

### 4. 统计节点类型分布
```bash
factory workflow show improve --format json | \
  jq '.nodes | to_entries | group_by(.value | keys[0]) | 
      map({type: .[0].value | keys[0], count: length})'
```

---

## 📖 参考资源

- **定义文件**: [`factory/workflow/primitives.py`](../factory/workflow/primitives.py)
- **Workflow 定义**: [`factory/workflow/definitions.py`](../factory/workflow/definitions.py)
- **Executor 实现**: [`factory/workflow/executor.py`](../factory/workflow/executor.py)
- **Workflows 总览**: [WORKFLOWS_OVERVIEW.md](WORKFLOWS_OVERVIEW.md)
- **主文档**: [../CLAUDE.md](../CLAUDE.md)

---

## 💡 设计原则

1. **类型安全**: 所有节点都是 Pydantic 模型，带有严格的类型检查
2. **声明式**: Workflow 是纯数据结构，可序列化为 JSON
3. **可组合**: 通过子图辅助函数复用节点组合
4. **可验证**: 内置图验证（DAG 检查、节点引用检查等）
5. **读写追踪**: 每个节点声明 reads/writes，便于依赖分析

---

## 🎓 学习路径

1. **初级**: 理解 AgentNode, FnNode, Study, Edge 基础
2. **中级**: 掌握 GateNode, ForkNode, JoinNode 控制流
3. **高级**: 学习 SubgraphForkNode, SelectionNode 并行实验
4. **专家**: 设计自定义 workflow，理解继承和子图复用

---

**Happy Workflow Building! 🚀**
