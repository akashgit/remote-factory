╔══════════════════════════════════════════════════════════════╗
║    📖 Design Workflow 逐句深度解析                         ║
╚══════════════════════════════════════════════════════════════╝

文件：factory/workflow/definitions.py
函数：design_workflow() (行 446-505)
总行数：60 行

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📝 完整源代码

```python
446  def design_workflow() -> Workflow:
447      """W₂: Design Mode — W₁ with user gate at strategy approval.
448  
449      W₂ = W₁[gate_strategy ← GateNode(user), +gate_has_factory, +study]
450  
451      Existing projects (HAS_FACTORY) route through study before research.
452      New/partial projects route through discover → study → fork_research.
453      """
454      wf = build_workflow()
455  
456      # Conditional entry: existing projects get study, new projects skip it
457      wf.nodes["gate_has_factory"] = GateNode(
458          id="gate_has_factory",
459          evaluator_type="fn",
460          evaluator_command=(
461              'python3 -c "'
462              'from pathlib import Path; '
463              'exists = Path(\"{project_path}/.factory/config.json\").exists(); '
464              'print(\"PROCEED\" if exists else \"HALT\")'
465              '"'
466          ),
467          reads={".factory/config.json"},
468      )
469  
470      wf.nodes["discover"] = FnNode(
471          id="discover",
472          command="factory discover {project_path}",
473          writes={".factory/eval_profile.json"},
474      )
475  
476      wf.nodes["study"] = Study(
477          id="study",
478          command="factory study {project_path}",
479          writes={".factory/strategy/observations.md"},
480      )
481  
482      wf.edges.extend([
483          Edge(source="gate_has_factory", target="study", condition=VerdictType.PROCEED),
484          Edge(source="gate_has_factory", target="discover", condition=VerdictType.HALT),
485          Edge(source="discover", target="study"),
486          Edge(source="study", target="fork_research"),
487      ])
488  
489      wf.start_node = "gate_has_factory"
490  
491      wf.nodes["gate_strategy"] = GateNode(
492          id="gate_strategy",
493          evaluator_type="user",
494          reads={".factory/strategy/current.md"},
495      )
496  
497      wf.name = "design"
498  
499      def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
500          return state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE, ProjectState.HAS_FACTORY} and ctx.get(
501              "interactive", False
502          )
503  
504      wf.trigger = trigger
505      return wf
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 逐句解析

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 446-453: 函数定义和文档字符串

```python
446  def design_workflow() -> Workflow:
```

**含义**：
- 定义 design_workflow 函数
- 返回类型：`Workflow` 对象

**用途**：
- 创建 Design Mode 的 DAG 定义
- 被 workflow registry 调用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
447      """W₂: Design Mode — W₁ with user gate at strategy approval.
```

**含义**：
- W₂ = Design workflow 的编号
- W₁ = Build workflow（基础 workflow）
- "with user gate at strategy approval" = 在策略批准环节添加用户审批

**设计模式**：
- Design 是 Build 的变体
- 核心区别：用户参与决策

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
449      W₂ = W₁[gate_strategy ← GateNode(user), +gate_has_factory, +study]
```

**含义**：数学公式表示 Design 与 Build 的关系

**解读**：
- `W₁[...]` - 从 Build workflow 开始
- `gate_strategy ← GateNode(user)` - 替换 gate_strategy 为 user gate
- `+gate_has_factory` - 添加新的 gate 节点
- `+study` - 添加新的 study 节点

**Build vs Design 的关键差异**：
```
Build:
  gate_strategy → GateNode(evaluator_type="agent", role=CEO)

