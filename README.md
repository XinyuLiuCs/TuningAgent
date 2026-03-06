[**中文**](README_CN.md) | English

# TuningAgent

> A debuggable, evaluable coding agent framework

An agent evaluation system built on [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent), focused on systematic evaluation of models, context, and tools.

## What is TuningAgent

A **minimal viable** agent evaluation framework for research and learning:
- Compare different LLMs on practical coding tasks
- Trace and replay agent execution
- Evaluate tool and skill effectiveness

## Core Features

### 1. Model Evaluation
- Switch LLMs via config (Anthropic / OpenAI / AWS Bedrock)
- Model pool — multiple models in one config, hot-swap at runtime
- Per-model stats (tokens, latency, errors)
- Health check (parallel probe on startup, `/health` on demand)

### 2. Context Tracing
- Structured JSONL logs (session / turn / step hierarchy with full message and tool call records)
- Conversation rewind (`/rewind` — truncate by turn, then switch model or rephrase)
- Context debugging (`/context` exports messages + tool schemas, `/log` browses log files)

### 3. Tools & Skills
- 10 built-in tools (bash + background process management, file read/write/edit, project memory, subagent tools)
- 10 Claude Skills (hot-reload via `/reload`)

### 4. Multi-Agent Delegation
- Fixed subagents: pre-defined via `SUBAGENT.yaml` (role, tools, limits)
- Dynamic subagents: LLM creates ad-hoc subagents at runtime via `subagent_create`
- Foreground mode (default): blocking with cancel_event transparency + timeout
- Background mode: non-blocking, result written to `.subagent/{id}.md`, main agent polls via `file_read`
- Execution control: Esc cancels all (foreground + background), per-subagent cancel via `subagent_cancel`

## Design Philosophy

### ReAct Agent Loop

The core loop follows a simple ReAct (Reason + Act) pattern: the LLM generates a response, the framework checks for tool calls — if present, tools are executed and results fed back; if absent, the turn ends. This loop repeats until the LLM produces a final text answer or `max_steps` is reached.

### Multi-Agent Architecture

Subagents extend the single-agent loop into a delegation model:

- **Fixed subagents** are declared in `SUBAGENT.yaml` files (role, allowed tools, token limits) and registered at startup — ideal for recurring roles like code exploration.
- **Dynamic subagents** are created by the LLM at runtime via `subagent_create`, specifying role and tools on the fly.
- **Foreground** (default): the main agent blocks until the subagent finishes. Supports timeout and cancel_event propagation.
- **Background**: the subagent runs asynchronously. Its result is written to `.subagent/{id}.md`; the main agent continues working and reads the file when ready.
- **Single-layer delegation**: subagents cannot spawn further subagents — this keeps the architecture flat and debuggable.

### Background Execution — Aha Moment

When a background subagent is launched, the framework provides no explicit "poll" instruction to the main agent. Yet the LLM spontaneously develops a wait-and-check strategy:

```
→ subagent_code-explorer(task)    → "Background subagent started..."
→ bash("sleep 15 && test -f .subagent/xxx.md && echo DONE || echo STILL RUNNING")
→ "STILL RUNNING"
→ bash("sleep 20 && test -f ...")  → "STILL RUNNING"
→ bash("sleep 30 && test -f ...")  → "DONE"
→ file_read(".subagent/xxx.md")    → full result
```

The framework only signals "file doesn't exist = still running." The polling strategy is entirely emergent LLM behavior.

### Interaction Model

Users interact only with the main agent. Pressing Esc cancels all agents (including background ones). Subagents are invisible to the user — this means future "planning" should be a mode switch within the main agent, not another subagent.

## Quick Start

### Install

```bash
cd TuningAgent

# Recommended
uv sync

# Or with pip
pip install -e .
```

### Configure

```bash
cp tuningagent/config/config-example.yaml tuningagent/config/config.yaml
vim tuningagent/config/config.yaml   # fill in your API key
```

Supported providers:
- Anthropic API (Claude)
- OpenAI API (GPT)
- MiniMax API
- AWS Bedrock (Claude via AWS credentials, no API key needed)

#### Multi-Model Pool

Define multiple models under the `models` key and set `default_model`:

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

- `anthropic` / `openai` providers require their own `api_key`
- `bedrock` authenticates via the AWS credential chain (env vars, `~/.aws/credentials`, IAM role, etc.)
- `provider` selects the protocol: `anthropic`, `openai`, or `bedrock`
- Both formats are backward-compatible — omitting `models` falls back to single-model mode

