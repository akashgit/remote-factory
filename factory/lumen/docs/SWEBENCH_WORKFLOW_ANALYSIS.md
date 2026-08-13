╔══════════════════════════════════════════════════════════════╗
║    🔍 SWE-bench Workflow 深度解析                          ║
╚══════════════════════════════════════════════════════════════╝

文件：factory/workflow/contributed/swebench/workflow.py
总行数：167 行
类型：Contributed Benchmark Workflow

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 什么是 SWE-bench？

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**SWE-bench（Software Engineering Benchmark）**是一个评测 AI 代码能力的
标准化基准测试，专门用于测试 AI 能否：

✅ 理解真实的 GitHub issue
✅ 分析现有代码库
✅ 定位 bug 根源
✅ 实现正确的修复
✅ 通过项目的测试套件

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 典型的 SWE-bench 任务

**示例任务**：
```
Repository: django/django
Issue: #12345 - QuerySet.filter() with Q objects raises TypeError

Description:
When using Q objects with multiple conditions in QuerySet.filter(),
a TypeError is raised in Django 3.2. The issue occurs when combining
Q objects with the | operator.

Steps to reproduce:
1. Create a model with multiple fields
2. Try: Model.objects.filter(Q(a=1) | Q(b=2))
3. Observe: TypeError: unsupported operand type(s) for |

Expected: Should return combined queryset
Actual: Raises TypeError
```

**任务文件**：`/tmp/task-instruction.md`

**评测方式**：
- ✅ Builder 实现修复
- ✅ 运行 Django 的测试套件
- ✅ Harbor 容器验证（官方评测环境）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 Workflow 核心设计

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 设计哲学

**极简主义（Minimalist）**：
- ❌ 不使用 .factory/ 基础设施
- ❌ 不运行 factory eval
- ❌ 不做深度 QA（no deep-qa subgraph）
- ❌ 不创建实验记录
- ✅ 只做一件事：修复 bug

**为什么极简？**
1. **速度优先**：benchmark 需要快速评测
2. **容器友好**：Harbor 环境资源受限
3. **标准化**：专注于核心任务（fix bug）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4 节点流程

```
study → builder → gate_verify → auto_merge
         ↑            ↓
         └───── RELOOP ──────┘
```

**节点职责**：
1. **study** (FnNode) - 收集代码库信息
2. **builder** (AgentNode) - 实现修复
3. **gate_verify** (GateNode-fn) - 验证修复
4. **auto_merge** (FnNode) - 合并到主分支

**RELOOP 机制**：
- gate_verify 检测测试失败 → RELOOP 到 builder
- 最多 3 次迭代（max_iterations=3）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔍 逐节点深度解析

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Node 1: Study（信息收集）

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
        "find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50 && "
        "echo '\\n=== Configuration Files ===' && "
        "ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true && "
        "echo '\\n=== Task Instruction ===' && "
        "cat /tmp/task-instruction.md 2>/dev/null || "
        "echo 'No task instruction file found at /tmp/task-instruction.md'"
        ") > .factory/reviews/study-output.md 2>&1"
    ),
    writes={".factory/reviews/study-output.md"},
)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**含义**：一个 Shell 脚本，自动收集项目信息

**执行步骤**：

**Step 1**: 创建目录
```bash
mkdir -p {project_path}/.factory/reviews
```
- 确保输出目录存在
- 即使没有完整 .factory/ 基础设施也能工作

**Step 2**: 列出 Python 文件（前 200 个）
```bash
find . -type f -name '*.py' | head -200
```
- 展示代码库结构
- 限制 200 个文件（避免信息过载）

**Step 3**: 列出测试文件（前 50 个）
```bash
find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50
```
- 找到所有测试文件
- 两种命名模式：`test_*.py` 和 `*_test.py`

**Step 4**: 列出配置文件
```bash
ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true
```
- 检查项目配置
- `2>/dev/null` - 忽略错误输出
- `|| true` - 即使文件不存在也继续

