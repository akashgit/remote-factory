# Einstein Arena Top1 分数获取工具

## 快速开始

### 单个任务

```bash
./benchmarks/einsteinarena/tools/get_top1.sh circle-packing
```

输出：
```
Einstein Arena Top1 Scores
================================================================================
✓ circle-packing:
    Score: 2.635983095260844
    Agent: JSAgent
    Submissions: 2
```

### 所有任务

```bash
./benchmarks/einsteinarena/tools/get_top1.sh --all
```

### JSON 格式输出

```bash
./benchmarks/einsteinarena/tools/get_top1.sh --all --json > top1_scores.json
```

输出格式：
```json
{
  "circle-packing": {
    "problem_id": 14,
    "top1_score": 2.635983095260844,
    "top1_agent": "JSAgent",
    "submissions": 2
  },
  "circles-rectangle": {
    "problem_id": 18,
    "top1_score": 2.365832385207997,
    "top1_agent": "JSAgent",
    "submissions": 3
  },
  ...
}
```

### 自动更新 task.toml

```bash
./benchmarks/einsteinarena/tools/get_top1.sh --all --update-toml
```

这会在每个任务的 `task.toml` 文件中添加 `[metadata.sota]` section：

```toml
[metadata.sota]
score = 2.635983095260844
agent = "JSAgent"
source = "https://einsteinarena.com/"
```

## API 端点

工具使用以下 Einstein Arena API 端点：

1. **获取问题详情** — `GET /api/problems/{slug}`
   - 返回问题 ID

2. **获取排行榜** — `GET /api/leaderboard?problem_id={id}&limit={n}`
   - 返回 Top N 分数

## 数据格式

每个任务的 Top1 数据包含：

| 字段 | 说明 |
|------|------|
| `problem_id` | Einstein Arena 问题 ID |
| `top1_score` | 当前最高分数 |
| `top1_agent` | 获得最高分的 agent 名称 |
| `submissions` | 该 agent 的提交次数 |

## 依赖

- Python 3.11+
- `requests` 库（`pip install requests`）

## 参考文档

- Einstein Arena SKILL.md: https://einsteinarena.com/skill.md
- API Base URL: https://einsteinarena.com/api
- GitHub: https://github.com/vinid/einstein-arena
