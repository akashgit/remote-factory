╔══════════════════════════════════════════════════════════════╗
║    📊 benchmarks/ 目录完整指南                             ║
║    - 结构、作用、配置、使用方法                             ║
╚══════════════════════════════════════════════════════════════╝

## 📁 目录结构

```
benchmarks/
├── 🔧 核心脚本 (Shell)
│   ├── config.sh                        # 8 种 benchmark 配置映射
│   ├── lib.sh                           # 共享函数库
│   ├── run.sh                           # 通用运行脚本
│   ├── run-harbor.sh                    # Harbor 通用入口
│   ├── run-swebenchifyhard.sh          # SWE-bench 专用脚本
│   ├── run-full-eval.sh                # 完整评测流程
│   └── commit-full-eval.sh             # 提交评测结果
│
├── 🐍 Agent 适配器 (Python)
│   └── factory_harbor_agent.py         # 8 个 Harbor Agent 类定义
│
├── 📝 额外指令 (Markdown)
│   ├── featurebench-extra-instructions.md    # FeatureBench 特殊说明
│   └── terminalbench-extra-instructions.md   # TerminalBench 特殊说明
│
├── 📊 结果和历史 (自动生成)
│   ├── results/                        # 评测结果 JSON 文件
│   └── history.jsonl                   # 历史记录
│
└── 🧪 本地 Benchmark 数据集
    ├── tomswe-harbor/                  # TomSWE 本地数据集（5 个任务）
    │   ├── csv-export/
    │   ├── date-parse/
    │   ├── dedup-list/
    │   ├── discount-calc/
    │   └── sort-order/
    │       ├── environment/            # Docker 环境配置
    │       ├── instruction.md          # 任务描述
    │       ├── task.toml               # Harbor 任务配置
    │       └── tests/                  # 测试脚本
    │
    └── programbench-harbor/            # ProgramBench 本地数据集
        └── cmatrix/
            ├── environment/
            ├── instruction.md
            ├── task.toml
            └── tests/
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎯 核心作用

**benchmarks/ 目录是 Factory 的评测基础设施**：

1. **CI/CD 集成**：在 GitHub Actions 中运行自动化评测
2. **Harbor 适配**：连接 Harbor benchmark 平台和 Factory CLI
3. **多 benchmark 支持**：统一接口支持 8 种不同的 benchmark
4. **本地测试数据集**：包含小规模 benchmark 实例用于开发测试

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📋 支持的 8 种 Benchmark

**定义位置**：`benchmarks/config.sh` 中的 `benchmark_config()` 函数

| Benchmark | Dataset | Agent Class | 说明 |
|-----------|---------|------------|------|
| **swebench** | `swe-bench/swe-bench-verified` | `SwebenchFactoryCeo` | 经典 SWE-bench bug 修复 |
| **swebenchifyhard** | `red-hat-ai/SWE-benchify-hard` | `SwebenchifyHardFactoryCeo` | 更难的 SWE-bench 变种 |
| **featurebench** | `featurebench` | `FeaturebenchFactoryCeo` | 实现缺失的函数功能 |
| **terminalbench** | `terminal-bench@2.0` | `TerminalbenchFactoryCeo` | 终端操作任务 |
| **programbench** | 本地数据集 | `ProgramBenchFactoryCeo` | 编程任务（本地） |
| **legacybench** | `factory-ai/legacy-bench` | `LegacybenchFactoryCeo` | 遗留代码重构 |
| **harborindex** | `harbor-index/harbor-index-1.0` | `HarborIndexFactoryCeo` | Harbor 索引基准 |
| **tomswe** | 本地 `tomswe-harbor/` | `TomsweFactoryCeo` | 小规模 SWE 任务（用于测试） |
| **salitrap** | `salitrap` | `SalitrapFactoryCeo` | Salitrap 基准 |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔧 核心文件详解

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1. `config.sh` - Benchmark 配置映射

**作用**：为每种 benchmark 定义配置参数

**核心函数**：
```bash
benchmark_config() {
    local name="$1"
    
    case "${name}" in
        swebench)
            BENCH_DATASET="swe-bench/swe-bench-verified"
            BENCH_AGENT_CLASS="factory_harbor_agent:SwebenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_FILTER_STYLE="glob"
            ;;
        featurebench)
            BENCH_DATASET="featurebench"
            BENCH_AGENT_CLASS="factory_harbor_agent:FeaturebenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_EXTRA_INSTRUCTION="featurebench-extra-instructions.md"
            BENCH_FILTER_STYLE="exact"
            ;;
        # ... 其他 6 种
    esac
}
```

**配置字段**：
- `BENCH_DATASET` - Harbor 数据集名称或本地路径
- `BENCH_AGENT_CLASS` - Agent 适配器类（Python 模块:类名）
- `BENCH_AGENT_IMPORT_FLAG` - Harbor 参数（`--agent-import-path` 或 `--agent`）
- `BENCH_EXTRA_INSTRUCTION` - 额外指令文件（如自动化模式说明）
- `BENCH_FILTER_STYLE` - 任务过滤方式（`glob`, `exact`, `none`）
- `BENCH_ALLOW_HOSTS` - 允许的网络主机（用于网络隔离）
- `BENCH_POST_EVAL_CMD` - 评测后执行的命令

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2. `lib.sh` - 共享函数库

**作用**：提供通用辅助函数

**关键函数**：
```bash
log()                    # 带时间戳的日志输出
write_result()           # 写入结果 JSON
setup_ci_dirs()          # 创建 CI 目录结构
benchmark_all_names()    # 列出所有支持的 benchmark
```

**环境变量**：
```bash
CI_RESULTS_DIR           # 结果输出目录
TIMESTAMP                # 当前时间戳
HARNESS_DIR              # benchmarks/ 绝对路径
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3. `run-swebenchifyhard.sh` - SWE-bench 专用脚本

