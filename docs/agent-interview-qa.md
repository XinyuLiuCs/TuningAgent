# Agent 开发面试 Q&A

## Q1: 为什么要设计成 Multi-Agent？

单 Agent 架构的根本瓶颈在于**上下文腐蚀（Context Rot）**：随着对话轮次和工具调用结果不断累积，输入 token 膨胀，模型对关键信息的提取能力急剧下降。这一现象已被多项研究证实——Liu et al. (2023) 在 *Lost in the Middle* 中发现 LLM 对上下文中部信息的利用率显著低于首尾，呈 U 型曲线；Xiao et al. (2023, ICLR 2024) 从注意力机制角度揭示了 *Attention Sink* 现象——模型会将不成比例的注意力分配给起始 token，而非语义相关的内容；

Multi-Agent 中每个子Agent有独立的上下文窗口和专注的system prompt，是用进程隔离的思路管理LLM的注意力资源。以 TuningAgent 为例，`code-explorer` 子 Agent 可能产生数千行代码搜索结果，但返回给主 Agent 的只是几句关键发现——这等于用进程隔离的思路保护了主 Agent 的注意力资源。
其他例子：wide-research 广域搜索

Multi-Agent 的问题：Agent间通信本身有开销、简单任务单Agent就够了、Agent之间如何协作。


## Q2: 上下文窗口不够时，优先保留哪些信息？为什么？

核心原则：**保留约束，压缩过程。**

**约束**是限定 Agent 后续行为空间的信息，丢失会导致方向错误或重复犯错：
- **System prompt + 用户意图**：丢失即方向错误
- **已做出的技术决策及理由**：丢了会导致后续步骤与已有选型矛盾
- **失败尝试及原因**：负向约束，丢了 Agent 会重复踩坑
- **关键事实发现**：定位到的 bug 根因、目标文件路径等不会变的结论

**过程**是产生约束的中间推理和原始工具输出，可压缩或丢弃：
- 大段的搜索结果、冗长的命令输出、中间的思考链——信息密度低，且可重新执行获取

在 TuningAgent 的 `agent.py` 中，`_summarize_messages` 直接体现了这个原则：token 超限时，保留 system prompt 和所有 user 消息不动，对每轮 assistant/tool 执行过程调用 LLM 生成摘要替换原文。压缩后结构为 `system → user1 → summary1 → user2 → summary2 → ...`。摘要 prompt 要求聚焦"完成了什么任务、调用了哪些工具、关键发现"，控制在 1000 词内。

**为什么这样分？** 约束是不可恢复的——丢了就不知道该做什么、不该做什么；过程是可恢复的——工具可以重新执行，推理可以重新生成。优先丢弃可恢复信息，是信息论意义上的最优策略。


## Q3: 介绍一下 Function Call 的原理

### 完整生命周期

整个过程是一个 **"注册 → 声明 → 生成 → 解析 → 执行 → 回填"** 的闭环：

**1. 注册：Tool 定义为 JSON Schema**
每个工具继承 `Tool` 基类（`tools/base.py`），声明 `name`、`description`、`parameters`（JSON Schema 格式）。Agent 初始化时把所有 Tool 实例注册到 `self.tools` 字典中。

**2. 声明：随请求发送给模型**
每次调用 LLM 时，Tool 列表通过 `to_openai_schema()` 转换为 API 要求的格式，作为 `tools` 参数随 messages 一起发送。模型从中获得三层信息：有哪些工具可用（name + description）、每个工具接受什么参数（properties 的 key、type、description）、哪些参数必填（required）。`description` 是给模型看的自然语言说明，`parameters` 是给约束解码用的形式化规则，两者配合引导模型做出正确的语义决策并保证输出格式合法。

**3. 生成：模型输出结构化 JSON**
模型经过 SFT/RLHF 训练后，学会了在需要外部信息时不直接回答，而是输出特殊格式的 tool_calls。每个 tool_call 包含 `id`、`function.name`、`function.arguments`（JSON 字符串）。

