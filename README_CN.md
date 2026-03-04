中文 | [**English**](README.md)

# TuningAgent

> 可评估/调试的通用 Coding Agent 框架

基于 [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) 开发的 Agent 评估系统，专注于模型、上下文和工具的系统化评估。

## 项目定位

TuningAgent 是一个用于学习研究的 **最小可行的** Agent 评估框架，用于：
- 对比不同 LLM 模型在实际任务中的表现
- 追踪和回溯 Agent 的执行过程
- 评估工具和 Skills 的有效性

## 核心能力

### 1. 模型评估
- 配置文件切换不同 LLM（Anthropic/OpenAI/AWS Bedrock）
- 模型池管理（单配置文件支持多模型，运行时热切换）
- 按模型记录执行统计（token、延迟、错误）
- 健康检查（启动时并行探测 API 连通性，`/health` 按需触发）

### 2. 上下文追踪
- 结构化 JSONL 日志（Session/Turn/Step 层级，完整记录消息、工具调用及结果）
- 对话回退（`/rewind`，turn 级截断后可换模型/改措辞重试）
- 上下文调试（`/context` 导出完整消息上下文及工具 schema，`/log` 查看日志文件）

### 3. 工具与 Skills
- 7 个基础工具（Bash + 后台进程管理、文件读写编辑、项目记忆）
- 10 个 Claude Skills（支持 `/reload` 热重载）

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
│   ├── tools/                # 工具实现
│   │   ├── bash_tool.py
│   │   ├── file_tools.py
│   │   ├── memory_tool.py
│   │   └── skill_tool.py
│   ├── skills/               # Claude Skills（10）
│   ├── schema/               # 数据结构定义
│   ├── config/               # 配置文件
│   └── cli.py                # 命令行入口
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

### Phase 3: 工具评估
- [ ] 工具调用统计（次数、成功率、耗时）
- [ ] 失败案例收集
- [ ] Skills 单元测试与性能基准

### Phase 4: 评估流程
- [ ] 定义评估任务集
- [ ] LLM 评判器实现
- [ ] 评估报告生成

## 技术栈

- **Python**: 3.10+
- **LLM 客户端**: Anthropic, OpenAI, AWS Bedrock
- **核心库**: Pydantic, HTTPX, PyYAML
- **Skills**: Claude Skills（10 个技能）

## 当前限制

- 仅支持单 Agent（无多 Agent 协作）
- 需要手动配置 API Key
- 无 Web UI（仅 CLI）

## 贡献

欢迎提交 Issue 和 Pull Request。当前重点关注：
- 执行可视化与失败案例归档（Phase 2 剩余）
- 工具评估框架（Phase 3）
- 评估流程自动化（Phase 4）

## License

MIT License - 基于 [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent)

## 相关资源

- **原项目**: https://github.com/MiniMax-AI/Mini-Agent
- **Claude Skills**: https://github.com/anthropics/skills
- **Anthropic API**: https://docs.anthropic.com
- **OpenAI API**: https://platform.openai.com