**作用**：运行单个或多个 SWE-bench 实例

**用法**：
```bash
# 运行单个实例（默认）
./benchmarks/run-swebenchifyhard.sh "containers--image-90028"

# 指定超时和数据集
./benchmarks/run-swebenchifyhard.sh "django--django-12345" 7200 "red-hat-ai/SWE-benchify-hard"
```

**参数**：
1. `INSTANCE_ID` - 实例 ID（默认：`containers--image-90028`）
2. `SOLVER_TIMEOUT` - 超时秒数（默认：3600）
3. `HARBOR_DATASET` - 数据集名称（默认：`red-hat-ai/SWE-benchify-hard`）

**核心流程**：
```bash
# 1. 加载配置
source lib.sh
source config.sh

# 2. 创建临时工作目录
JOBS_DIR=$(mktemp -d)

# 3. 调用 Harbor
uvx harbor run \
    --dataset "${HARBOR_DATASET}" \
    --agent-import-path factory_harbor_agent:SwebenchifyHardFactoryCeo \
    --model "${MODEL}" \
    --include-task-name "*${INSTANCE_ID}" \
    --n-concurrent 1 \
    --jobs-dir "${JOBS_DIR}"

# 4. 提取结果
# 5. 写入 JSON
# 6. 清理
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4. `factory_harbor_agent.py` - Agent 适配器

**作用**：为每种 benchmark 实现 Harbor `BaseInstalledAgent` 接口

**文件大小**：349 行

**包含的 Agent 类**：
```python
# 8 个 Agent 类，都继承自 BaseInstalledAgent

class SwebenchFactoryCeo(BaseInstalledAgent):
    """SWE-bench 适配器"""
    async def install(self, environment):
        # 安装 Claude Code + factory CLI
        ...
    
    async def run(self, instruction, environment, context):
        # 运行 factory ceo . --headless --focus "..."
        ...

class SwebenchifyHardFactoryCeo(BaseInstalledAgent):
    """SWE-benchify-hard 适配器（更难）"""
    ...

