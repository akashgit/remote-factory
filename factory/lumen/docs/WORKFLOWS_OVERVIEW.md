# Remote Factory Workflows 总览

本文档整理了 remote-factory 中所有 30 个 workflows 的分类和说明。

> 最后更新：2026-08-07

## 📊 概览

- **总数**：30 个 workflows
- **核心定义文件**：`factory/workflow/definitions.py` (4560 行)
- **Contributed workflows**：`factory/workflow/contributed/*/workflow.py`

---

## 🎯 核心开发 Workflows (10 个)

### 1. build (20 nodes, 27 edges)
- **用途**：从想法/spec 构建新项目
- **起点**：`fork_research` (3个并行研究者)
- **定义**：`factory/workflow/definitions.py::build_workflow()`

### 2. improve (19 nodes, 24 edges)
- **用途**：改进现有项目（最常用的主要工作流）
- **起点**：`study`
- **定义**：`factory/workflow/definitions.py::improve_workflow()`

### 3. design (23 nodes, 31 edges)
- **用途**：交互式设计模式
- **起点**：`gate_has_factory`
- **定义**：`factory/workflow/definitions.py::design_workflow()`

### 4. research (21 nodes, 27 edges)
- **用途**：研究模式改进
- **起点**：`baseline`
- **定义**：`factory/workflow/definitions.py::research_workflow()`

### 5. founder (5 nodes, 5 edges)
- **用途**：快速原型模式（跳过 review）
- **起点**：`study`
- **定义**：`factory/workflow/definitions.py::founder_workflow()`
- **特点**：最简单的 workflow，适合快速实验

### 6. parallel-improve (19 nodes, 22 edges)
- **用途**：并行改进模式
- **起点**：`study`
- **定义**：`factory/workflow/definitions.py::parallel_improve_workflow()`

### 7. refine (15 nodes, 18 edges)
- **用途**：精化单个变更请求
- **起点**：`refiner`
- **定义**：`factory/workflow/definitions.py::refine_workflow()`

### 8. plan (12 nodes, 16 edges)
- **用途**：纯规划模式（无实现）
- **起点**：`check_prior_plans`
- **定义**：`factory/workflow/definitions.py::plan_workflow()`

### 9. discover (3 nodes, 3 edges)
- **用途**：发现 eval 维度
- **起点**：`discover`
- **定义**：`factory/workflow/definitions.py::discover_workflow()`
- **特点**：最简单的核心 workflow

### 10. review (8 nodes, 8 edges)
- **用途**：Review 模式
- **起点**：`eval_test`
- **定义**：`factory/workflow/definitions.py::review_workflow()`

---

## 🧪 Benchmark Workflows (8 个)

所有 benchmark workflows 都有 4 个节点，结构相似，用于测试和评估。

### 11. swebench
- **用途**：SWE-bench 标准测试
- **定义**：`factory/workflow/contributed/swebench/workflow.py`

### 12. swebenchifyhard
- **用途**：SWE-bench hard 变体
- **定义**：`factory/workflow/contributed/swebenchifyhard/workflow.py`

### 13. featurebench
- **用途**：Feature benchmark 测试
- **定义**：`factory/workflow/contributed/featurebench/workflow.py`

### 14. terminalbench
- **用途**：Terminal 任务 benchmark
- **定义**：`factory/workflow/contributed/terminalbench/workflow.py`

### 15. programbench
- **用途**：程序构建 benchmark
- **定义**：`factory/workflow/contributed/programbench/workflow.py`

### 16. legacybench
- **用途**：Legacy 代码 benchmark
- **定义**：`factory/workflow/contributed/legacybench/workflow.py`

### 17. tomswe
- **用途**：Tom's SWE benchmark
- **定义**：`factory/workflow/contributed/tomswe/workflow.py`

### 18. salitrap
- **用途**：SaliTrap benchmark
- **定义**：`factory/workflow/contributed/salitrap/workflow.py`

---

## 🎨 前端设计 Workflows (3 个)

### 19. frontend-design (26 nodes, 40 edges)
- **用途**：前端设计系统实现
- **起点**：`gate_design_system`
- **定义**：`factory/workflow/definitions.py::frontend_design_workflow()`
- **特点**：最复杂的 workflow

### 20. frontend-design-discover (11 nodes, 16 edges)
- **用途**：前端设计发现
- **起点**：`fork_discover_research`
- **定义**：`factory/workflow/definitions.py::frontend_design_discover_workflow()`

### 21. frontend-design-scan (17 nodes, 24 edges)
- **用途**：前端设计扫描
- **起点**：`fork_scan_research`
- **定义**：`factory/workflow/definitions.py::frontend_design_scan_workflow()`

---

## 🔧 Meta & 工具 Workflows (6 个)

