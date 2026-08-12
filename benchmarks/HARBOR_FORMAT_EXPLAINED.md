# Harbor Benchmark 格式详解

## 文件结构

每个 Harbor 任务包含 4 个文件：

```
benchmarks/<benchmark>-harbor/<task-name>/
├── instruction.md          # 任务描述（给 agent 看的）
├── task.toml              # 元数据和配置（给 harness 看的）
├── environment/
│   └── Dockerfile         # 运行环境定义
└── tests/
    └── test.sh            # 验证脚本
```

---

## 1. instruction.md — 任务说明书

### 作用

**给 agent 阅读的任务描述。** 这是 agent 唯一能看到的任务信息。

### 内容

包含问题描述、约束条件、预期输出格式、评分标准等所有 agent 需要知道的信息。

### Einstein Arena 示例

```markdown
## Problem

Pack $n = 26$ non-overlapping circles inside the unit square $[0, 1]^2$ 
to **maximize** the sum of their radii

$$S = \sum_{i=1}^{26} r_i$$

Each circle has center $(x_i, y_i)$ and radius $r_i > 0$. Constraints:

- **Containment:** $r_i \le x_i$, ...
- **Non-overlap:** $\|\mathbf{c}_i - \mathbf{c}_j\| \ge r_i + r_j$ ...

## Scoring

Submit `circles` — an array of exactly 26 triples $[x, y, r]$. 
The score is the sum of all radii if the packing is valid, 
$-\infty$ otherwise. Higher is better.

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "circles": // array of [x, y, r] triples
}
```

## Scoring Direction

**MAXIMIZE**

The verifier will evaluate your solution and return a numerical score.
```

### SWE-bench 风格示例（tomswe）

```markdown
The export feature is broken for some records. When users download 
their data, certain rows come out garbled. It works fine most of 
the time though.

## User Profile
You are working with a data engineer who has these preferences:
- **Verbosity:** verbose — likes to understand the full picture
- **Testing:** pytest, always test edge cases with special characters
- **Code style:** use the csv module from stdlib, type hints
- **Git:** descriptive commit messages explaining the why
- **Data handling:** never silently drop or modify data
```

### 传递方式

Harbor harness 将 `instruction.md` 内容写入容器的 `/tmp/task-instruction.md`：

```python
# factory_harbor_agent.py
command = f"cat > /tmp/task-instruction.md << 'INSTREOF'\n{instruction}\nINSTREOF"
```

然后通过 `--focus` 参数传递给 factory CLI：

```bash
factory ceo /testbed \
  --mode <mode> \
  --focus "$(cat /tmp/task-instruction.md)"
```

Agent 在执行时读取这个文件作为任务描述。

---

## 2. task.toml — 任务元数据

### 作用

**给 Harbor harness 读取的配置文件。** Agent 不会看到这个文件。

### 内容

包含任务元数据、资源限制、超时设置、环境变量等 harness 运行时需要的配置。

### 完整示例

```toml
schema_version = "1.3"

# ─────────────────────────────────────────────────────────
# [task] — 任务基本信息
# ─────────────────────────────────────────────────────────
[task]
name = "einsteinarena/circle-packing"           # 任务唯一标识
description = "Circle Packing in a Square"      # 简短描述
authors = ["Einstein Arena"]                    # 作者/来源
keywords = ["einsteinarena", "maximize", "mathematics"]

# ─────────────────────────────────────────────────────────
# [metadata] — 分类和标签
# ─────────────────────────────────────────────────────────
[metadata]
difficulty = "hard"                             # 难度: easy/medium/hard
category = "mathematics"                        # 类别: programming/mathematics/...
tags = ["maximize", "optimization"]             # 标签

# ─────────────────────────────────────────────────────────
# [environment] — 容器资源限制
# ─────────────────────────────────────────────────────────
[environment]
network_mode = "none"                           # 网络访问: none/public
build_timeout_sec = 900.0                       # Docker build 超时（秒）
cpus = 2                                        # CPU 核心数
memory_mb = 4096                                # 内存限制（MB）
storage_mb = 10240                              # 磁盘空间（MB）
gpus = 0                                        # GPU 数量
mcp_servers = []                                # MCP 服务器列表

[environment.env]                               # 环境变量（可选）

# ─────────────────────────────────────────────────────────
# [agent] — Agent 运行配置
# ─────────────────────────────────────────────────────────
[agent]
timeout_sec = 7200.0                            # Agent 超时（秒）

# ─────────────────────────────────────────────────────────
# [verifier] — 验证脚本配置
# ─────────────────────────────────────────────────────────
[verifier]
timeout_sec = 600.0                             # Verifier 超时（秒）

[verifier.env]                                  # Verifier 环境变量（可选）

