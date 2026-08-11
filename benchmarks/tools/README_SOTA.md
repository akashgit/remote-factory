# Einstein Arena SOTA 更新工具

## 推荐使用

```bash
python3 benchmarks/tools/add_sota_to_instruction.py --all
```

## 功能

只更新 `instruction.md`（agent 能看到的文件），添加：

```markdown
## State of the Art

**Current best score:** 2.635983095260844
**Updated:** 2026-08-11

**Minimum improvement:** Your score must improve by at least 1e-10 to be considered meaningful.
```

## 设计原则

1. **只更新 instruction.md** — task.toml 对 agent 不可见，没必要更新
2. **只保留关键信息** — score + updated，不需要 agent 名称和 source
3. **添加 minImprovement** — 告诉 agent 改进阈值（原数据缺失）

## 字段说明

- **Current best score**: 当前排行榜第一名的分数
- **Updated**: 数据更新日期
- **Minimum improvement**: 最小有意义改进量（从 API 获取）
  - 如果 = 0（整数问题），则不显示此字段

## 对比其他工具

| 工具 | 更新 instruction.md | 更新 task.toml | Agent 可见 | 推荐 |
|------|---------------------|----------------|-----------|------|
| `add_sota_to_instruction.py` | ✅ | ❌ | ✅ | ✅ 推荐 |
| `update_sota.py` | ✅ | ✅ | 部分 | ⚠️ 冗余 |
| `get_einsteinarena_top1.py --update-toml` | ❌ | ✅ | ❌ | ❌ 废弃 |

## 示例输出

```
Processing: circle-packing
  Best score: 2.635983095260844
  Min improvement: 1e-10
  Status: ✓ updated
```

---

**创建日期**: 2026-08-11  
**版本**: 3.0（简化版）