**4. 解析：框架层反序列化**
LLM Client（如 `OpenAIClient`）将 API 返回的原始响应解析为统一的 `LLMResponse` 对象，其中 `tool_calls` 是 `ToolCall` 列表，`arguments` 已从 JSON 字符串反序列化为 Python dict。

**5. 执行：路由到具体 Tool**
Agent loop（`agent.py:559-603`）遍历 `response.tool_calls`，从 `self.tools` 字典中按 `function_name` 查找对应 Tool 实例，调用 `tool.execute(**arguments)` —— `**arguments` 将 dict 展开为关键字参数，直接映射到 Tool 子类的 `execute()` 方法签名。返回 `ToolResult(success, content, error)`。

**6. 回填：结果以 tool message 追加到历史**
执行结果封装为 `role="tool"` 的 Message，携带 `tool_call_id` 与原始调用对应，追加到 `self.messages`。下一轮 LLM 调用时，模型看到这个 tool message 就知道"我之前调的函数返回了什么"，继续决策是再调工具还是给出最终回答。

**关键设计点：模型并不"执行"任何代码**，它只是生成了一段符合 schema 约束的 JSON。所有执行都发生在框架层。

### finish_reason

`finish_reason` 是 API 响应中表示模型**为什么停止生成**的字段，是 Agent loop 的核心控制信号：

| 值 | OpenAI | Anthropic（`stop_reason`） | 含义 |
|---|---|---|---|
| `stop` | Y | Y (`end_turn`) | 模型自然结束，产出最终回答 |
| `tool_calls` | Y | Y (`tool_use`) | 模型请求调用工具 |
| `length` | Y | Y (`max_tokens`) | 输出达到 max_tokens 上限被截断 |
| `content_filter` | Y | N | 内容被安全过滤拦截 |

在 TuningAgent 中，`agent.py:544` 实际通过 `if not response.tool_calls` 来判断是否结束——有 tool_calls 就执行工具，没有就结束。这比检查 finish_reason 字符串更鲁棒，因为不同 provider 命名不统一（OpenAI 叫 `tool_calls`，Anthropic 叫 `tool_use`），而 tool_calls 列表是否为空是跨 provider 一致的。

### 如何保证模型返回的参数是正确的？

不能 100% 保证，但通过多层机制逼近正确：

**1. 训练时：SFT + RLHF** —— 模型学会根据 JSON Schema 生成符合格式的参数，是基础能力。

**2. 推理时：约束解码（Constrained Decoding）** —— 不改变模型权重，在每步 token 采样时根据形式化规则屏蔽非法 token。机制：将 JSON Schema 编译为有限状态机（FSM），预计算每个状态下词表中哪些 token 是合法转移，生成时将非法 token 的 logit 设为负无穷。发展脉络：Hokamp & Liu (ACL 2017) 首次提出词汇级约束解码 → Willard & Louf (2023) 提出基于 FSM 的 logit masking（Outlines 库）→ OpenAI (2024) 推出 Structured Outputs（`strict: true`），从学术走向工业标准。约束解码保证格式绝对正确，但不保证语义正确。

**3. 框架层：错误反馈闭环** —— TuningAgent 的 `agent.py:593-603` 捕获工具执行异常，将错误信息封装为 `ToolResult(success=False, error=...)` 回填到消息历史，模型下一轮看到错误后自我修正。本质上是用 Agent loop 的迭代能力容忍单次参数错误。

**一句话总结**：Schema 声明引导模型"填什么"，约束解码保证"格式对"，错误反馈闭环兜底"语义错了能改"。


## Q4: 在项目中如何解决幻觉问题？

### 为什么会有幻觉？

**当前的 Agent 是开环系统。** 模型训练完成后参数固定不再更新，而现实世界持续变化——这意味着模型的"知识"必然与现实存在偏差，且偏差随时间增大。训练阶段的数据本身也可能包含错误、矛盾和过时信息，模型无法区分真假，只是学到了 token 之间的共现概率分布。