**Step 5**: 读取任务描述
```bash
cat /tmp/task-instruction.md
```
- Harbor 容器中任务描述的标准位置
- 包含完整的 GitHub issue 内容

**输出**：`.factory/reviews/study-output.md`

**示例输出**：
```markdown
=== Repository Structure ===
./django/core/models.py
./django/db/query.py
./django/db/models/query.py
... (200 files)

=== Test Files ===
./tests/test_query.py
./tests/models/test_filter.py
... (50 files)

=== Configuration Files ===
-rw-r--r-- 1 user user 1234 setup.py
-rw-r--r-- 1 user user  567 pyproject.toml
-rw-r--r-- 1 user user  890 tox.ini

=== Task Instruction ===
# Issue #12345: QuerySet.filter() with Q objects raises TypeError

Description:
When using Q objects with multiple conditions...
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Node 2: Builder（实现修复）

```python
nodes["builder"] = AgentNode(
    id="builder",
    role=AgentRole.BUILDER,
    model="opus",
    timeout=7200,
    max_iterations=3,
    prompt_template=(
        "You are fixing a bug in an open-source project for the SWE-bench benchmark.\n\n"
        "## Your Task\n\n"
        "1. **Read the task instruction** — Read /tmp/task-instruction.md for the full "
        "bug description and task requirements.\n\n"
        "2. **Understand the codebase** — explore the repository structure. "
        "Read relevant source files, test files, and configuration. "
        "Identify the root cause of the bug described in the task.\n\n"
        "3. **Implement the fix** — make the MINIMAL change that resolves the "
        "issue. Do NOT refactor, modernize, or add unrelated improvements. "
        "Fix ONLY the described bug.\n\n"
        "4. **Run the project's own tests** — this is CRITICAL. Run the test "
        "suite to verify your fix works AND existing tests still pass. "
        "Use pytest, tox, or whatever test runner the project uses. "
        "If specific test files are mentioned in the task, run those first.\n\n"
        "5. **Commit your changes** — commit directly on the current branch "
        "with a descriptive message referencing the issue. Do NOT create a "
        "new branch. Do NOT create a PR.\n\n"
        "## Rules\n\n"
        "- MINIMAL fix only — smallest diff that resolves the issue\n"
        "- MUST run tests before committing — never commit untested code\n"
        "- Do NOT create branches or PRs — commit on current branch\n"
        "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
        "- Do NOT modify test files unless the bug is IN the test infrastructure\n"
        "- If tests fail after your fix, investigate and fix the issue\n"
    ),
    reads={".factory/reviews/study-output.md"},
    writes={".factory/reviews/builder-latest.md"},
)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**含义**：Spawn Builder agent 来实现修复

**关键参数**：

**model="opus"**
- 使用 Claude Opus（最强模型）
- SWE-bench 任务复杂，需要高推理能力

**timeout=7200**
- 2 小时超时（7200 秒）
- 比标准 Builder (1200s) 长 6 倍
- 原因：需要时间理解复杂代码库

**max_iterations=3**
- 最多 3 次重试
- 配合 RELOOP 机制

**prompt_template 解析**：

**任务 1: 读取任务描述**
```
Read /tmp/task-instruction.md
```
- Harbor 容器的标准位置
- 包含完整的 GitHub issue

**任务 2: 理解代码库**
```
explore the repository structure
Read relevant source files, test files, and configuration
Identify the root cause
```
- 不是盲目修改
- 要求理解根本原因

**任务 3: 实现修复**
```
make the MINIMAL change
Do NOT refactor, modernize, or add unrelated improvements
Fix ONLY the described bug
```
- **极简原则**：最小化修改
- 不做额外优化
- 专注 bug 修复

**为什么强调 MINIMAL？**
1. 减少引入新 bug 的风险
2. 更容易验证修复的正确性
3. SWE-bench 评测标准：只修复问题

