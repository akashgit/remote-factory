# SOTA 信息更新指南

## 重要说明

Einstein Arena 的 SOTA（State-of-the-Art）信息应该**同时更新两个文件**：

### ✅ instruction.md — Agent 能看到

Agent 需要知道当前最好的分数才能有优化目标！

```markdown
## Current Best Score (SOTA)

**Score:** 2.635983095260844
**Agent:** JSAgent
**Source:** https://einsteinarena.com/
**Updated:** 2026-08-11

Your goal is to match or exceed this score.
```

### ✅ task.toml — 外部工具能看到

虽然 agent 看不到，但外部工具（dashboard、分析脚本）需要结构化数据。

```toml
[metadata.sota]
score = 2.635983095260844
agent = "JSAgent"
source = "https://einsteinarena.com/"
updated = "2026-08-11"
```

---

## 为什么要同时更新？

### 问题：原先的设计

❌ **旧脚本**（`get_einsteinarena_top1.py --update-toml`）：
- 只更新 `task.toml`
- Agent **看不到** task.toml
- SOTA 信息对 agent 优化任务**毫无帮助**

### 解决方案：新设计

✅ **新脚本**（`update_sota.py`）：
- 同时更新 `instruction.md` 和 `task.toml`
- Agent 能看到目标分数
- 外部工具也能读取结构化数据

---

## 使用方法

### 单个任务

```bash
python3 benchmarks/einsteinarena/tools/update_sota.py circle-packing
```

输出：
```
Processing: circle-packing
  SOTA: 2.635983095260844 (by JSAgent)
  instruction.md: ✓ updated
  task.toml:      ✓ updated
```

### 所有任务

```bash
python3 benchmarks/einsteinarena/tools/update_sota.py --all
```

---

## 文件变更示例

### instruction.md 变更

**添加位置**：文件末尾

```diff
 ## Scoring Direction
 
 **MAXIMIZE**
 
 The verifier will evaluate your solution and return a numerical score.
+
+
+## Current Best Score (SOTA)
+
+**Score:** 2.635983095260844
+**Agent:** JSAgent
+**Source:** https://einsteinarena.com/
+**Updated:** 2026-08-11
+
+Your goal is to match or exceed this score.
```

### task.toml 变更

**添加位置**：`[metadata]` section 内

```diff
 [metadata]
 difficulty = "hard"
 category = "mathematics"
 tags = ["maximize", "optimization"]
+
+[metadata.sota]
+score = 2.635983095260844
+agent = "JSAgent"
+source = "https://einsteinarena.com/"
+updated = "2026-08-11"
 
 [environment]
```

---

## 幂等性

脚本是幂等的：

**第一次运行：**
```
instruction.md: ✓ updated
task.toml:      ✓ updated
```

**第二次运行：**
```
INFO: circle-packing instruction.md already has SOTA section
INFO: circle-packing task.toml already has [metadata.sota]
instruction.md: ✓ updated  (更新现有内容)
task.toml:      ○ skipped   (不重复添加)
```

**行为：**
- `instruction.md`：更新现有 SOTA section 的分数和日期
- `task.toml`：如果已存在则跳过（避免重复）

---

## 对比旧脚本

| 特性 | get_einsteinarena_top1.py --update-toml | update_sota.py |
|------|----------------------------------------|----------------|
| 更新 instruction.md | ❌ 不更新 | ✅ 更新 |
| 更新 task.toml | ✅ 更新 | ✅ 更新 |
| Agent 能看到 SOTA | ❌ 不能 | ✅ 能 |
| 外部工具能读取 | ✅ 能 | ✅ 能 |
| 推荐使用 | ❌ 已废弃 | ✅ 推荐 |

---

## 完整示例

```bash
# 1. 更新单个任务
python3 benchmarks/einsteinarena/tools/update_sota.py circle-packing

# 2. 查看 agent 能看到的内容
cat benchmarks/einsteinarena/circle-packing/instruction.md | tail -10

# 输出：
# ## Current Best Score (SOTA)
#
# **Score:** 2.635983095260844
# **Agent:** JSAgent
# **Source:** https://einsteinarena.com/
# **Updated:** 2026-08-11
#
# Your goal is to match or exceed this score.

# 3. 模拟 agent 看到的任务（Harbor 传递方式）
cat benchmarks/einsteinarena/circle-packing/instruction.md > /tmp/task-instruction.md
factory ceo /testbed --focus "$(cat /tmp/task-instruction.md)"

# Agent 现在知道目标是 2.636！
```

---

## 设计原理

### Agent 的视角

Agent 收到的 prompt：

```
You are tasked with solving the following optimization problem:

<task-description>
Pack 26 circles in a square to maximize the sum of radii...

## Current Best Score (SOTA)
**Score:** 2.635983095260844
**Agent:** JSAgent

Your goal is to match or exceed this score.
</task-description>

Generate solution.json with your optimized packing.
```

Agent **能看到**目标分数 → 可以优化策略 → 更有可能超越 SOTA

### 外部工具的视角

Dashboard/分析脚本读取 task.toml：

```python
config = parse_toml("task.toml")
sota_score = config["metadata"]["sota"]["score"]  # 2.636
sota_agent = config["metadata"]["sota"]["agent"]  # "JSAgent"

# 用于排行榜、进度跟踪等
```

---

## 常见问题

### Q1: 为什么不只更新 instruction.md？

A: 因为外部工具需要结构化数据（TOML 格式）而不是 Markdown。两者各有用途：
- instruction.md：给 agent 看（人类可读）
- task.toml：给程序看（机器可读）

### Q2: 旧脚本还能用吗？

A: 能用，但**不推荐**。旧脚本 `get_einsteinarena_top1.py --update-toml` 只更新 task.toml，agent 看不到 SOTA，失去了更新的意义。

### Q3: 如果 SOTA 分数更新了怎么办？

A: 重新运行脚本。instruction.md 会更新现有的 SOTA section，task.toml 会被跳过（已存在）。

### Q4: 可以手动编辑吗？

A: 可以！格式很简单：

**instruction.md（末尾添加）：**
```markdown
## Current Best Score (SOTA)
**Score:** 2.636
**Agent:** YourAgent
**Updated:** 2026-08-11
Your goal is to match or exceed this score.
```

**task.toml（[metadata] 内添加）：**
```toml
[metadata.sota]
score = 2.636
agent = "YourAgent"
updated = "2026-08-11"
```

---

## 总结

✅ **正确做法**：使用 `update_sota.py` 同时更新两个文件  
❌ **错误做法**：只更新 task.toml（agent 看不到）

**核心原则**：Agent 需要看到目标才能优化！

---

**创建日期**: 2026-08-11  
**脚本版本**: 2.0（同时更新两个文件）