**推理阶段，幻觉被放大。** 模型输出的是概率分布，每一步采样都有几率选中错误的 token。更关键的是，模型是自回归架构——基于自己已生成的内容继续输出。一旦早期产生了错误，后续会在错误基础上"滚雪球"（Zhang et al., 2023, *How Language Model Hallucinations Can Snowball*）。Xu et al. (2024) 从学习理论角度形式化证明：幻觉是 LLM 的内生限制，无法完全消除。

### 如何在使用阶段抑制幻觉？

核心思路两条：**提供充分信息减少猜测，执行后验证让模型知道自己错了。**

**业界常用方法：**
- **RAG**（Lewis et al., 2020）：检索真实文档注入上下文，让模型基于证据回答而非依赖参数记忆
- **Chain-of-Verification**（Dhuliawala et al., 2023, Meta）：模型生成回答后自动生成验证问题，独立回答后修正原始输出
- **Self-Consistency**（Wang et al., 2023, ICLR）：采样多条推理路径，取一致性最高的答案
- **Tool-Grounding**（Schick et al., 2023, *Toolformer*）：让模型调用搜索引擎、计算器等工具获取真实数据，替代参数记忆
- **RLHF / DPO**（训练阶段）：通过人类偏好反馈对齐模型，降低幻觉倾向；Anthropic 的 Constitutional AI 进一步用原则约束模型"说不知道"而非编造

**TuningAgent 中的实践：**
- **System Prompt 禁止猜测**（`system_prompt.md:70`）：**"Don't guess — use tools to discover missing information"**，从指令层面要求模型用工具获取事实
- **工具锚定替代参数记忆**：`ReadTool` 读真实文件、`BashTool` 执行真实命令——回答基于工具返回的实际数据，本质是 RAG 思想在 Agent 场景的延伸
- **执行后验证**（`terminal_bench_agent.py:569`）：**"Before finishing, verify the actual file contents or command output"**——强制模型完成任务前用工具验证结果
- **错误反馈闭环**（`agent.py:593-603`）：工具执行失败时错误信息回填上下文，模型看到真实的错误从而自我修正，而非幻想成功
- **持久化记忆**（`MemoryTool`）：关键事实写入 `AGENT.md` 并注入 system prompt，避免跨会话时虚构上下文

**一句话总结**：幻觉源于模型是开环的概率系统，在 Agent 架构中通过工具锚定提供真实信息、通过执行后验证形成反馈闭环，把 LLM 从"知识库"转变为"推理引擎"。


## Q5: Agent 的记忆是如何存储和管理的？

LLM 本身无状态，每次请求独立。但 Agent 需要完成任务需要提供必要的信息，例如：用户意图、已做出的决策、执行过程中的关键发现、失败的尝试。TuningAgent 通过两种互补机制实现记忆：

**执行日志（Log）**：`AgentLogger`（`logger.py`）以 JSONL 格式按 session → turn → step 三级层次，只追加地记录完整执行轨迹——LLM 请求/响应、工具调用参数与结果、rewind、子 Agent 派发等，存储在 `~/.mini-agent/log/<session_id>/` 下。日志不注入上下文，是给人看的，用于事后分析 Agent 行为、定位失败原因、对比模型表现。

**持久化记忆（AGENT.md）**：`MemoryTool`（`tools/memory_tool.py`）将跨会话需要保留的关键信息（project conventions、user preferences、key decisions）写入 `AGENT.md`。启动时全量注入 system prompt，Agent 无需额外操作即可"想起"之前的记忆。写入通过 `memory_update(content)` 全量覆写，Agent 自行决定保留什么、丢弃什么。System prompt 明确约束不存 in-progress state 和 unverified guesses，防止记忆膨胀。

两者互补：Log 是监控录像，AGENT.md 是墙上的备忘录。一个完整但不参与推理，一个精炼但直接影响模型行为。

### 其他项目的记忆方案

OpenClaw 在此基础上做了两个方向的扩展：

**多文件分层记忆**：OpenClaw 还有 `SOUL.md`（定义 Agent 的人格、行为原则和边界——相当于"性格宪法"）、USER.md 用户名、称呼、时区、偏好，每日日志 `memory/YYYY-MM-DD.md`（只追加的工作笔记）。不同文件承载不同生命周期的信息：SOUL.md 几乎不变，MEMORY.md 缓慢演进，每日日志快速积累。