### 22. meta (13 nodes, 16 edges)
- **用途**：Meta 模式（改进 factory 自身）
- **起点**：`insights`
- **定义**：`factory/workflow/definitions.py::meta_workflow()`

### 23. create (19 nodes, 26 edges)
- **用途**：创建新的 factory mode/workflow
- **起点**：`fork_research`
- **定义**：`factory/workflow/definitions.py::create_workflow()`

### 24. evolve (16 nodes, 19 edges)
- **用途**：Workflow 进化
- **起点**：`baseline`
- **定义**：`factory/workflow/definitions.py::evolve_workflow()`

### 25. deep-qa (6 nodes, 6 edges)
- **用途**：深度 QA 验证
- **起点**：`health_checker`
- **定义**：`factory/workflow/definitions.py::deep_qa_workflow()`

### 26. skill-refine (5 nodes, 5 edges)
- **用途**：Skill 精化
- **起点**：`dag_sort`
- **定义**：`factory/workflow/definitions.py::skill_refine_workflow()`

### 27. spec-generate (6 nodes, 7 edges)
- **用途**：Spec 生成
- **起点**：`extract`
- **定义**：`factory/workflow/definitions.py::spec_generate_workflow()`

---

## 📝 文档 Workflows (3 个)

### 28. doc-generate (6 nodes, 8 edges)
- **用途**：文档生成
- **起点**：`scan_project`
- **定义**：`factory/workflow/definitions.py::doc_generate_workflow()`

### 29. doc-update (5 nodes, 6 edges)
- **用途**：文档更新
- **起点**：`diff_scope`
- **定义**：`factory/workflow/definitions.py::doc_update_workflow()`

### 30. spec-update (6 nodes, 7 edges)
- **用途**：Spec 更新
- **起点**：`graph_update`
- **定义**：`factory/workflow/definitions.py::spec_update_workflow()`

---

## 📈 复杂度排名

按节点数排序（从复杂到简单）：

1. **frontend-design** - 26 nodes, 40 edges (最复杂)
2. **design** - 23 nodes, 31 edges
3. **research** - 21 nodes, 27 edges
4. **build** - 20 nodes, 27 edges
5. **improve** - 19 nodes, 24 edges
6. **parallel-improve** - 19 nodes, 22 edges
7. **create** - 19 nodes, 26 edges
8. **frontend-design-scan** - 17 nodes, 24 edges
9. **evolve** - 16 nodes, 19 edges
10. **refine** - 15 nodes, 18 edges

...

**最简单**: discover - 3 nodes, 3 edges

---

## 🛠️ Workflow 构成要素

### 节点类型 (8 种)

定义文件：`factory/workflow/primitives.py`

1. **AgentNode** - 调用 specialist agent
2. **FnNode** - 运行 shell 命令
3. **GateNode** - 决策点 (PROCEED/RELOOP/HALT)
4. **ForkNode** - 并行启动多个分支
5. **JoinNode** - 等待所有并行分支完成
6. **SubgraphForkNode** - N 个子图的并行副本
7. **SelectionNode** - 从多个分支中选择最佳结果
8. **Study** - 特殊的 FnNode，运行 `factory study`

### 支持文件

```
factory/workflow/
├── definitions.py      # 所有核心 workflow 定义 (4560 行)
├── primitives.py       # 节点类型定义
├── executor.py         # Headless 执行器 (WorkflowExecutor)
├── skill_export.py     # 生成 SKILL.md
├── validation.py       # 验证 workflow 图
├── registry.py         # Workflow 注册与发现
├── cli.py              # CLI 命令
└── contributed/        # 第三方贡献的 workflows
    ├── swebench/
    ├── featurebench/
    └── ...
```

---

## 🔍 常用命令

```bash
# 查看所有 workflows
factory workflow list

# 查看某个 workflow 的详细结构
factory workflow show improve

# 以 JSON 格式导出
factory workflow show improve --format json

# 验证 workflow 图
factory workflow validate improve

# 生成 SKILL.md 文件
factory workflow export-skills

# 运行 workflow (headless 模式)
factory workflow run improve --project /path/to/project
```

---

## 💡 使用建议

### 日常开发
- `improve` - 改进现有项目（最常用）
- `build` - 从零构建新项目
- `design` - 交互式设计 + 构建
- `refine` - 单个变更请求

### 快速原型
- `founder` - 快速原型（跳过 review）

### 自定义
- `create` - 创建新的 workflow

### 元开发
- `meta` - 改进 factory 自身
- `evolve` - 进化 workflow 定义

---

## 📚 参考资源

- [Workflow Engine README](../factory/workflow/README.md)
- [Workflow Primitives 定义](../factory/workflow/primitives.py)
- [Workflow Executor 实现](../factory/workflow/executor.py)
- [主 CLAUDE.md](../CLAUDE.md)
