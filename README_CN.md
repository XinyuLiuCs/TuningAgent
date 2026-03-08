中文 | [**English**](README.md)

# TuningAgent

> 可评估/调试的通用 Coding Agent

基于 [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) 开发的 Agent 评估系统，专注于模型、上下文和工具的系统化评估。

## 项目定位

TuningAgent 是一个用于学习研究的 **最小可行的** Agent 评估框架，用于：
- 对比不同 LLM 模型在实际任务中的表现
- 追踪和回溯 Agent 的执行过程
- 评估工具、 Skills 和 MultiAgent 的有效性

## 核心能力

### 1. 模型评估
- 配置文件切换不同 LLM（Anthropic/OpenAI/AWS Bedrock）
- 模型池管理（配置文件支持多模型，运行时热切换）
- 按模型记录执行统计（token、延迟、错误）
- 健康检查（启动时并行探测 API 连通性，`/health` 按需触发）

### 2. 上下文追踪
- 结构化 JSONL 日志（Session/Turn/Step 层级，完整记录消息、工具调用及结果）
- 对话回退（`/rewind`，turn 级截断后可换模型/工具/改措辞重试）
- 上下文调试（`/context` 导出完整消息上下文及工具 schema，`/log` 查看日志文件）

### 3. 工具与 Skills
- 11 个基础工具（Bash + 后台进程管理、文件读写编辑、项目记忆、Subagent 工具、模式切换）
- 10 个 Claude Skills（支持 `/reload` 热重载）

### 4. Agent 模式（Ask / Plan / Build）
- **Build**（默认）：完整执行模式，所有工具可用
- **Ask**：只读问答模式——禁用写入工具，LLM 专注回答问题
- **Plan**：只读规划模式——禁用写入工具，LLM 输出结构化计划
- 通过 CLI 命令（`/ask`、`/plan`、`/build`）或 LLM 工具调用（`mode_switch`）切换
- Plan → Build 切换时自动压缩规划上下文为摘要，释放 context 空间

### 5. MultiAgent 委托
- 固定 Subagent：通过 `SUBAGENT.yaml` 预定义（角色、工具、权限）
- 动态 Subagent：LLM 在运行时通过 `subagent_create` 按需创建
- 前台模式（默认）：阻塞执行，支持 cancel_event 透传和超时控制
- 后台模式：非阻塞执行，结果写入 `.subagent/{id}.md`，主 Agent 通过 `file_read` 轮询
- 执行控制：Esc 取消所有 Agent，支持通过 `subagent_cancel` 单独取消

### 6. Benchmark 集成
- 内置 `benchmark` CLI 入口，直接对接 Terminal-Bench
- 内置任务子集：`curated-smoke` 和 `curated-core`
- 同时支持本地数据集（`--dataset-path`）和 registry 数据集（`--dataset`）
- 自动将 Terminal-Bench 原始结果标准化为 `tuningagent_summary.json`
- 提供本地 TuningAgent 适配器，复用现有 Agent 循环、模型池、日志系统与文件/Bash 工具

## 设计理念

### ReAct 循环

核心是 ReAct（推理 + 行动）循环：LLM 生成响应 → 框架检查工具调用 → 有则执行并回传结果，无则结束。如此反复，直到 LLM 给出最终回答或达到 `max_steps` 上限。

### MultiAgent 委托

主 Agent 可以把任务委托给 Subagent，每个 Subagent 独立运行自己的 ReAct 循环：

- **固定 Subagent**：通过 `SUBAGENT.yaml` 预定义角色、工具白名单和 token 限制，启动时加载。
- **动态 Subagent**：LLM 运行时通过 `subagent_create` 按需创建，自行指定角色和工具。
- **前台执行**（默认）：主 Agent 阻塞等待，支持超时和取消信号透传。
- **后台执行**：Subagent 异步运行，完成后将结果写入 `.subagent/{id}.md`，主 Agent 按需读取。
- **单层约束**：Subagent 不能再创建 Subagent，保持架构扁平、便于调试。

### 涌现行为

后台 Subagent 启动后，框架不会主动提示"去轮询结果"，但 LLM 自发产生了等待-检查策略：

```
→ subagent_code-explorer(task)    → "后台 Subagent 已启动..."
→ bash("sleep 15 && test -f .subagent/xxx.md && echo DONE || echo STILL RUNNING")
→ "STILL RUNNING"
→ bash("sleep 20 && test -f ...")  → "STILL RUNNING"
→ bash("sleep 30 && test -f ...")  → "DONE"
→ file_read(".subagent/xxx.md")    → 读到完整结果
```

框架只暴露了一个隐含信号：文件不存在 = 仍在运行。轮询间隔、重试次数等策略完全由 LLM 自主决定。

### Agent 模式

TuningAgent 没有用独立的 Subagent 做规划，而是在主 Agent 内部切换模式。**Plan** 模式下写入工具被移除，system prompt 指导 LLM 输出结构化计划。用户确认后切换到 **Build** 模式，规划过程被压缩为摘要（释放 context），恢复全部工具。LLM 也可以通过 `mode_switch(mode="build")` 自行触发切换。