#### Session Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear session history (keep system prompt) |
| `/history` | Show current session message count |
| `/stats` | Show session statistics |
| `/model` | List all models, mark the active one |
| `/model <alias>` | Switch to a model (e.g. `/model claude-sonnet`) |
| `/model-stats` | Per-model stats (calls, tokens, latency, errors) |
| `/health` | Check API connectivity for all models |
| `/rewind [N]` | Roll back N conversation turns (default 1) |
| `/context` | Export full message history and tool schemas |
| `/reload` | Hot-reload skills from disk (SKILL.md changes) |
| `/log` | Browse log files |
| `/exit` | Exit program (also: `/quit`, `/q`) |

#### Conversation Rewind (`/rewind`)

Roll back when the agent goes off track, then switch model or rephrase:

```
/rewind        roll back 1 turn
/rewind 3      roll back 3 turns
```

**Example:**

```
before:    system → user1 → asst1 → tool1 → user2 → asst2 → user3 → asst3
/rewind 1: system → user1 → asst1 → tool1 → user2 → asst2
/rewind 2: system → user1 → asst1 → tool1
```

Rewind only truncates context — it does not re-execute. This lets you freely adjust before retrying: switch model (`/model`), rephrase, or ask something entirely different. Tool side effects (file changes, bash commands) are irreversible; running background processes are flagged.

### Run

```bash
# Interactive session
python -m tuningagent.cli

# Or after install
tuningagent
```

## Project Structure

```
TuningAgent/
├── tuningagent/              # Core package
│   ├── agent.py              # Agent loop
│   ├── llm/                  # LLM clients
│   │   ├── anthropic_client.py
│   │   ├── bedrock_client.py # AWS Bedrock (extends AnthropicClient)
│   │   ├── openai_client.py
│   │   ├── llm_wrapper.py
│   │   └── model_pool.py    # Multi-model pool
│   ├── tools/                # Tool implementations
│   │   ├── bash_tool.py
│   │   ├── file_tools.py
│   │   ├── memory_tool.py
│   │   ├── skill_tool.py
│   │   ├── subagent_tool.py    # Subagent tools + SubagentManager
│   │   └── subagent_loader.py  # SUBAGENT.yaml loader
│   ├── skills/               # Claude Skills (10)
│   ├── schema/               # Data models
│   ├── config/               # Configuration files
│   │   ├── subagents/          # Fixed subagent definitions (SUBAGENT.yaml)
│   └── cli.py                # CLI entry point
├── tests/                    # Tests
└── pyproject.toml
```

## Roadmap

### Phase 1: Model Pool ✅
- [x] Multi-model config in a single file
- [x] Runtime model switching (`/model`)
- [x] Per-model execution stats (`/model-stats`)
- [x] API health check (startup + `/health`)
- [ ] Parallel multi-model comparison

### Phase 2: Execution Tracing ✅
- [x] Structured JSONL logging (session/turn/step)
- [x] Tool call I/O recording
- [x] `/context` export, `/rewind` rollback
- [ ] Execution visualization (steps, tokens, tool calls)
- [ ] Automatic failure archiving

### Phase 2.5: Multi-Agent Delegation ✅
- [x] Fixed subagents (SUBAGENT.yaml)
- [x] Dynamic subagent creation at runtime
- [x] Foreground + background execution modes
- [x] Execution control (cancel, timeout)
- [ ] Agent modes (plan mode, etc.)

### Phase 3: Tool Evaluation
- [ ] Tool call statistics (count, success rate, duration)
- [ ] Failure case collection
- [ ] Skills unit tests and benchmarks

### Phase 4: Evaluation Pipeline
- [ ] Define evaluation task sets
- [ ] LLM-as-judge implementation
- [ ] Evaluation report generation

## Tech Stack

- **Python** 3.10+
- **LLM clients**: Anthropic, OpenAI, AWS Bedrock
- **Core libs**: Pydantic, HTTPX, PyYAML
- **Skills**: 10 Claude Skills

## Limitations

- Manual API key configuration
- CLI only (no web UI)

## Contributing

Issues and pull requests are welcome. Current priorities:
- Execution visualization & failure archiving (Phase 2 remaining)
- Tool evaluation framework (Phase 3)
- Evaluation pipeline automation (Phase 4)

## License

MIT License — built on [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent)

## Resources

- **Upstream**: https://github.com/MiniMax-AI/Mini-Agent
- **Claude Skills**: https://github.com/anthropics/skills
- **Anthropic API**: https://docs.anthropic.com
- **OpenAI API**: https://platform.openai.com