**RAG 语义检索**：当记忆文件增长到无法全量注入上下文时，OpenClaw 引入了检索增强。Markdown 文件被切分为 ~400 token 的 chunk 并生成 embedding，存入 SQLite 向量数据库。检索时采用**混合搜索**——向量相似度（擅长语义匹配）+ BM25 关键词（擅长精确符号匹配）融合打分，再经 MMR 去重和时间衰减重排序，通过 `memory_search` 工具按需召回相关片段。这解决了 TuningAgent 全量注入方案的容量瓶颈：当记忆量超出上下文窗口时，只检索相关部分而非全部加载。


## Q6: 如何评估 Agent 的性能？

Agent 评估比单纯的 LLM 评估复杂——LLM 评估只看"回答对不对"，Agent 评估要看"任务完没完成"，而完成一个任务涉及多步决策、工具调用、错误恢复等整条链路。

### 评估什么？

**结果指标**：任务是否完成（pass/fail）、通过率（pass rate，即 pass@1）。多次采样时用 pass@k 消除 LLM 采样随机性。

**过程指标**：
- **步数（steps）**：完成任务用了多少轮 LLM 调用，反映推理效率
- **Token 用量**：prompt + completion tokens，直接关联成本
- **耗时**：端到端延迟，影响用户体验

**失败模式分布**：不同失败模式指向不同优化方向，混在一起算 pass rate 会掩盖真正的瓶颈：
- **agent_timeout**：Agent 在规定时间内未完成任务（工具超时、死循环等）→ 优化工具层
- **test_timeout**：Agent 已提交结果，但 grader 评分脚本运行超时 → 排查产出质量
- **parse_error**：评分框架无法解析 Agent 输出（容器损坏、格式不符）→ 排查环境兼容性
- **unknown_agent_error**：Agent 启动就失败（Docker 问题、依赖缺失）→ 排查基础设施
- **wrong_answer**：Agent 完成了但答案错误 → 改进 prompt 或换更强模型

### TuningAgent 的评估实践：Terminal-Bench

TuningAgent 集成了 Terminal-Bench 评估框架（`benchmark/terminal_bench.py`），流程为：

1. **任务容器隔离**：每个任务在独立 Docker 容器中运行，Agent 通过 tmux session 操作远程环境，确保任务间互不干扰
2. **Agent 执行**：`TuningAgentTerminalBenchAgent`（`terminal_bench_agent.py`）将工具适配为远程容器操作，Agent 在容器内自主完成任务
3. **自动评分**：任务完成后由 grader 脚本检查结果（文件内容、命令输出等），判定 pass/fail
4. **结果聚合**：转换为 `BenchmarkTaskResult` 和 `BenchmarkRunSummary`，包含每个任务的 resolved 状态、失败模式、耗时等


## Q7: 能否只用 bash 一个工具？为什么还需要单独的文件读写编辑工具？

技术上完全可以——bash 是图灵完备的。但 TuningAgent 单独实现 `file_read`/`file_write`/`file_edit` 三个文件工具，每个都针对 Agent 场景做了专门优化：

**ReadTool**：自动添加行号（`LINE_NUMBER|CONTENT` 格式），方便模型在后续编辑时精确定位；支持 `offset`/`limit` 分块读取大文件；内置 token 感知截断（`truncate_text_by_tokens`），超过 32000 token 时保留头尾、截断中间并标注 `[Content truncated]`，防止单次读取撑爆上下文。用 bash 的 `cat` 读一个大文件，几千行输出直接污染整个上下文窗口。

**EditTool**：只传差异——模型提供 `old_str` 和 `new_str`，工具做精确字符串匹配替换。相比让模型用 `sed 's/old/new/g'` 或重写整个文件，好处是：模型只需生成要改的那几行而非整个文件内容，极大节省 token；要求 `old_str` 在文件中唯一存在，匹配不到会报错，避免误改其他位置；不需要模型掌握 sed 正则语法，降低出错概率。