### 交互模型

用户只与主 Agent 对话，Subagent 对用户透明。按 Esc 可取消所有 Agent（含后台）。

### 工具命名 — `{category}_{action}`

所有工具采用 `{类别}_{动作}` 的前缀命名规范：

| 前缀 | 工具 | 匹配模式 |
|-------|------|---------|
| `bash_` | `bash`, `bash_output`, `bash_kill` | `bash*` |
| `file_` | `file_read`, `file_write`, `file_edit` | `file_*` |
| `memory_` | `memory_update` | `memory_*` |
| `skill_` | `skill_get` | `skill_*` |
| `subagent_` | `subagent_run`, `subagent_create`, `subagent_cancel` | `subagent_*` |
| `mode_` | `mode_switch` | `mode_*` |

**为什么用前缀？** 框架需要在 LLM 采样阶段按类别 mask 工具（允许/禁用）。统一前缀后，`file_*` 一条规则即可选中整个类别，无需逐个枚举。新增工具只要遵循前缀，自动归入对应分组。

## 快速开始

### 安装

```bash
cd TuningAgent

# 安装依赖（推荐使用 uv）
uv sync

# 或使用 pip
pip install -e .
```

### 配置

```bash
# 复制配置示例
cp tuningagent/config/config-example.yaml tuningagent/config/config.yaml

# 编辑配置，填入 API Key
vim tuningagent/config/config.yaml
```

配置文件支持：
- Anthropic API（Claude 模型）
- OpenAI API（GPT 模型）
- MiniMax API（MiniMax 模型）
- AWS Bedrock（Claude 模型，通过 AWS 凭证认证，无需 API Key）

#### 多模型配置（模型池）

在 `models` 字段中定义多个模型，通过 `default_model` 指定默认使用的模型：

```yaml
models:
  minimax-m2:
    api_key: "sk-xxx"
    api_base: "https://api.minimaxi.com"
    model: "MiniMax-M2.1"
    provider: "anthropic"
  claude-sonnet:
    api_key: "sk-ant-xxx"
    api_base: "https://api.anthropic.com"
    model: "claude-sonnet-4-20250514"
    provider: "anthropic"
  gpt-4o:
    api_key: "sk-openai-xxx"
    api_base: "https://api.openai.com/v1"
    model: "gpt-4o"
    provider: "openai"
  bedrock-claude:
    provider: "bedrock"
    model: "us.anthropic.claude-opus-4-6-v1"
    aws_region: "us-east-1"
    aws_profile: ""
default_model: "bedrock-claude"
```

- `anthropic` / `openai` 类型的模型需要各自的 `api_key`
- `bedrock` 类型无需 `api_key`，通过 AWS 凭证链认证（环境变量、`~/.aws/credentials`、IAM Role 等）
- `provider` 决定使用的协议：`anthropic`、`openai` 或 `bedrock`
- 两种配置格式完全向后兼容——没有 `models` 字段时自动退回单模型模式

#### 会话命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示可用命令 |
| `/clear` | 清空会话历史（保留 system prompt） |
| `/history` | 显示当前会话消息数 |
| `/stats` | 显示会话统计信息 |
| `/model` | 列出模型池中所有模型，标记当前活跃模型 |
| `/model <alias>` | 切换到指定别名的模型（如 `/model claude-sonnet`） |
| `/model-stats` | 显示各模型的执行统计（调用次数、token 用量、平均延迟、错误数） |
| `/health` | 检查所有模型的 API 连通性（启动时也会自动执行） |
| `/rewind [N]` | 回退 N 个对话 turn（默认 1） |
| `/context` | 导出完整消息历史和工具 schema |
| `/ask` | 切换到 Ask 模式（只读问答） |
| `/plan` | 切换到 Plan 模式（只读规划） |
| `/build` | 切换到 Build 模式（完整执行） |
| `/reload` | 热重载 Skills（磁盘上 SKILL.md 的变更即时生效） |
| `/log` | 查看日志文件 |
| `/exit` | 退出程序（也可用 `/quit`、`/q`） |

#### 对话回退（`/rewind`）

当 Agent 回答不满意或方向走错时，用 `/rewind` 回退到之前的 turn 重新对话：

```
/rewind        回退 1 个 turn
/rewind 3      回退 3 个 turn
```

**截断示例：**

```
初始:      system → user1 → asst1 → tool1 → user2 → asst2 → user3 → asst3
/rewind 1: system → user1 → asst1 → tool1 → user2 → asst2
/rewind 2: system → user1 → asst1 → tool1
```

**设计要点：** rewind 只截断对话上下文，不自动重新执行。这让用户可以在重试前自由调整——换模型（`/model`）、改措辞、或换一个完全不同的问题。工具的副作用（文件修改、bash 执行）不可逆，rewind 时会提示仍在运行的后台进程。

### 运行

```bash
# 交互式运行
python -m tuningagent.cli

# 或安装后直接调用
tuningagent
```

### 使用 Terminal-Bench 跑评测

先准备 benchmark 依赖：

```bash
cd bench/terminal-bench
uv sync
cd ../..
```

查看内置 profile：