**任务 4: 运行测试**
```
Run the test suite to verify your fix works
Use pytest, tox, or whatever test runner the project uses
```
- **关键步骤**：必须运行测试
- 使用项目自己的测试工具
- 验证修复有效且不破坏现有功能

**任务 5: 提交修改**
```
commit directly on the current branch
Do NOT create a new branch
Do NOT create a PR
```
- 直接提交到当前分支
- 不创建 PR（benchmark 不需要）

**禁止规则**：

```
- Do NOT run factory commands
- Do NOT modify test files
- Do NOT create branches or PRs
```

**为什么禁止？**
- factory 命令在 Harbor 容器中不可用
- 测试文件是评测标准，不能修改
- 分支/PR 不是 benchmark 流程的一部分

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Builder 工作流示例**：

1. 读取 `/tmp/task-instruction.md`：
   ```
   Issue: QuerySet.filter() with Q objects raises TypeError
   ```

2. 分析代码：
   ```python
   # Read django/db/models/query.py
   # Find the filter() method
   # Identify the bug in Q object handling
   ```

3. 实现修复：
   ```python
   # django/db/models/query.py
   def filter(self, *args, **kwargs):
       # OLD: broken Q object logic
       # NEW: fixed Q object logic
       ...
   ```

4. 运行测试：
   ```bash
   pytest tests/models/test_filter.py -v
   ```

5. 提交：
   ```bash
   git add django/db/models/query.py
   git commit -m "Fix QuerySet.filter() TypeError with Q objects (#12345)"
   ```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Node 3: Gate Verify（验证修复）

```python
nodes["gate_verify"] = GateNode(
    id="gate_verify",
    evaluator_type="fn",
    evaluator_command=(
        "cd {project_path} && "
        "CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && "
        "if [ \"$CHANGES\" = 'NO_COMMITS' ] || [ -z \"$CHANGES\" ]; then "
        "echo 'fail: builder did not commit any changes'; "
        "exit 0; fi && "
        "BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && "
        "if echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then "
        "echo 'pass: builder reports tests passing'; "
        "elif echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(fail|error|FAILED)'; then "
        "echo 'reloop: builder needs to retry — tests did not pass'; "
        "else "
        "echo 'pass: changes committed, no issues detected'; "
        "fi"
    ),
    reads={".factory/reviews/builder-latest.md"},
)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**含义**：验证 Builder 的修复是否有效

**验证步骤**：

**Step 1: 检查是否有提交**
```bash
CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS')
if [ "$CHANGES" = 'NO_COMMITS' ] || [ -z "$CHANGES" ]; then
    echo 'fail: builder did not commit any changes'
    exit 0
fi
```

**逻辑**：
- `git diff HEAD~1 --stat` - 对比最新提交和上一个提交
- 如果没有提交或没有变更 → 失败
- Builder 必须提交修改才算有效

**为什么重要？**
- 确保 Builder 实际执行了修复
- 防止 Builder "说修复了但没做"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Step 2: 读取 Builder 输出**
```bash
BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '')
```

**含义**：
- 读取 Builder 的完整输出
- 用于检查测试结果

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Step 3: 检查测试状态**

**Case 1: 测试通过**
```bash
if echo "$BUILDER_OUTPUT" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then
    echo 'pass: builder reports tests passing'
```

**模式匹配**：
- `grep -qiE` - 大小写不敏感的正则匹配
- `'tests?.*(pass|succeed|ok|PASSED)'` - 匹配多种成功表达
- 示例匹配：
  * "All tests passed"
  * "Test succeed"
  * "Tests OK"
  * "pytest PASSED"

**输出**：`pass: builder reports tests passing`
- 包含 "pass" → GateNode 返回 PROCEED verdict
- 继续到 auto_merge

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Case 2: 测试失败**
```bash
elif echo "$BUILDER_OUTPUT" | grep -qiE 'tests?.*(fail|error|FAILED)'; then
    echo 'reloop: builder needs to retry — tests did not pass'