class FeaturebenchFactoryCeo(BaseInstalledAgent):
    """FeatureBench 适配器"""
    # 添加额外的 featurebench-extra-instructions.md
    ...

class TerminalbenchFactoryCeo(BaseInstalledAgent):
    """TerminalBench 适配器"""
    # 添加额外的 terminalbench-extra-instructions.md
    ...

class ProgramBenchFactoryCeo(BaseInstalledAgent):
    """ProgramBench 适配器（本地数据集）"""
    ...

class LegacybenchFactoryCeo(BaseInstalledAgent):
    """LegacyBench 适配器"""
    ...

class HarborIndexFactoryCeo(BaseInstalledAgent):
    """Harbor Index 适配器"""
    ...

class TomsweFactoryCeo(BaseInstalledAgent):
    """TomSWE 适配器（本地小规模测试）"""
    ...

class SalitrapFactoryCeo(BaseInstalledAgent):
    """Salitrap 适配器"""
    ...
```

**核心模式**（所有 Agent 都相似）：

```python
async def install(self, environment: BaseEnvironment) -> None:
    # Step 1: 安装系统依赖
    await self.exec_as_root(
        environment,
        command="apt-get update && apt-get install -y curl git procps"
    )
    
    # Step 2: 安装 Claude Code
    await self.exec_as_agent(
        environment,
        command="curl -fsSL https://downloads.claude.ai/.../bootstrap.sh | bash"
    )
    
    # Step 3: 安装 factory CLI
    await self.exec_as_agent(
        environment,
        command="uv tool install 'remote-factory @ git+https://github.com/...'"
    )

async def run(
    self,
    instruction: str,
    environment: BaseEnvironment,
    context: AgentContext,
) -> None:
    # 构建 factory 命令
    command = (
        'factory ceo . '
        '--headless '
        '--no-github '
        '--focus "$(cat /tmp/task-instruction.md)"'
    )
    
    # 在容器内执行
    await self.exec_as_agent(environment, command=command)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 5. `*-extra-instructions.md` - 特殊指令

**作用**：为某些 benchmark 提供额外的运行时指令

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `featurebench-extra-instructions.md`

**内容**：
```markdown
## Important: Autonomous Execution

- Do NOT ask for confirmation
- Do NOT say 'Want me to go ahead?'
- Execute immediately
- Full permissions enabled
- Time is limited

## FeatureBench Context

- Function bodies have been removed
- Implement based on specifications
- Do NOT modify test files
- Focus on correctness
```

**注入方式**：
```python
class FeaturebenchFactoryCeo(BaseInstalledAgent):
    async def run(self, instruction, environment, context):
        # 读取额外指令
        extra = Path("featurebench-extra-instructions.md").read_text()
        
        # 拼接到任务描述
        full_instruction = f"{instruction}\n\n{extra}"
        
        # 写入标准位置
        await environment.write_file("/tmp/task-instruction.md", full_instruction)
        
        # 运行 factory
        ...
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `terminalbench-extra-instructions.md`

**内容**：类似，强调：
- 自动执行模式
- 不要等待确认
- 直接操作终端

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 6. 本地 Benchmark 数据集

**目的**：
- 本地测试和开发
- 不依赖外部数据集
- 快速验证 agent 逻辑

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `tomswe-harbor/` 结构

**5 个小任务**：
```
tomswe-harbor/
├── csv-export/          # CSV 导出 bug（处理逗号和引号）
├── date-parse/          # 日期解析问题
├── dedup-list/          # 列表去重
├── discount-calc/       # 折扣计算错误
└── sort-order/          # 排序逻辑问题
```

**每个任务包含**：

```
csv-export/
├── environment/         # Docker 环境配置（基础镜像、依赖）
├── instruction.md       # 任务描述（给 Agent 看的问题说明）
├── task.toml           # Harbor 任务元数据
└── tests/              # 验证脚本（Harbor 用于评分）
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `instruction.md` 示例