```bash
python -m tuningagent.cli benchmark --list-profiles
```

使用本地数据集跑一个 smoke 集合：

```bash
python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --run-id tb-smoke-20260308
```

只跑 registry 数据集：

```bash
python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench-core==0.1.1 \
  --no-profile \
  --run-id tb-core-20260308
```

常用参数：
- `--task-id`：附加一个或多个显式任务
- `--no-profile`：禁用默认的 `curated-smoke` 集合
- `--dataset-path`：运行 `bench/terminal-bench` 下的本地数据集
- `--dataset`：运行 registry 数据集，例如 `name==version`
- `--dry-run`：只输出标准化后的运行元信息，不真正执行
- `--keep-proxy-env`：保留 `ALL_PROXY` / `all_proxy`，不做默认剥离

运行产物会写入 `bench/terminal-bench/runs/tuningagent/<run-id>/`：
- `results.json`：Terminal-Bench 原始结果
- `tuningagent_summary.json`：TuningAgent 统一 schema 下的标准化摘要

## 项目结构

```
TuningAgent/
├── tuningagent/              # 核心包
│   ├── agent.py              # Agent 执行逻辑
│   ├── llm/                  # LLM 客户端
│   │   ├── anthropic_client.py
│   │   ├── bedrock_client.py # AWS Bedrock（继承 AnthropicClient）
│   │   ├── openai_client.py
│   │   ├── llm_wrapper.py
│   │   └── model_pool.py    # 多模型池管理
│   ├── benchmark/            # Benchmark 集成
│   │   ├── terminal_bench.py
│   │   └── terminal_bench_agent.py
│   ├── tools/                # 工具实现
│   │   ├── bash_tool.py
│   │   ├── file_tools.py
│   │   ├── memory_tool.py
│   │   ├── mode_tool.py         # Agent 模式切换（Ask/Plan/Build）
│   │   ├── skill_tool.py
│   │   ├── subagent_tool.py    # Subagent 工具 + SubagentManager
│   │   └── subagent_loader.py  # SUBAGENT.yaml 加载器
│   ├── skills/               # Claude Skills（10）
│   ├── schema/               # 数据结构定义
│   ├── config/               # 配置文件
│   │   ├── subagents/          # 固定 Subagent 定义（SUBAGENT.yaml）
│   └── cli.py                # 命令行入口
├── docs/                     # 使用与 benchmark 文档
├── bench/                    # 外部 benchmark 工作区（Terminal-Bench）
├── tests/                    # 测试用例
└── pyproject.toml
```

## 开发计划

### Phase 1: 模型池管理 ✅
- [x] 支持单配置文件定义多个模型
- [x] 通过 `/model` 命令快速切换模型
- [x] 记录每个模型的执行结果（`/model-stats`）
- [x] API 健康检查（启动并行探测 + `/health` 按需检查）
- [ ] 并行调用多模型对比结果

### Phase 2: 执行追踪 ✅
- [x] 结构化 JSONL 日志（session/turn/step 层级）
- [x] 工具调用的输入输出记录
- [x] `/context` 上下文导出、`/rewind` 对话回退
- [ ] 简单的执行可视化（步骤、token、工具调用）
- [ ] 失败案例自动归档

### Phase 2.5: MultiAgent 委托 ✅
- [x] 固定 Subagent（SUBAGENT.yaml）
- [x] 运行时动态创建 Subagent
- [x] 前台 + 后台执行模式
- [x] 执行控制（取消、超时）
- [x] Agent 模式（Ask / Plan / Build）及 Plan 上下文压缩

### Phase 3: 工具评估
- [ ] 工具调用统计（次数、成功率、耗时）
- [ ] 失败案例收集
- [ ] Skills 单元测试与性能基准

### Phase 4: Benchmark 流程 ✅
- [x] Terminal-Bench CLI 集成
- [x] 内置 benchmark profile
- [x] 标准化 benchmark 结果摘要
- [ ] 扩展更多 benchmark 适配器
- [ ] 评估报告与可视化面板

## 技术栈

- **Python**: 3.10+
- **LLM 客户端**: Anthropic, OpenAI, AWS Bedrock
- **核心库**: Pydantic, HTTPX, PyYAML
- **Skills**: Claude Skills（10 个技能）
- **Benchmark**: Terminal-Bench, Docker

## 当前限制

- 需要手动配置 API Key
- 无 Web UI（仅 CLI）

## 贡献

欢迎提交 Issue 和 Pull Request。当前重点关注：
- 执行可视化与失败案例归档（Phase 2 剩余）
- 工具评估框架（Phase 3）
- Benchmark 扩展与报告自动化（Phase 4）

## License

MIT License - 基于 [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent)

## 相关资源

- **原项目**: https://github.com/MiniMax-AI/Mini-Agent
- **Claude Skills**: https://github.com/anthropics/skills
- **Anthropic API**: https://docs.anthropic.com
- **OpenAI API**: https://platform.openai.com
- **Terminal-Bench 评估指南**: ./docs/terminal-bench-evaluation-guide-cn.md
- **Terminal-Bench 数据集速查**: ./docs/terminal-bench-datasets-quick-cn.md