```

**模式匹配**：
- `'tests?.*(fail|error|FAILED)'`
- 示例匹配：
  * "Test failed"
  * "Tests error"
  * "pytest FAILED"

**输出**：`reloop: builder needs to retry — tests did not pass`
- 包含 "reloop" → GateNode 返回 RELOOP verdict
- 回到 builder 节点重试
- 最多 3 次（max_iterations=3）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Case 3: 无法确定（默认通过）**
```bash
else
    echo 'pass: changes committed, no issues detected'
fi
```

**含义**：
- Builder 提交了修改
- 输出中没有明确的测试失败信息
- 假定通过（乐观策略）

**为什么乐观？**
- Builder 被要求必须运行测试
- 如果没有报告失败，可能测试通过了
- 后续 Harbor 验证会最终判定

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Verdict 映射**：

输出包含      | Verdict    | 下一步
-------------|-----------|------------------
"pass"       | PROCEED   | → auto_merge
"reloop"     | RELOOP    | → builder (重试)
"fail"       | HALT      | 停止 workflow

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Node 4: Auto Merge（自动合并）

```python
nodes["auto_merge"] = FnNode(
    id="auto_merge",
    command=(
        "cd {project_path} && "
        "CURRENT=$(git rev-parse --abbrev-ref HEAD) && "
        "COMMON=$(git rev-parse --git-common-dir) && "
        "BASE=$(git --git-dir=\"$COMMON\" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main) && "
        "if [ \"$CURRENT\" = \"$BASE\" ]; then "
        "echo \"Already on $BASE — no merge needed\"; "
        "exit 0; fi && "
        "git update-ref refs/heads/\"$BASE\" HEAD && "
        "PARENT_WT=$(cd \"$COMMON/..\" && pwd) && "
        "git diff-tree --no-commit-id --name-only -r HEAD HEAD~1 | "
        "while read file; do "
        "if [ -f \"$file\" ]; then "
        "mkdir -p \"$PARENT_WT/$(dirname $file)\" && "
        "cp \"$file\" \"$PARENT_WT/$file\"; "
        "fi; done && "
        "echo \"Updated $BASE to $(git rev-parse --short HEAD)\""
    ),
    reads={".factory/reviews/builder-latest.md"},
)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**含义**：将修复合并到主分支

**为什么需要这一步？**

**Harbor 容器的特殊环境**：
- Builder 可能在 worktree 中工作（隔离的 git 副本）
- Harbor 验证器检查 **主分支** 的变更
- 需要将 worktree 的修改同步回主分支

**执行步骤**：

**Step 1: 获取当前分支**
```bash
CURRENT=$(git rev-parse --abbrev-ref HEAD)
```
- 示例：`worktree-branch-1`

**Step 2: 获取共享 git 目录**
```bash
COMMON=$(git rev-parse --git-common-dir)
```
- Worktree 的共享 git 目录
- 示例：`/path/to/repo/.git`

**Step 3: 获取主分支名**
```bash
BASE=$(git --git-dir="$COMMON" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)
```
- 尝试获取原始仓库的当前分支
- 失败则默认 `main`

**Step 4: 检查是否需要合并**
```bash
if [ "$CURRENT" = "$BASE" ]; then
    echo "Already on $BASE — no merge needed"
    exit 0
fi
```
- 如果已经在主分支，跳过

**Step 5: 更新主分支引用**
```bash
git update-ref refs/heads/"$BASE" HEAD
```
- 将主分支指向当前 HEAD
- 不需要实际合并，直接移动指针

**Step 6: 复制文件到父 worktree**
```bash
PARENT_WT=$(cd "$COMMON/.." && pwd)
git diff-tree --no-commit-id --name-only -r HEAD HEAD~1 |
while read file; do
    if [ -f "$file" ]; then
        mkdir -p "$PARENT_WT/$(dirname $file)"
        cp "$file" "$PARENT_WT/$file"
    fi
done
```

