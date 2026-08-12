# --update-toml 功能详解

## 功能说明

`--update-toml` 标志会自动在每个 Einstein Arena 任务的 `task.toml` 文件中添加 **SOTA (State-of-the-Art)** 信息。

## 添加的内容

在 `[metadata]` section 内添加一个子 section：

```toml
[metadata.sota]
score = 2.635983095260844
agent = "JSAgent"
source = "https://einsteinarena.com/"
```

### 字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| `score` | 当前 Einstein Arena 排行榜的 Top1 分数 | `2.635983095260844` |
| `agent` | 获得 Top1 的 agent 名称 | `"JSAgent"` |
| `source` | 数据来源 URL | `"https://einsteinarena.com/"` |

## 使用方法

### 单个任务

```bash
./benchmarks/einsteinarena/tools/get_top1.sh circle-packing --update-toml
```

输出：
```
Einstein Arena Top1 Scores
================================================================================
✓ circle-packing:
    Score: 2.635983095260844
    Agent: JSAgent
    Submissions: 2
    TOML: ✓ updated
```

### 所有任务

```bash
./benchmarks/einsteinarena/tools/get_top1.sh --all --update-toml
```

输出示例：
```
✓ circle-packing:
    Score: 2.635983095260844
    Agent: JSAgent
    Submissions: 2
    TOML: ✓ updated

✓ circles-rectangle:
    Score: 2.365832385207997
    Agent: JSAgent
    Submissions: 3
    TOML: ✓ updated

INFO: kissing-number-d11-605 already has [metadata.sota], skipping
✓ kissing-number-d11-605:
    Score: 605.0
    Agent: AlphaEvolve
    Submissions: 2
    TOML: ○ skipped
```

## 文件变更示例

### 更新前

```toml
schema_version = "1.3"

[task]
name = "einsteinarena/circle-packing"
description = "Circle Packing in a Square"
authors = ["Einstein Arena"]
keywords = ["einsteinarena", "maximize", "mathematics"]

[metadata]
difficulty = "hard"
category = "mathematics"
tags = ["maximize", "optimization"]

[environment]
network_mode = "none"
...
```

### 更新后

```toml
schema_version = "1.3"

[task]
name = "einsteinarena/circle-packing"
description = "Circle Packing in a Square"
authors = ["Einstein Arena"]
keywords = ["einsteinarena", "maximize", "mathematics"]

[metadata]
difficulty = "hard"
category = "mathematics"
tags = ["maximize", "optimization"]

[metadata.sota]
score = 2.635983095260844
agent = "JSAgent"
source = "https://einsteinarena.com/"

[environment]
network_mode = "none"
...
```

### Git diff

```diff
@@ -11,6 +11,11 @@ difficulty = "hard"
 category = "mathematics"
 tags = ["maximize", "optimization"]
 
+[metadata.sota]
+score = 2.635983095260844
+agent = "JSAgent"
+source = "https://einsteinarena.com/"
+
 [environment]
 network_mode = "none"
```

## 幂等性保证

脚本是**幂等的**：多次运行不会重复添加。

第一次运行：
```bash
$ ./benchmarks/einsteinarena/tools/get_top1.sh circle-packing --update-toml
✓ circle-packing:
    TOML: ✓ updated
```

第二次运行：
```bash
$ ./benchmarks/einsteinarena/tools/get_top1.sh circle-packing --update-toml
INFO: circle-packing already has [metadata.sota], skipping
✓ circle-packing:
    TOML: ○ skipped
```

## 恢复方法

如果需要撤销更新：

### 单个文件

```bash
git checkout benchmarks/einsteinarena/circle-packing/task.toml
```

### 所有文件

```bash
git checkout benchmarks/einsteinarena/*/task.toml
```

## 技术实现

脚本执行以下步骤：

1. **获取问题 ID**
   ```python
   problem_id = requests.get(f"{API_BASE}/problems/{slug}").json()["id"]
   ```

2. **获取排行榜 Top1**
   ```python
   leaderboard = requests.get(
       f"{API_BASE}/leaderboard",
       params={"problem_id": problem_id, "limit": 1}
   ).json()
   top1_score = leaderboard[0]["bestScore"]
   top1_agent = leaderboard[0]["agentName"]
   ```

3. **定位 `[metadata]` section 结束位置**
   - 找到 `[metadata]` 行
   - 找到下一个 `[xxx]` section 开始位置
   - 在两者之间插入 `[metadata.sota]`

4. **插入 SOTA 信息**
   ```python
   sota_lines = [
       "",
       "[metadata.sota]",
       f"score = {top1_score}",
       f'agent = "{top1_agent}"',
       f'source = "https://einsteinarena.com/"',
   ]
   ```

5. **写回文件**

## 注意事项

1. **网络依赖**：需要访问 `https://einsteinarena.com/api`
2. **格式要求**：task.toml 必须有标准的 `[metadata]` section
3. **只读操作**：脚本只修改 `task.toml`，不影响其他文件
4. **跳过已更新**：如果 `[metadata.sota]` 已存在，自动跳过

## 用途

- **基准比较**：记录当前 SOTA 分数，便于后续比较 agent 性能
- **目标设定**：为 agent 提供明确的优化目标
- **进度跟踪**：监控 agent 是否接近或超越 SOTA
- **元数据丰富**：为 Harbor benchmark 添加更多上下文信息

## 完整示例

```bash
# 1. 查看当前状态
cat benchmarks/einsteinarena/circle-packing/task.toml | grep -A 3 "\[metadata\]"

# 输出：
# [metadata]
# difficulty = "hard"
# category = "mathematics"
# tags = ["maximize", "optimization"]

# 2. 更新 SOTA 信息
./benchmarks/einsteinarena/tools/get_top1.sh circle-packing --update-toml

# 输出：
# ✓ circle-packing:
#     Score: 2.635983095260844
#     Agent: JSAgent
#     Submissions: 2
#     TOML: ✓ updated

# 3. 验证更新
cat benchmarks/einsteinarena/circle-packing/task.toml | grep -A 7 "\[metadata\]"

# 输出：
# [metadata]
# difficulty = "hard"
# category = "mathematics"
# tags = ["maximize", "optimization"]
#
# [metadata.sota]
# score = 2.635983095260844
# agent = "JSAgent"
# source = "https://einsteinarena.com/"
```

---

**创建日期**: 2026-08-11  
**脚本版本**: 1.1（已修复重复字段 bug）  
**测试状态**: ✅ 通过