**WriteTool**：自动创建父目录（`mkdir -p`），description 中引导模型"先读再写"、"优先编辑而非新建"，从工具描述层面约束模型行为。

**一句话**：bash 是万能但不精确的，专用工具通过截断、行号、差异编辑、唯一性校验等机制，**用约束换正确率**。


## Q8: MCP 的实现过程是怎样的？

### MCP 是什么？

MCP（Model Context Protocol）是一个开放协议，让 Agent 能够以统一的方式连接外部工具服务。核心思想是**工具的提供者和消费者解耦**——Agent 不需要知道工具的具体实现，只需要通过 MCP 协议发现和调用。

### 完整生命周期：配置 → 连接 → 发现 → 执行 → 清理

**1. 配置（mcp.json）**

每个 MCP Server 在 `mcp.json` 中声明连接方式，支持三种传输：
- **STDIO**：启动子进程，通过 stdin/stdout 通信，适合本地工具
- **SSE**：Server-Sent Events，单向推送，适合实时更新
- **HTTP/Streamable HTTP**：标准 HTTP 请求响应，适合远程服务

**2. 连接（MCPServerConnection.connect()）**

启动时遍历 `mcp.json` 中所有未禁用的 server，根据 type 选择传输方式建立连接，创建 `ClientSession` 完成握手初始化，用 `AsyncExitStack` 管理生命周期确保异常时也能清理。

**3. 工具发现（session.list_tools()）**

连接建立后，调用 `session.list_tools()` 获取该 server 暴露的所有工具——每个工具包含 name、description、inputSchema（JSON Schema）。无需硬编码，工具完全动态发现。

**4. 工具包装（MCPTool）**

每个发现的 MCP 工具被包装为 `MCPTool` 实例（继承自 `Tool` 基类），与内置工具（BashTool、ReadTool 等）实现相同接口。对 Agent 和 LLM 来说，MCP 工具和内置工具没有任何区别，统一注册到 `agent.tools` 字典中。

**5. 执行（MCPTool.execute()）**

Agent loop 中模型生成 tool_call 后，通过 `session.call_tool(name, arguments)` 发送 RPC 请求到 MCP Server，Server 执行实际逻辑返回 `CallToolResult`，结果解析为 `ToolResult(success, content, error)` 回填消息历史。全程有 `asyncio.timeout(execute_timeout)` 保护。

**6. 清理（cleanup_mcp_connections()）**

会话结束时遍历所有活跃连接，通过 `AsyncExitStack.aclose()` 关闭。

### 工具过滤：协议不管，client 负责

MCP 协议的 `tools/list` 支持分页（`nextCursor`），但**没有过滤参数**——不能指定只获取某些工具。协议的设计意图是 server 暴露全部工具列表，由 client 侧决定哪些传给 LLM。

Mini-Agent 当前实现是全量加载——`session.list_tools()` 拿到什么就全注册。工具少时没问题，但工具多了会有两个问题：
- **上下文占用**：每个工具的 schema 随请求发给 LLM，20 个工具可能占几千 token
- **决策干扰**：工具越多模型越容易选错

可优化方向：
- **配置级过滤**：在 mcp.json 中加 `include_tools` / `exclude_tools` 字段，加载时跳过不需要的工具
- **Progressive disclosure**：先只把工具名和描述告诉模型，需要时再激活具体 schema——TuningAgent 的 Skill 系统就是这个思路

### 关键设计

- **统一接口**：MCP 工具和内置工具对 Agent 完全透明，不需要特殊处理逻辑
- **三层超时**：connect_timeout（10s）、execute_timeout（60s）、sse_read_timeout（120s），支持全局默认 + 每个 server 单独覆盖
- **错误隔离**：超时和异常都转为 ToolResult 错误，Agent 能看到错误并自我恢复，而非直接崩溃
- **动态更新**：Server 可声明 `listChanged` 能力，工具列表变化时发送通知，client 刷新工具注册

