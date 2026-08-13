# Lumen Branch 文档

本目录包含 `lumen` 分支的专属文档，这些文档不会出现在 `main` 分支中。

**注意**：此目录在 `.gitignore` 中被忽略，所有内容仅供本地学习使用，不会被推送到远程仓库。

## 📚 文档列表

### [BRANCH_STRATEGY.md](BRANCH_STRATEGY.md)
分支策略文档，说明如何使用双分支策略（main + lumen）进行开发。

**内容**：
- main 分支：upstream 镜像
- lumen 分支：开发分支
- 日常工作流程
- 同步 upstream 的步骤

---

### [ARCHITECTURE.md](ARCHITECTURE.md)
Factory 代码架构文档，描述 `factory/` 目录下的核心 Python 包结构。

**内容**：
- 目录结构
- 核心模块说明
- 代码组织方式

---

### [WORKFLOWS_OVERVIEW.md](WORKFLOWS_OVERVIEW.md)
Remote Factory 所有 30 个 workflows 的分类总览。

**内容**：
- 核心开发 workflows (10 个)
- Benchmark workflows (8 个)
- 前端设计 workflows (3 个)
- Meta & 工具 workflows (6 个)
- 文档 workflows (3 个)
- 复杂度排名
- 节点类型说明
- 常用命令

---

### [WORKFLOW_NODES.md](WORKFLOW_NODES.md)
Workflow 节点类型完整指南 - 详细说明所有 8 种 node 类型。

**内容**：
- 8 种 Node 类型详解（AgentNode, FnNode, GateNode, ForkNode, JoinNode, SubgraphForkNode, SelectionNode, Study）
- 11 种 AgentRole 说明
- 3 种 Gate 类型（agent/fn/user）
- Verdict 系统（PROCEED/RELOOP/HALT）
- Edge 边定义和条件分支
- 常见 Workflow 模式
- 实际示例和最佳实践

---

### [DESIGN_WORKFLOW_ANALYSIS.md](DESIGN_WORKFLOW_ANALYSIS.md)
Design Workflow 逐句深度解析 - 60 行代码的完整剖析。

**内容**：
- 完整源代码带行号
- 逐句详细分析（每一行的含义、用途、设计决策）
- 完整流程图（新项目和现有项目的两条路径）
- Design vs Build 对比表
- 4 种设计模式（继承、条件入口、User Gate、Fn Gate）
- 关键要点总结

---

### [SWEBENCH_WORKFLOW_ANALYSIS.md](SWEBENCH_WORKFLOW_ANALYSIS.md)
SWE-bench Workflow 详细解析 - Benchmark workflow 的典型实现。

**内容**：
- 完整源代码（167 行）
- 4 节点极简流程（study → builder → gate_verify → auto_merge）
- Benchmark workflow 的 3 大特征
- RELOOP 重试机制
- Harbor 容器集成
- 与核心 workflows 的对比

---

### [BENCHMARK_WORKFLOW_EXPLAINED.md](BENCHMARK_WORKFLOW_EXPLAINED.md)
**三层架构完整解析** - 一个 Workflow 如何应对整个 Benchmark

**内容**：
- **核心疑问**：SWE-bench 有 1000 个 issues，但 workflow 只处理单个 issue？
- **三层架构**：Harbor（批量）→ Factory Agent（适配）→ Workflow（单任务）
- **Harbor Orchestrator**：数据集格式、批量执行流程、容器循环伪代码
- **Factory Harbor Agent**：Install 阶段、Run 阶段、适配器价值
- **完整流程示例**：1000 实例的执行过程（配伪代码）
- **数据流图**：从 HuggingFace 数据集到验证结果
- **4 大设计理念**：单一职责、标准化接口、容器隔离、Headless 优先
- **实际案例**：单实例测试 vs 完整 benchmark（并发 100x 加速）

---

### [BENCHMARKS_DIRECTORY.md](BENCHMARKS_DIRECTORY.md)
**benchmarks/ 目录完整指南** - 结构、作用、配置、使用方法