Design:
  gate_strategy → GateNode(evaluator_type="user")  ← 人工批准！
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
451      Existing projects (HAS_FACTORY) route through study before research.
452      New/partial projects route through discover → study → fork_research.
```

**含义**：两种入口路径

**路径 1（现有项目）**：
```
gate_has_factory [PROCEED] → study → fork_research
```
- 已有 `.factory/config.json`
- 直接 study 代码库
- 跳过 discover（因为已经配置好了）

**路径 2（新项目）**：
```
gate_has_factory [HALT] → discover → study → fork_research
```
- 没有 `.factory/config.json`
- 先 discover（生成 eval profile）
- 再 study（分析代码）
- 然后进入研究阶段

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 454: 继承 Build Workflow

```python
454      wf = build_workflow()
```

**含义**：
- 调用 `build_workflow()` 函数
- 获取完整的 Build workflow 对象
- 作为 Design workflow 的基础

**Build workflow 包含什么？**
- fork_research (3 个并行 researchers)
- join_research
- strategist
- gate_strategy (CEO gate - 将被覆盖)
- apply_spec_diff
- begin
- builder
- gate_build
- deep_qa subgraph (health_checker, code_reviewer, adversarial_tester, gate_review)
- gate_doc_freshness
- gate_precheck
- finalize
- archivist

**继承的好处**：
- DRY（Don't Repeat Yourself）
- 只需修改差异部分
- 保持一致性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 456-468: 添加条件入口 Gate

```python
456      # Conditional entry: existing projects get study, new projects skip it
457      wf.nodes["gate_has_factory"] = GateNode(
```

**注释解读**：
- "Conditional entry" = 分支入口
- "existing projects get study" = 现有项目先 study
- "new projects skip it" = 新项目跳过 study（实际上是先 discover）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
458          id="gate_has_factory",
459          evaluator_type="fn",
```

**含义**：
- 节点 ID：`gate_has_factory`
- 评估器类型：`"fn"` = 运行 shell 命令

**Gate 类型回顾**：
- `"fn"` - 运行命令，根据输出决策
- `"agent"` - CEO agent 审查
- `"user"` - 人工批准

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
460          evaluator_command=(
461              'python3 -c "'
462              'from pathlib import Path; '
463              'exists = Path(\"{project_path}/.factory/config.json\").exists(); '
464              'print(\"PROCEED\" if exists else \"HALT\")'
465              '"'
466          ),
```

**含义**：执行一段 Python 代码检查文件是否存在

**代码逐行解读**：

1. `python3 -c "..."` - 运行内联 Python 代码
2. `from pathlib import Path` - 导入路径库
3. `exists = Path("{project_path}/.factory/config.json").exists()` 
   - `{project_path}` 会被替换为实际项目路径
   - 检查 `.factory/config.json` 是否存在
4. `print("PROCEED" if exists else "HALT")`
   - 存在 → 输出 "PROCEED"
   - 不存在 → 输出 "HALT"

**Verdict 映射**：
- 输出包含 "PROCEED" → VerdictType.PROCEED
- 输出包含 "HALT" → VerdictType.HALT

**为什么用 Python 而不是 shell？**
```bash
# Shell 版本（可能不跨平台）
[ -f {project_path}/.factory/config.json ] && echo PROCEED || echo HALT

# Python 版本（跨平台）
python3 -c "from pathlib import Path; ..."
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
467          reads={".factory/config.json"},
```

**含义**：
- 声明这个节点读取 `.factory/config.json`
- 用于依赖分析和文档生成
- 实际检查由 `evaluator_command` 执行

**reads vs writes**：
- `reads` - 节点读取的文件
- `writes` - 节点写入的文件
- 用于 DAG 验证和可视化

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 470-474: 添加 Discover 节点

```python
470      wf.nodes["discover"] = FnNode(
471          id="discover",
472          command="factory discover {project_path}",
473          writes={".factory/eval_profile.json"},
474      )
```

**含义**：
- 添加 `discover` FnNode
- 运行 `factory discover` 命令
- 生成 eval profile（评测维度配置）

**Discover 命令做什么？**
1. 检测项目语言和框架（Python? Node.js? Go?）
2. 分析项目结构（有测试吗？有 lint 吗？）
3. 生成 `EvalProfile` 对象
4. 写入 `.factory/eval_profile.json`

**EvalProfile 示例**：
```json
{
  "dimensions": [
    {"name": "test_coverage", "weight": 0.3, "command": "pytest --cov"},
    {"name": "type_safety", "weight": 0.2, "command": "mypy ."},
    {"name": "lint_score", "weight": 0.2, "command": "ruff check ."}
  ]
}
```

**何时运行？**
- 仅当 `gate_has_factory` 返回 HALT
- 即：新项目没有 `.factory/config.json`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 476-480: 添加 Study 节点

```python
476      wf.nodes["study"] = Study(
477          id="study",
478          command="factory study {project_path}",
479          writes={".factory/strategy/observations.md"},
480      )
```

**含义**：
- 添加 `study` Study 节点
- `Study` 是 `FnNode` 的子类（特殊标记）
- 运行 `factory study` 命令
- 生成 observations.md

**Study 节点 vs FnNode**：
```python
class Study(FnNode):
    focus: str | None = None
```
- 本质上是 FnNode
- 语义标记："这是代码分析节点"
- 支持 `focus` 参数（聚焦特定主题）

**Study 命令做什么？**
1. 读取 `.factory/config.json` 和 `eval_profile.json`
2. 分析代码库结构
3. 查看 git 历史和实验记录
4. 生成 observations.md：
   - 项目概况
   - 评分弱点
   - 待办事项 (backlog)
   - 假设预算 (Hypothesis Budget)

**Observations.md 示例**：
```markdown
# Project Observations

## Overview
- Python CLI tool, 2k LOC
- pytest, mypy, ruff configured

## Eval Scores
- test_coverage: 0.65 (weak)
- type_safety: 0.85 (strong)
- lint_score: 0.90 (strong)

## Backlog
1. Add tests for auth module
2. Document CLI flags

## Hypothesis Budget
Max hypotheses: 3
Growth ratio: 50%
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 482-487: 定义条件边

```python
482      wf.edges.extend([
```

**含义**：
- 向 workflow 的 edges 列表添加新边
- `extend()` = 批量添加

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
483          Edge(source="gate_has_factory", target="study", condition=VerdictType.PROCEED),
```

**含义**：条件边 #1

**图示**：
```
gate_has_factory --[PROCEED]--> study
```

**何时触发？**
- `gate_has_factory` 检测到 `.factory/config.json` 存在
- 返回 PROCEED verdict
- 进入 `study` 节点

**场景**：现有项目
```
factory ceo /path/to/existing-project --mode design
```
1. 检查 `.factory/config.json` → 存在
2. gate_has_factory → PROCEED
3. 直接进入 study
4. 跳过 discover

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
484          Edge(source="gate_has_factory", target="discover", condition=VerdictType.HALT),
```

**含义**：条件边 #2

**图示**：
```
gate_has_factory --[HALT]--> discover
```

**何时触发？**
- `gate_has_factory` 检测到 `.factory/config.json` 不存在
- 返回 HALT verdict
- 进入 `discover` 节点

**场景**：新项目
```
factory ceo "Build a weather CLI" --mode design
```
1. 创建新项目目录
2. 没有 `.factory/config.json`
3. gate_has_factory → HALT
4. 进入 discover
5. 生成 eval profile

**为什么用 HALT 而不是 REDIRECT？**
- HALT 表示"正常停止，但有出口"
- 通过 condition=HALT 的边定义出口
- 不是错误，是分支选择

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
485          Edge(source="discover", target="study"),
```

**含义**：无条件边

**图示**：
```
discover --> study
```

**何时触发？**
- `discover` 节点完成后
- 无条件进入 `study` 节点

**路径 2 完整流程**：
```
gate_has_factory [HALT] → discover → study
```

**为什么 discover 后必须 study？**
- Discover 只生成 eval profile（技术配置）
- Study 生成 observations（业务洞察）
- 两者都是后续策略所需

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
486          Edge(source="study", target="fork_research"),
```

**含义**：无条件边，连接到 Build workflow 的节点

**图示**：
```
study --> fork_research
```

**为什么指向 fork_research？**
- `fork_research` 是 Build workflow 中的节点
- Design 继承了 Build 的所有节点
- `fork_research` 启动 3 个并行 researcher

**完整的两条路径汇合**：
```
路径 1: gate_has_factory [PROCEED] → study → fork_research
路径 2: gate_has_factory [HALT] → discover → study → fork_research
```

**fork_research 之后（继承自 Build）**：
```
fork_research
  ├─→ researcher_similar ┐
  ├─→ researcher_techstack ┼→ join_research → strategist → ...
  └─→ researcher_pitfalls ┘
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
487      ])
```

**含义**：边列表结束

**总共添加了 4 条边**：
1. gate_has_factory --[PROCEED]--> study
2. gate_has_factory --[HALT]--> discover
3. discover --> study
4. study --> fork_research

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 489: 设置起始节点

```python
489      wf.start_node = "gate_has_factory"
```

**含义**：
- 设置 workflow 的起始节点
- 覆盖 Build workflow 的 start_node

**Build workflow 的 start_node**：
```python
# build_workflow() 中
wf.start_node = "fork_research"
```

**Design workflow 的 start_node**：
```python
# design_workflow() 中
wf.start_node = "gate_has_factory"  ← 覆盖！
```

**为什么要改？**
- Design 需要先判断项目类型（新/旧）
- Build 直接进入研究阶段

**执行流程对比**：
```
Build:   fork_research → join → strategist → ...
Design:  gate_has_factory → [study OR discover→study] → fork_research → ...
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 491-495: 覆盖 gate_strategy 为 User Gate

```python
491      wf.nodes["gate_strategy"] = GateNode(
492          id="gate_strategy",
493          evaluator_type="user",
494          reads={".factory/strategy/current.md"},
495      )
```

**含义**：
- 覆盖 Build workflow 中的 `gate_strategy` 节点
- 从 CEO gate 改为 user gate

**Build workflow 的 gate_strategy**：
```python
# build_workflow() 中
GateNode(
    id="gate_strategy",
    evaluator_type="agent",
    evaluator_role=AgentRole.CEO,
    gate_prompt="HARD GATE. Check: specific enough to implement? ..."
)
```

**Design workflow 的 gate_strategy**：
```python
# design_workflow() 中
GateNode(
    id="gate_strategy",
    evaluator_type="user",  ← 人工批准！
    reads={".factory/strategy/current.md"}
)
```

**关键差异**：
| Build Mode | Design Mode |
|------------|-------------|
| CEO 自动批准策略 | 你手动批准策略 |
| 快速迭代 | 人工控制 |
| 适合已有项目 | 适合新项目/讨论阶段 |

**User gate 的交互流程**：
1. Strategist 生成假设 → `.factory/strategy/current.md`
2. WorkflowExecutor 遇到 user gate → 暂停
3. 在 terminal 中显示提示：
   ```
   Review strategy at .factory/strategy/current.md
   Approve? [y/n]
   ```
4. 你审查策略，决定批准或拒绝
5. 批准 → PROCEED → 继续执行
6. 拒绝 → HALT → workflow 终止

**为什么 Design 需要 user gate？**
- Design mode 用于讨论和头脑风暴
- 你需要参与决策："这个方向对吗？"
- 不是自动化执行，是协作式开发

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 497: 设置 Workflow 名称

```python
497      wf.name = "design"
```

**含义**：
- 覆盖 Build workflow 的 name
- 设置为 `"design"`

**用途**：
- Workflow registry 中的唯一标识符
- 命令行调用：`factory ceo /path --mode design`
- 日志和报告中显示的名称

**Build vs Design**：
```python
# build_workflow()
wf.name = "build"

# design_workflow()
wf.name = "design"
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 499-502: 定义触发条件

```python
499      def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
500          return state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE, ProjectState.HAS_FACTORY} and ctx.get(
501              "interactive", False
502          )
```

**含义**：
- 定义一个 `trigger()` 函数
- 决定何时自动选择这个 workflow

**参数**：
- `state: ProjectState` - 项目状态枚举
- `ctx: dict[str, Any]` - 上下文字典

**ProjectState 枚举值**：
```python
class ProjectState(str, Enum):
    NO_REPO = "no_repo"              # 没有 git repo
    REPO_INCOMPLETE = "repo_incomplete"  # 有 repo，无 .factory/
    HAS_FACTORY = "has_factory"      # 有 .factory/config.json
    HAS_EXPERIMENTS = "has_experiments"  # 有实验历史
    RESEARCH_MODE = "research_mode"  # 研究模式
```

**条件解读**：

**Part 1**: `state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE, ProjectState.HAS_FACTORY}`

含义：状态是以下三种之一
- `NO_REPO` - 全新项目，没有 git
- `REPO_INCOMPLETE` - 有 git，但没配置 factory
- `HAS_FACTORY` - 配置了 factory

**Part 2**: `and ctx.get("interactive", False)`

含义：上下文中 `interactive` 为 True

**什么是 interactive？**
```python
# 命令行调用
factory ceo /path --mode design  # ctx["interactive"] = True
factory ceo /path --mode build   # ctx["interactive"] = False
```

**完整逻辑**：
```python
design_workflow 触发条件：
  (NO_REPO or REPO_INCOMPLETE or HAS_FACTORY) AND interactive=True
```

**为什么需要 interactive=True？**
- Design mode 有 user gate
- Headless mode（`factory workflow run design`）无法处理 user gate
- 必须在 interactive mode (CEO orchestration) 中运行

**场景对比**：

场景 1：新项目 + design mode
```bash
factory ceo "Build a weather CLI" --mode design
```
- state = NO_REPO
- interactive = True
- ✅ 触发 design_workflow

场景 2：现有项目 + design mode
```bash
factory ceo /path/to/project --mode design
```
- state = HAS_FACTORY
- interactive = True
- ✅ 触发 design_workflow

场景 3：Headless mode
```bash
factory workflow run design /path
```
- interactive = False
- ❌ 不触发 design_workflow（即使状态匹配）

场景 4：Build mode
```bash
factory ceo /path --mode build
```
- ctx["mode"] = "build" (不是 design)
- ❌ 不触发 design_workflow
- ✅ 触发 build_workflow

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 504: 注册触发函数

```python
504      wf.trigger = trigger
```

**含义**：
- 将 `trigger()` 函数赋值给 `wf.trigger`
- Workflow 对象的 `trigger` 属性

**Workflow 类定义**：
```python
class Workflow(BaseModel):
    name: str
    nodes: dict[str, NodeType]
    edges: list[Edge]
    start_node: str
    trigger: TriggerFn | None = None  ← 这里
```

**TriggerFn 类型**：
```python
TriggerFn = Callable[[ProjectState, dict[str, Any]], bool]
```

**用途**：
- Workflow registry 自动选择 workflow
- `factory ceo /path` 不指定 mode 时，自动匹配

**自动选择逻辑**：
```python
# factory/workflow/primitives.py
def select_workflow(
    self, state: ProjectState, context: dict[str, Any] | None = None
) -> Workflow | None:
    ctx = context or {}
    for wf in self.workflows.values():
        if wf.trigger and wf.trigger(state, ctx):
            return wf
    return None
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 行 505: 返回 Workflow

```python
505      return wf
```

**含义**：
- 返回修改后的 Workflow 对象

**wf 对象包含什么？**
1. **继承自 Build workflow 的节点**：
   - fork_research, join_research
   - 3 个 researcher nodes
   - strategist
   - apply_spec_diff
   - begin, builder, gate_build
   - deep_qa subgraph (health_checker, code_reviewer, adversarial_tester, gate_review)
   - gate_doc_freshness, gate_precheck
   - finalize, archivist

2. **Design 新增/覆盖的节点**：
   - gate_has_factory (新增)
   - discover (新增)
   - study (新增)
   - gate_strategy (覆盖为 user gate)

3. **边**：
   - Build 的所有边
   - 4 条新边（gate_has_factory 相关）

4. **元数据**：
   - name = "design"
   - start_node = "gate_has_factory"
   - trigger = trigger()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 完整流程图

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 新项目流程（NO_REPO）

```
START
  ↓
gate_has_factory (检查 .factory/config.json)
  ↓ [HALT - 不存在]
discover (生成 eval_profile.json)
  ↓
study (生成 observations.md)
  ↓
fork_research (并行启动 3 个 researcher)
  ├─→ researcher_similar
  ├─→ researcher_techstack
  └─→ researcher_pitfalls
  ↓
join_research (等待所有 researcher 完成)
  ↓
strategist (生成假设 → current.md)
  ↓
gate_strategy (USER GATE - 你批准策略)
  ↓ [PROCEED]
apply_spec_diff (应用 SPEC diff)
  ↓
begin (开始实验)
  ↓
builder (实现假设)
  ↓
gate_build (CEO 审查实现)
  ↓ [PROCEED]
health_checker (健康检查)
  ↓
code_reviewer (代码审查)
  ↓
gate_review (CEO 审查 QA)
  ↓ [PROCEED]
adversarial_tester (对抗性测试)
  ↓
gate_doc_freshness (检查文档)
  ↓
gate_precheck (运行 precheck)
  ↓ [PROCEED]
finalize (结束实验，记录结果)
  ↓
archivist (归档知识)
  ↓
END
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 现有项目流程（HAS_FACTORY）

```
START
  ↓
gate_has_factory (检查 .factory/config.json)
  ↓ [PROCEED - 存在]
study (生成 observations.md)
  ↓
fork_research
  ... (同上)
```

**差异**：跳过 discover 步骤

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 Design vs Build 对比表

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

特性                | Build Mode            | Design Mode
-------------------|----------------------|------------------------
基础关系            | 基础 workflow         | 继承 Build + 修改
起始节点            | fork_research         | gate_has_factory
条件入口            | ❌ 无                 | ✅ gate_has_factory
Discover 步骤       | ❌ 跳过              | ✅ 新项目需要
Study 步骤          | ❌ 跳过              | ✅ 必须
策略批准            | CEO auto-approve      | USER manual approve
适用场景            | 已有想法，快速构建    | 新想法，讨论阶段
交互模式            | 可 headless           | 必须 interactive
触发条件            | 所有状态              | NO_REPO/INCOMPLETE/HAS_FACTORY + interactive
User Gate 数量      | 0                     | 1 (gate_strategy)
主要用途            | 自动化构建            | 协作式设计

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 💡 设计模式与最佳实践

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1️⃣ Workflow 继承模式

```python
def design_workflow() -> Workflow:
    wf = build_workflow()  ← 继承
    
    # 添加新节点
    wf.nodes["new_node"] = ...
    
    # 覆盖旧节点
    wf.nodes["existing_node"] = ...
    
    # 修改元数据
    wf.start_node = "new_start"
    wf.name = "design"
    
    return wf
```

**优点**：
- DRY（不重复代码）
- 保持一致性
- 易于维护

**应用**：
- design ← build
- research ← improve
- review ← deep-qa

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2️⃣ 条件入口模式

```python
# 检查状态
gate_has_factory → fn gate (检查文件)
  ├─ PROCEED → 路径 A (现有项目)
  └─ HALT → 路径 B (新项目)

# 路径汇合
路径 A → study → fork_research
路径 B → discover → study → fork_research
```

**优点**：
- 同一个 workflow 处理多种场景
- 避免代码重复
- 灵活性高

**应用**：
- design (新/旧项目)
- discover (不同语言)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3️⃣ User Gate 模式

```python
GateNode(
    evaluator_type="user",
    reads={".factory/strategy/current.md"}
)
```

**何时使用**：
- 需要人工决策
- 方向性选择（不是质量检查）
- 协作式开发

**何时避免**：
- 自动化流水线
- Headless mode
- 需要快速迭代

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4️⃣ Fn Gate 模式

```python
GateNode(
    evaluator_type="fn",
    evaluator_command="python3 -c '...'"
)
```

**何时使用**：
- 确定性检查（文件存在、环境变量等）
- 快速决策
- 跨平台兼容

**最佳实践**：
- 优先用 Python（跨平台）
- 输出明确（PROCEED/HALT/RELOOP）
- 避免副作用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎓 关键要点总结

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **Design = Build + 人工审批**
   - 继承 Build 的所有节点
   - 覆盖 gate_strategy 为 user gate
   - 添加条件入口（gate_has_factory）

2. **两条路径**
   - 新项目：gate → discover → study → research
   - 旧项目：gate → study → research
   - 最终汇合到 fork_research

3. **User Gate 的作用**
   - 让你参与策略决策
   - 适合头脑风暴和讨论阶段
   - 不适合自动化执行

4. **Trigger 函数**
   - 自动选择 workflow
   - 必须 interactive=True
   - 支持 3 种项目状态

5. **继承模式的威力**
   - 60 行代码定义完整 workflow
   - 复用 Build 的 200+ 行代码
   - 只修改差异部分

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

希望这个逐句解析帮助你深入理解 Design Workflow 的每一个细节！🎉