**文件**：`tomswe-harbor/csv-export/instruction.md`

```markdown
The export feature is broken for some records. When users download
their data, certain rows come out garbled. It works fine most of the
time though.

## User Profile
You are working with a data engineer who has these preferences:
- **Verbosity:** verbose — likes to understand the full picture
- **Testing:** pytest, always test edge cases with special characters
- **Code style:** use csv module, type hints
- **Git:** descriptive commit messages
- **Data handling:** never silently drop data, raise on corruption
```

**特点**：
- 故意模糊（像真实用户报告）
- 包含用户偏好（影响 Agent 行为）
- 不给具体文件位置（Agent 需要自己找）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### `task.toml` 示例

**文件**：`tomswe-harbor/csv-export/task.toml`

```toml
schema_version = "1.3"

[task]
name = "tomswe/csv-export"
description = "Fix CSV export to handle fields with commas and quotes"
authors = []
keywords = ["tomswe", "csv", "quoting"]

[metadata]
difficulty = "medium"
category = "programming"

[environment]
network_mode = "public"          # 允许网络访问
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
gpus = 0
mcp_servers = []

[agent]
timeout_sec = 3600.0             # Agent 超时 1 小时

[verifier]
timeout_sec = 300.0              # 验证超时 5 分钟
```

**配置说明**：
- `network_mode`: `public`（联网）, `private`（隔离）, `limited`（受限）
- `mcp_servers`: 可以指定 MCP 工具服务器
- `timeout_sec`: 防止 Agent 无限运行

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🚀 使用方法

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 1: 运行单个 SWE-bench 实例（测试）

```bash
cd /path/to/remote-factory

# 运行默认实例
./benchmarks/run-swebenchifyhard.sh

# 运行指定实例
./benchmarks/run-swebenchifyhard.sh "django--django-12345"

# 自定义超时（2 小时）
./benchmarks/run-swebenchifyhard.sh "flask--flask-67890" 7200
```

**输出**：
```json
// benchmarks/results/<timestamp>-swebenchifyhard.json
{
  "benchmark": "swebenchifyhard",
  "run_id": "ci-swebenchifyhard-20260810-123456",
  "status": "success",
  "passed": 1,
  "total": 1,
  "details": {
    "solver": "factory",
    "cost_usd": 2.34,
    "input_tokens": 45000,
    "output_tokens": 12000,
    ...
  }
}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 2: 运行本地 TomSWE 任务（快速验证）

```bash
# 使用通用脚本
./benchmarks/run-harbor.sh tomswe csv-export

# 等价于
uvx harbor run \
    --dataset benchmarks/tomswe-harbor \
    --agent-import-path factory_harbor_agent:TomsweFactoryCeo \
    --include-task-name "tomswe/csv-export" \
    --n-concurrent 1
```

**TomSWE 所有任务**：
```bash
./benchmarks/run-harbor.sh tomswe csv-export
./benchmarks/run-harbor.sh tomswe date-parse
./benchmarks/run-harbor.sh tomswe dedup-list
./benchmarks/run-harbor.sh tomswe discount-calc
./benchmarks/run-harbor.sh tomswe sort-order
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 3: 运行完整 Benchmark（批量）