**逻辑**：
1. 找到父 worktree 目录
2. 列出最新提交修改的文件
3. 逐个复制到父 worktree

**为什么复制文件？**
- Worktree 是独立的工作目录
- 修改只在 worktree 中可见
- 需要同步回原始仓库

**Step 7: 输出确认**
```bash
echo "Updated $BASE to $(git rev-parse --short HEAD)"
```
- 示例：`Updated main to a1b2c3d`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔄 RELOOP 机制详解

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 边定义

```python
edges = [
    Edge(source="study", target="builder"),
    Edge(source="builder", target="gate_verify"),
    Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
    Edge(source="gate_verify", target="builder", condition=VerdictType.RELOOP),  ← 关键
]
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 流程图

```
study
  ↓
builder (iteration 1)
  ↓
gate_verify
  ├─ 测试通过 (PROCEED) → auto_merge → 结束
  └─ 测试失败 (RELOOP) → builder (iteration 2)
                            ↓
                         gate_verify
                            ├─ 通过 → auto_merge
                            └─ 失败 → builder (iteration 3)
                                        ↓
                                     gate_verify
                                        ├─ 通过 → auto_merge
                                        └─ 失败 → 超过最大迭代，HALT
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 实际案例

**Iteration 1**：
```
Builder: 修复了 Q 对象的 __or__ 方法
         运行 pytest
         结果：FAILED - 2 tests failed

gate_verify: 检测到 "tests FAILED"
             输出 "reloop: builder needs to retry"
             → RELOOP to builder
```

**Iteration 2**：
```
Builder: 分析失败原因
         发现还需要修改 __and__ 方法
         实现额外修复
         运行 pytest
         结果：PASSED - all tests passed

gate_verify: 检测到 "tests PASSED"
             输出 "pass: builder reports tests passing"
             → PROCEED to auto_merge
```

**Iteration 3 (如果需要)**：
```
如果 iteration 2 仍然失败，继续重试
max_iterations=3 限制最多 3 次
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 与标准 Workflow 的对比

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

特性                  | Improve Workflow      | SWE-bench Workflow
---------------------|-----------------------|--------------------
节点数量             | 15+ 节点              | 4 节点
.factory/ 基础设施   | ✅ 完整               | ❌ 最小化
Eval 评分            | ✅ eval_before/after  | ❌ 不使用
Deep-QA              | ✅ 3 个 QA agents     | ❌ 不使用
Archivist            | ✅ 强制归档           | ❌ 不归档
CEO Gates            | ✅ 多个 CEO 审查      | ❌ 无 CEO gates
User Gates           | ❌ 无                 | ❌ 无
Fn Gates             | ✅ precheck           | ✅ gate_verify
RELOOP 机制          | ✅ gate_qa → builder  | ✅ gate_verify → builder
实验记录             | ✅ results.tsv        | ❌ 不记录
Builder Model        | opus (default)        | opus (explicit)
Builder Timeout      | 1200s (20 min)        | 7200s (2 hours)
Headless 兼容        | ❌ (有 CEO gates)     | ✅ (只有 fn gates)
Container 优化       | ❌                    | ✅ Harbor 专用
任务来源             | backlog/research      | /tmp/task-instruction.md
合并策略             | PR workflow           | 直接提交 + auto_merge

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🐳 Harbor 容器集成

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 什么是 Harbor？

**Harbor** 是 SWE-bench 的官方评测环境：
- 标准化的 Docker 容器
- 预装项目依赖
- 隔离的测试环境
- 自动化验证流程

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Harbor 特殊约定

**1. 任务文件位置**
```
/tmp/task-instruction.md
```
- Harbor 自动挂载
- 包含 GitHub issue 全文

**2. 主分支检查**
```bash
# Harbor 验证器检查 main 分支
git checkout main
pytest tests/
```
- 所以需要 auto_merge 步骤

**3. 资源限制**
- CPU: 2-4 cores
- Memory: 4-8GB
- Timeout: 通常 2-4 小时

**4. 评测标准**
```
✅ 通过条件：
   - 修复了描述的 bug
   - 所有现有测试通过
   - 没有引入新的失败