# ─────────────────────────────────────────────────────────
# [solution.env] — Solution 环境变量（可选）
# ─────────────────────────────────────────────────────────
[solution.env]
```

### 可选扩展（SOTA 信息）

可以添加 `[metadata.sota]` subsection 记录当前最佳分数：

```toml
[metadata.sota]
score = 2.635983095260844
agent = "JSAgent"
source = "https://einsteinarena.com/"
```

### 字段详解

| Section | Field | 说明 | 示例 |
|---------|-------|------|------|
| `[task]` | `name` | 任务唯一标识（通常是 `<benchmark>/<task-id>`） | `"einsteinarena/circle-packing"` |
| `[task]` | `description` | 简短描述（一句话） | `"Circle Packing in a Square"` |
| `[task]` | `authors` | 作者列表 | `["Einstein Arena"]` |
| `[task]` | `keywords` | 关键词列表 | `["maximize", "mathematics"]` |
| `[metadata]` | `difficulty` | 难度等级 | `"easy"`, `"medium"`, `"hard"` |
| `[metadata]` | `category` | 任务类别 | `"programming"`, `"mathematics"` |
| `[metadata]` | `tags` | 标签数组 | `["optimize", "geometry"]` |
| `[environment]` | `network_mode` | 网络访问权限 | `"none"`, `"public"` |
| `[environment]` | `cpus` | CPU 核心数 | `2` |
| `[environment]` | `memory_mb` | 内存限制（MB） | `4096` |
| `[environment]` | `gpus` | GPU 数量 | `0`, `1`, `2` |
| `[agent]` | `timeout_sec` | Agent 最大运行时间 | `7200.0` (2 小时) |
| `[verifier]` | `timeout_sec` | Verifier 最大运行时间 | `600.0` (10 分钟) |

---

## 文件角色对比

| 特性 | instruction.md | task.toml |
|------|----------------|-----------|
| **读者** | Agent（Claude/GPT/...） | Harbor harness（Python） |
| **格式** | Markdown（人类可读） | TOML（机器可读） |
| **内容** | 任务描述、约束、输出格式 | 元数据、资源限制、超时 |
| **可见性** | Agent 能看到（通过 `--focus`） | Agent 看不到 |
| **作用** | 告诉 agent "做什么" | 告诉 harness "怎么运行" |
| **示例** | "Pack 26 circles to maximize..." | `timeout_sec = 7200.0` |

---

## 完整执行流程

### 1. Harbor harness 读取 task.toml

```python
# 读取配置
config = parse_toml("benchmarks/einsteinarena-harbor/circle-packing/task.toml")

# 应用资源限制
container_config = {
    "cpus": config["environment"]["cpus"],          # 2
    "memory": config["environment"]["memory_mb"],   # 4096 MB
    "timeout": config["agent"]["timeout_sec"],      # 7200 秒
}
```

### 2. 启动容器并写入 instruction.md

```python
# 读取任务描述
instruction = read_file("benchmarks/einsteinarena-harbor/circle-packing/instruction.md")

# 写入容器
exec_in_container(
    "cat > /tmp/task-instruction.md << 'INSTREOF'\n" +
    instruction + "\nINSTREOF"
)
```

### 3. 调用 factory CLI

```bash
factory ceo /testbed \
  --mode einsteinarena \
  --focus "$(cat /tmp/task-instruction.md)"
```

### 4. Agent 读取任务

Agent（Claude）收到的 prompt：

```
You are tasked with solving the following problem:

<task-description>
$(cat /tmp/task-instruction.md)
</task-description>

Generate a solution.json file with the required format.
```

### 5. 验证输出

```bash
# 运行 tests/test.sh
cd /workspace
./tests/test.sh

# 检查 solution.json
# 计算分数
# 写入 score.txt
```

---

## 设计原则

### instruction.md

1. **完整性**：包含 agent 需要的所有信息
2. **清晰性**：使用标题、列表、代码块组织信息
3. **精确性**：明确约束条件、输出格式、评分标准
4. **示例**：提供输入/输出示例（如果适用）

### task.toml

1. **标准化**：使用 schema_version = "1.3"
2. **最小化**：只包含 harness 需要的配置
3. **合理性**：资源限制应该适合任务复杂度
   - 简单任务：`timeout_sec = 1800` (30 分钟)
   - 复杂任务：`timeout_sec = 7200` (2 小时)
4. **安全性**：默认 `network_mode = "none"`（除非需要外网）

---

## 对比其他 Benchmark 格式

### Einstein Arena

- **instruction.md**：纯数学问题描述 + JSON schema
- **task.toml**：`network_mode = "none"`（离线运行）
- **输出**：`solution.json`（数据文件）
- **验证**：Python verifier 函数

### SWE-bench / tomswe

- **instruction.md**：bug 描述 + 用户偏好
- **task.toml**：`network_mode = "public"`（可能需要安装包）
- **输出**：代码修改（git diff）
- **验证**：pytest 测试套件

### ProgramBench

- **instruction.md**：逆向工程目标 + 规格说明
- **task.toml**：更长的超时（`timeout_sec = 10800`）
- **输出**：C 源码
- **验证**：编译 + 功能测试

---

## 常见问题

### Q1: Agent 能看到 task.toml 吗？

**不能。** Agent 只能看到 `instruction.md` 的内容（通过 `--focus` 参数）。

### Q2: task.toml 里的 description 和 instruction.md 有什么区别？

- `task.toml` 的 `description`：一句话简短描述（用于 UI 展示、日志）
- `instruction.md`：完整的任务说明书（agent 实际阅读的内容）

### Q3: 可以在 instruction.md 里提到资源限制吗？

**可以，但不推荐。** Agent 不需要知道容器有 4GB 内存——这些是 harness 的责任。只在必要时告诉 agent（例如："优化内存使用"）。

### Q4: 如果我想添加自定义字段到 task.toml？

可以添加自定义 section，例如：

```toml
[metadata.custom]
baseline_score = 1.5
difficulty_rating = 8.5
```

Harbor harness 会忽略未识别的字段，但可以被自定义工具读取。

---

## 参考

- **Schema**: Harbor format v1.3
- **示例**: `benchmarks/einsteinarena-harbor/`, `benchmarks/tomswe-harbor/`
- **代码**: `benchmarks/factory_harbor_agent.py`
- **工具**: `benchmarks/tools/extract_einstein_arena.py`

---

**创建日期**: 2026-08-11  
**版本**: 1.0
