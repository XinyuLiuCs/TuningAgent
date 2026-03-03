# TuningAgent

> 可评估/调试的通用 Coding Agent 框架

基于 [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) 开发的 Agent 评估框架，专注于模型、上下文和工具的系统化评估。

## 项目定位

TuningAgent 是一个用于学习研究的 **最小可行的** Agent 评估框架，用于：
- 对比不同 LLM 模型在实际任务中的表现
- 追踪和回溯 Agent 的执行过程
- 评估工具和 Skills 的有效性


## 核心能力

### 1. 模型评估

**当前支持：**
- ✅ 配置文件切换不同 LLM（Anthropic/OpenAI/AWS Bedrock）
- ✅ 基础的多模型客户端
- ✅ 模型池管理（单配置文件支持多模型，运行时切换）
- ✅ 按模型记录执行统计（token、延迟、错误）
- ✅ 健康检查（启动时与初始化并行探测 API 连通性，`/health` 按需触发）

**规划中：**
- [ ] 并行调用多模型对比结果

### 2. 上下文追踪

**当前支持：**
- ✅ 基础执行日志

**规划中：**
- [ ] 完整保存执行轨迹（消息历史、工具调用、结果）
- [ ] 从任意步骤重新执行
- [ ] 上下文调试，系统上下文集中到一个文件，进行版本管理
- [ ] 简单的执行可视化（步骤、token、工具调用）
- [ ] 失败案例自动归档

### 3. 工具评估

**当前支持：**
- ✅ 5个基础工具（文件读写、Bash、笔记）
- ✅ 15+ Claude Skills

**规划中：**
- [ ] 工具单元测试框架
- [ ] 调用统计（次数、成功率、耗时）
- [ ] Skills 性能基准

### 4. 端到端测试

**当前支持：**
- ✅ 程序化驱动 CLI（pipe input/output），无需人工交互
- ✅ 真实 API 调用 + 真实工具执行，验证真实副作用
- ✅ 3 个基础场景：文件创建、Bash 命令、读取+编辑
- ✅ `pytest -m e2e` 标记，可独立运行或排除

**规划中：**
- [ ] 定义标准化测试集（任务 + 预期结果），批量评估不同模型
- [ ] 评估结果自动对比报告

#### 运行 E2E 测试

```bash
# 前置条件：config.yaml 中需配置有效的 API Key

# 运行所有 E2E 测试
pytest tests/test_cli_e2e.py -v

# 仅运行 E2E 测试（通过 mark 筛选）
pytest -m e2e -v

# 排除 E2E 测试（仅跑单元测试）
pytest -m "not e2e"
```

#### 编写新的 E2E 测试

```python
async def test_your_scenario(tmp_path):
    config = _load_config()  # 自动设置 max_steps=5

    with create_pipe_input() as inp:
        inp.send_text("你的任务描述\r")   # 注意用 \r 而非 \n
        inp.send_text("/exit\r")
        await run_agent(tmp_path, config=config, input=inp, output=DummyOutput())

    # 验证副作用
    assert (tmp_path / "expected_file").exists()
```

**注意事项：**
- pipe input 必须用 `\r`（回车）提交，`\n` 会被 `c-j` key binding 截获
- Bash 命令需使用绝对路径（`cd {tmp_path} && ...`），因为 `BashTool` 不自动切换到 workspace 目录
- `max_steps` 设为小值（如 5）以限制测试耗时

## 评估方法论

### 小样本快速启动
- 从少量代表性任务开始验证
- 不依赖大规模数据集
- 快速迭代，逐步扩展

### LLM 作为评判者
- 使用 LLM 评估任务完成质量
- 结合人工审核关键案例
- 建立可复现的评估标准

## 快速开始

### 安装

```bash
# 克隆项目
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

#### 单模型配置（默认）

直接在顶层填写 API 信息即可：

```yaml
api_key: "sk-your-key"
api_base: "https://api.minimax.io"
model: "MiniMax-M2.1"
provider: "anthropic"
```

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
    aws_region: "us-east-1"      # 可选，默认读取 AWS 环境配置
    aws_profile: ""              # 可选，默认读取 AWS 环境配置
default_model: "bedrock-claude"
```