❌ 失败条件：
   - 测试仍然失败
   - 破坏了现有功能
   - 超时
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 Trigger 条件

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```python
def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
    return ctx.get("mode") == "swebench"
```

**含义**：
- 只有显式指定 `--mode swebench` 才触发
- 不依赖项目状态（state）

**调用方式**：
```bash
# Interactive mode
factory ceo /path/to/repo --mode swebench

# Headless mode
factory workflow run swebench /path/to/repo
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎓 设计亮点

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1️⃣ 极简主义

**只做一件事**：修复 bug
- 不做评分
- 不做深度 QA
- 不做实验跟踪

**好处**：
- 速度快
- 资源占用少
- 容易调试

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2️⃣ 自包含 Study

**传统 Study**：
```bash
factory study /path  # 复杂的 Python 程序
```

**SWE-bench Study**：
```bash
find . -name '*.py' | head -200
cat /tmp/task-instruction.md
```

**好处**：
- 不依赖 factory CLI 的复杂逻辑
- 容器友好
- 可预测的输出

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3️⃣ 智能验证

**多层验证**：
1. 检查是否有提交（防止无作为）
2. 解析测试输出（快速反馈）
3. RELOOP 机制（允许重试）
4. Harbor 最终验证（权威评测）

**渐进式质量保证**：
```
本地快速检查 → RELOOP 修复 → Harbor 权威验证
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4️⃣ Worktree 兼容

**auto_merge 处理复杂的 git 场景**：
- 检测 worktree 环境
- 同步修改到主分支
- 复制文件到父仓库

**适配多种运行模式**：
- 直接在主分支工作
- 在 worktree 中工作
- 在 Harbor 容器中工作

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🚀 实际使用场景

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 1: 本地测试

```bash
# 1. 准备任务描述
echo "Fix bug in module.py" > /tmp/task-instruction.md

# 2. 运行 workflow
factory workflow run swebench /path/to/project

# 3. 检查结果
git log -1  # 查看修复提交
git diff HEAD~1  # 查看修改内容
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 2: Harbor 容器评测

```bash
# Harbor 自动执行：
docker run harbor-swebench/django \
  factory workflow run swebench /workspace

# Harbor 验证：
pytest tests/  # 在 main 分支运行
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 3: Benchmark 批量评测

```python
# 评测脚本
for task in swebench_tasks:
    result = run_workflow("swebench", task.repo)
    results.append({
        "task_id": task.id,
        "passed": verify_fix(task),
        "time": result.duration,
    })
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 💡 总结

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### SWE-bench Workflow 的本质

**它是一个**：
✅ 极简的 bug 修复流水线
✅ 标准化的 benchmark 评测工具
✅ 容器优化的自动化系统

**它不是**：
❌ 完整的软件工程工厂
❌ 实验跟踪系统
❌ 代码质量提升工具

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 核心设计原则

1. **极简主义**：只做必要的事
2. **容器优先**：适配 Harbor 环境
3. **自包含**：不依赖复杂基础设施
4. **可重试**：RELOOP 机制提高成功率
5. **验证严格**：多层验证确保质量

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4 节点的职责分工

```
study        → 收集信息（项目结构 + 任务描述）
builder      → 实现修复（理解 + 编码 + 测试）
gate_verify  → 验证修复（检查提交 + 解析测试结果）
auto_merge   → 同步修改（处理 worktree + 更新主分支）
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 为什么这个设计很优秀？

**简洁**：4 个节点完成完整流程
**高效**：2 小时内完成大多数任务
**可靠**：RELOOP 机制 + 多层验证
**通用**：适用于任何 Python 项目的 bug 修复

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

这就是 SWE-bench Workflow 的完整解析！🎉