```bash
# 运行完整 SWE-bench（所有实例）
./benchmarks/run-full-eval.sh swebench

# 并发 10 个容器
N_CONCURRENT=10 ./benchmarks/run-full-eval.sh featurebench

# 运行所有 benchmark
for bench in $(benchmark_all_names); do
    ./benchmarks/run-full-eval.sh "$bench"
done
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 4: CI/CD 集成（GitHub Actions）

**文件**：`.github/workflows/benchmark.yml`

```yaml
jobs:
  swebench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Run SWE-bench
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          ./benchmarks/run-swebenchifyhard.sh "containers--image-90028"
      
      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: benchmark-results
          path: benchmarks/results/*.json
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 场景 5: 直接调用 Harbor（高级）

**绕过脚本，直接用 Harbor CLI**：

```bash
# 安装 Harbor
uvx harbor --help

# 运行单个实例
uvx harbor run \
    --dataset "red-hat-ai/SWE-benchify-hard" \
    --agent-import-path benchmarks/factory_harbor_agent.py:SwebenchifyHardFactoryCeo \
    --model "anthropic/claude-opus-4-6" \
    --include-task-name "*django--django-12345" \
    --n-concurrent 1 \
    --jobs-dir /tmp/harbor-jobs

# 运行多个实例（并发）
uvx harbor run \
    --dataset "featurebench" \
    --agent-import-path benchmarks/factory_harbor_agent.py:FeaturebenchFactoryCeo \
    --n-concurrent 10

# 本地数据集
uvx harbor run \
    --dataset benchmarks/tomswe-harbor \
    --agent benchmarks/factory_harbor_agent.py:TomsweFactoryCeo \
    --include-task-name "tomswe/csv-export"
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔍 深入理解：数据流

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 完整流程（以 SWE-bench 为例）

```
1. 用户执行
   ↓
   ./benchmarks/run-swebenchifyhard.sh "django-12345"

2. Shell 脚本
   ↓
   source lib.sh
   source config.sh
   benchmark_config "swebenchifyhard"
   ↓
   BENCH_DATASET="red-hat-ai/SWE-benchify-hard"
   BENCH_AGENT_CLASS="factory_harbor_agent:SwebenchifyHardFactoryCeo"

3. 调用 Harbor
   ↓
   uvx harbor run \
       --dataset "${BENCH_DATASET}" \
       --agent-import-path "${BENCH_AGENT_CLASS}" \
       --include-task-name "*django-12345"

4. Harbor 加载数据集
   ↓
   从 HuggingFace 下载 "red-hat-ai/SWE-benchify-hard"
   过滤出 instance_id = "django-12345"

5. Harbor 创建容器
   ↓
   docker run --name django-12345 ...
   ↓
   cd /workspace/django
   git checkout <base_commit>

6. Harbor 导入 Agent
   ↓
   import factory_harbor_agent
   agent = SwebenchifyHardFactoryCeo()

7. Harbor 调用 agent.install()
   ↓
   在容器内执行：
   - apt-get install curl git procps
   - 安装 Claude Code
   - uv tool install remote-factory

8. Harbor 写入任务描述
   ↓
   echo "<problem_statement>" > /tmp/task-instruction.md

9. Harbor 调用 agent.run()
   ↓
   在容器内执行：
   factory ceo . --headless --no-github \
       --focus "$(cat /tmp/task-instruction.md)"

10. Factory CEO 启动
    ↓
    WorkflowExecutor 运行 swebench_workflow()
    ↓
    study → builder → gate_verify → auto_merge

11. Builder Agent 修复代码
    ↓
    编辑 django/db/models/query.py
    运行测试
    git commit -m "Fix Q object issue"

12. Harbor 验证
    ↓
    git apply <test_patch>
    运行测试
    ✅ PASSED

13. Harbor 评分
    ↓
    对比 agent_patch 和 golden_patch
    计算 resolved: true/false

14. Harbor 返回结果
    ↓
    {
      "instance_id": "django-12345",
      "resolved": true,
      "cost_usd": 2.34,
      ...
    }

15. Shell 脚本写入结果
    ↓
    echo "{...}" > benchmarks/results/<timestamp>-swebenchifyhard.json

16. 清理
    ↓
    docker rm django-12345
    rm -rf /tmp/harbor-jobs
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📊 结果格式

**输出位置**：`benchmarks/results/<timestamp>-<benchmark>.json`

**结构**：
```json
{
  "benchmark": "swebenchifyhard",
  "run_id": "ci-swebenchifyhard-20260810-123456",
  "timestamp": "2026-08-10T12:34:56Z",
  "status": "success",  // 或 "failure", "timeout"
  "passed": 1,
  "total": 1,
  "instances": [
    {
      "instance_id": "django--django-12345",
      "resolved": true,
      "cost_usd": 2.34,
      "duration_sec": 1847
    }
  ],
  "details": {
    "solver": "factory",
    "model": "claude-opus-4",
    "input_tokens": 45000,
    "output_tokens": 12000,
    "cache_read_tokens": 23000,
    "cache_creation_tokens": 8000
  }
}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🎓 关键设计理念

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1️⃣ 统一接口

**所有 benchmark 都通过相同的模式**：
```bash
./benchmarks/run-harbor.sh <benchmark-name> <instance-id>
```

**内部自动处理**：
- 数据集来源（HuggingFace vs 本地）
- Agent 类选择
- 额外指令注入
- 结果提取

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2️⃣ 可组合性

**Shell 脚本分层**：
```
run-swebenchifyhard.sh  (特定 benchmark 入口)
    ↓
run-harbor.sh           (通用 Harbor 包装器)
    ↓
config.sh               (配置映射)
    ↓
lib.sh                  (共享函数)
```

**好处**：
- 添加新 benchmark 只需修改 `config.sh`
- 通用逻辑复用（日志、结果写入、清理）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3️⃣ 本地 + 云端混合

**两种数据集来源**：
```bash
# 云端数据集（HuggingFace）
BENCH_DATASET="swe-bench/swe-bench-verified"

# 本地数据集（repo 内）
BENCH_LOCAL_PATH="${HARNESS_DIR}/benchmarks/tomswe-harbor"
```

**好处**：
- 云端数据集：大规模、标准化
- 本地数据集：快速测试、无依赖、可定制

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4️⃣ 容器隔离

**每个实例独立容器**：
- 环境一致性
- 并行执行
- 失败隔离
- 可复现性

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🛠️ 开发指南

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 添加新 Benchmark

**步骤**：

#### 1. 在 `config.sh` 中添加配置

```bash
benchmark_config() {
    local name="$1"
    
    case "${name}" in
        # ... 现有 benchmarks
        
        mynewbench)
            BENCH_DATASET="huggingface/mynewbench"
            BENCH_AGENT_CLASS="factory_harbor_agent:MynewbenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_FILTER_STYLE="exact"
            ;;
    esac
}

benchmark_all_names() {
    echo "swebench featurebench ... mynewbench"  # 添加到列表
}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 2. 在 `factory_harbor_agent.py` 中添加 Agent 类

```python
class MynewbenchFactoryCeo(BaseInstalledAgent):
    """My New Benchmark 适配器"""
    
    async def install(self, environment: BaseEnvironment) -> None:
        # 标准安装流程
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl git procps"
        )
        await self.exec_as_agent(
            environment,
            command="curl -fsSL https://downloads.claude.ai/.../bootstrap.sh | bash"
        )
        await self.exec_as_agent(
            environment,
            command="uv tool install 'remote-factory @ git+https://...'"
        )
    
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # 可选：添加额外指令
        extra_instructions = Path("mynewbench-extra-instructions.md").read_text()
        full_instruction = f"{instruction}\n\n{extra_instructions}"
        
        # 写入标准位置
        await environment.write_file("/tmp/task-instruction.md", full_instruction)
        
        # 运行 factory
        command = (
            'factory ceo . '
            '--headless '
            '--no-github '
            '--focus "$(cat /tmp/task-instruction.md)"'
        )
        await self.exec_as_agent(environment, command=command)
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 3. （可选）添加额外指令文件

```bash
# benchmarks/mynewbench-extra-instructions.md
echo "## Important: Mynewbench Context

- Specific instructions for this benchmark
- Autonomous execution mode
- Time limits
" > benchmarks/mynewbench-extra-instructions.md
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 4. 测试

```bash
# 运行单个实例
./benchmarks/run-harbor.sh mynewbench "test-instance-001"

# 运行完整 benchmark
./benchmarks/run-full-eval.sh mynewbench
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 添加本地测试数据集

**步骤**：

#### 1. 创建目录结构

```bash
mkdir -p benchmarks/mynewbench-harbor/task-001/{environment,tests}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 2. 编写任务描述

```bash
# benchmarks/mynewbench-harbor/task-001/instruction.md
cat > benchmarks/mynewbench-harbor/task-001/instruction.md <<'EOF'
The login feature is broken. Users report getting "Invalid credentials"
even with correct passwords.

## User Profile
- Prefers verbose error messages
- Uses pytest for testing
- Follows PEP 8 strictly
EOF
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 3. 创建任务配置

```bash
# benchmarks/mynewbench-harbor/task-001/task.toml
cat > benchmarks/mynewbench-harbor/task-001/task.toml <<'EOF'
schema_version = "1.3"

[task]
name = "mynewbench/task-001"
description = "Fix login authentication bug"
keywords = ["auth", "bug"]

[metadata]
difficulty = "medium"
category = "programming"

[environment]
network_mode = "public"
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096

[agent]
timeout_sec = 3600.0

[verifier]
timeout_sec = 300.0
EOF
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 4. 编写验证脚本

```bash
# benchmarks/mynewbench-harbor/task-001/tests/verify.sh
cat > benchmarks/mynewbench-harbor/task-001/tests/verify.sh <<'EOF'
#!/bin/bash
set -e

# 运行测试
pytest tests/test_auth.py -v

# 验证修复
python -c "
from app import login
assert login('user', 'password') == True
print('✅ Login fix verified')
"
EOF

chmod +x benchmarks/mynewbench-harbor/task-001/tests/verify.sh
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 5. 配置环境

```bash
# benchmarks/mynewbench-harbor/task-001/environment/Dockerfile
cat > benchmarks/mynewbench-harbor/task-001/environment/Dockerfile <<'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y git curl

WORKDIR /workspace
COPY . .

RUN pip install -r requirements.txt
EOF
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#### 6. 测试

```bash
uvx harbor run \
    --dataset benchmarks/mynewbench-harbor \
    --agent benchmarks/factory_harbor_agent.py:MynewbenchFactoryCeo \
    --include-task-name "mynewbench/task-001"
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 🔗 相关文档

- **Harbor 官方文档**: https://github.com/akashgit/harbor
- **SWE-bench 论文**: https://www.swebench.com
- **Factory Workflow 系统**: `lumen_docs/WORKFLOWS_OVERVIEW.md`
- **Benchmark Workflow 分析**: `lumen_docs/SWEBENCH_WORKFLOW_ANALYSIS.md`
- **三层架构解析**: `lumen_docs/BENCHMARK_WORKFLOW_EXPLAINED.md`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📝 总结

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**benchmarks/ 目录是 Factory 的评测基础设施**，提供：

✅ **8 种 Benchmark 支持**（SWE-bench, FeatureBench, TerminalBench 等）
✅ **统一接口**（所有 benchmark 通过相同脚本运行）
✅ **Harbor 集成**（适配 Harbor 容器编排平台）
✅ **本地测试数据集**（快速验证，无外部依赖）
✅ **CI/CD 就绪**（GitHub Actions 集成）
✅ **可扩展架构**（添加新 benchmark 只需修改配置）

**核心文件**：
- `config.sh` - Benchmark 配置映射
- `factory_harbor_agent.py` - 8 个 Agent 适配器类
- `run-*.sh` - 运行脚本
- `tomswe-harbor/`, `programbench-harbor/` - 本地数据集

**典型用法**：
```bash
# 快速测试单个实例
./benchmarks/run-swebenchifyhard.sh "django-12345"

# 本地小规模验证
./benchmarks/run-harbor.sh tomswe csv-export

# 完整 benchmark 评测
./benchmarks/run-full-eval.sh featurebench
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