**内容**：
- **目录结构**：核心脚本、Agent 适配器、本地数据集、结果输出
- **8 种 Benchmark**：SWE-bench, FeatureBench, TerminalBench, ProgramBench 等
- **核心文件详解**：
  - `config.sh` - Benchmark 配置映射（8 种）
  - `factory_harbor_agent.py` - 8 个 Agent 适配器类（349 行）
  - `*-extra-instructions.md` - 自动化模式特殊指令
  - 运行脚本：`run-swebenchifyhard.sh`, `run-harbor.sh`, `run-full-eval.sh`
- **本地数据集**：tomswe-harbor（5 个任务）, programbench-harbor（1 个任务）
  - 每个任务：instruction.md, task.toml, environment/, tests/
- **使用方法**：5 种场景（单实例测试、本地验证、批量评测、CI/CD、直接 Harbor）
- **数据流深入**：15 步完整流程（从 Shell 到 Harbor 到 Factory 到结果）
- **开发指南**：如何添加新 Benchmark、如何创建本地测试数据集

---

### [HARBOR_EXPLAINED.md](HARBOR_EXPLAINED.md)
**Harbor 完整介绍** - 什么是 Harbor？与 Benchmark 集成的关系

**内容**：
- **Harbor 是什么**：AI 代码 Agent 评测平台、容器编排系统、Benchmark 托管
- **为什么需要 Harbor**：解决环境差异、接口不统一、验证标准不一致的问题
- **核心概念**：Dataset（数据集）、Instance（实例）、Agent（代理）、Environment（容器环境）、Verifier（验证器）
- **完整工作流程**：15 步详解（从加载数据集到返回结果）
- **数据集格式**：
  - 远程数据集（HuggingFace）- SWE-bench 示例
  - 本地数据集（Harbor 目录结构）- task.toml, instruction.md, tests/
- **Harbor 与 Factory 集成**：三层架构、8 个 Agent 类、关键约定（/tmp/task-instruction.md）
- **添加新 Benchmark**：
  - 场景 1：使用现有远程数据集（3 步）
  - 场景 2：创建本地数据集（7 步）
- **Harbor 的价值**：对比手动实现 vs Harbor 提供（9 个维度对比表）

---

## 🎯 快速导航

- **想了解分支策略？** → [BRANCH_STRATEGY.md](BRANCH_STRATEGY.md)
- **想了解代码结构？** → [ARCHITECTURE.md](ARCHITECTURE.md)
- **想了解所有 workflows？** → [WORKFLOWS_OVERVIEW.md](WORKFLOWS_OVERVIEW.md)
- **想了解 workflow 节点类型？** → [WORKFLOW_NODES.md](WORKFLOW_NODES.md)
- **想深入理解 Design Workflow？** → [DESIGN_WORKFLOW_ANALYSIS.md](DESIGN_WORKFLOW_ANALYSIS.md)
- **想了解 SWE-bench Workflow？** → [SWEBENCH_WORKFLOW_ANALYSIS.md](SWEBENCH_WORKFLOW_ANALYSIS.md)
- **想理解 Benchmark 三层架构？** → [BENCHMARK_WORKFLOW_EXPLAINED.md](BENCHMARK_WORKFLOW_EXPLAINED.md)
- **想了解 benchmarks/ 目录？** → [BENCHMARKS_DIRECTORY.md](BENCHMARKS_DIRECTORY.md)
- **想了解 Harbor 评测平台？** → [HARBOR_EXPLAINED.md](HARBOR_EXPLAINED.md)
- **想了解整体项目？** → [../CLAUDE.md](../CLAUDE.md)

---

## 📝 说明

这些文档只存在于 `lumen` 分支，用于：
- 记录 lumen 分支的开发流程和规范
- 整理学习笔记和代码理解
- 与 main 分支（upstream 镜像）区分

**main 分支** 只包含 `CLAUDE.md`，保持与 upstream 完全一致。

**隐私保护**：此目录已添加到 `.gitignore`，所有学习笔记仅保存在本地，不会被推送到任何远程仓库。