- `anthropic` / `openai` 类型的模型需要各自的 `api_key`
- `bedrock` 类型无需 `api_key`，通过 AWS 凭证链认证（环境变量、`~/.aws/credentials`、IAM Role 等）
- `provider` 决定使用的协议：`anthropic`、`openai` 或 `bedrock`
- 两种配置格式完全向后兼容——没有 `models` 字段时自动退回单模型模式

启动后在交互会话中使用以下命令管理模型：

| 命令 | 说明 |
|------|------|
| `/model` | 列出模型池中所有模型，标记当前活跃模型 |
| `/model <alias>` | 切换到指定别名的模型（如 `/model claude-sonnet`） |
| `/model-stats` | 显示各模型的执行统计（调用次数、token 用量、平均延迟、错误数） |
| `/health` | 检查所有模型的 API 连通性（启动时也会自动执行） |

### 运行

```bash
# 交互式运行
python -m tuningagent.cli

# 或安装后直接调用
pip install -e .
tuningagent
```

## 项目结构

```
TuningAgent/
├── tuningagent/              # 核心包
│   ├── agent.py              # Agent 执行逻辑
│   ├── llm/                  # LLM 客户端
│   │   ├── anthropic_client.py
│   │   ├── bedrock_client.py  # AWS Bedrock（继承 AnthropicClient）
│   │   ├── openai_client.py
│   │   ├── llm_wrapper.py
│   │   └── model_pool.py     # 多模型池管理
│   ├── tools/                # 工具实现
│   │   ├── bash_tool.py
│   │   ├── file_tools.py
│   │   ├── note_tool.py
│   │   └── skill_tool.py
│   ├── skills/               # Claude Skills（15+）
│   ├── schema/               # 数据结构定义
│   ├── config/               # 配置文件
│   └── cli.py                # 命令行入口
├── examples/                 # 示例代码
├── tests/                    # 测试用例
│   └── test_cli_e2e.py       # 端到端测试（真实 API）
└── pyproject.toml            # 项目配置
```

## 开发计划

### Phase 1: 模型池管理（MVP）
- [x] 支持单配置文件定义多个模型
- [x] 通过 `/model` 命令快速切换模型
- [x] 记录每个模型的执行结果（`/model-stats`）
- [x] API 健康检查（启动并行探测 + `/health` 按需检查）

### Phase 2: 端到端测试基础设施
- [x] CLI 可程序化驱动（`run_agent()` 支持注入 config/input/output）
- [x] 真实 API + 真实工具的 E2E 测试（3 个场景通过）
- [ ] 定义标准化评估任务集
- [ ] 批量评估不同模型，自动对比报告

### Phase 3: 执行追踪
- [ ] 序列化保存完整对话历史
- [ ] 工具调用的输入输出记录
- [ ] 基础的步骤可视化

### Phase 4: 工具评估
- [ ] 工具调用统计
- [ ] 失败案例收集
- [ ] Skills 单元测试

### Phase 5: 评估流程
- [ ] LLM 评判器实现
- [ ] 评估报告生成

## 技术栈

- **Python**: 3.10+
- **LLM 客户端**: Anthropic, OpenAI, AWS Bedrock
- **核心库**: Pydantic, HTTPX, PyYAML
- **Skills**: Claude Skills（15+ 官方技能）

## 当前限制

- 仅支持单 Agent（无多 Agent 协作）
- 需要手动配置 API Key
- 执行追踪功能待完善
- 无 Web UI（仅 CLI）

## 贡献

欢迎提交 Issue 和 Pull Request。重点关注：
- 模型评估功能的完善
- 执行追踪的实现
- 工具测试框架的建立

## License

MIT License - 基于 [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent)

## 相关资源

- **原项目**: https://github.com/MiniMax-AI/Mini-Agent
- **Claude Skills**: https://github.com/anthropics/skills
- **Anthropic API**: https://docs.anthropic.com
- **OpenAI API**: https://platform.openai.com
