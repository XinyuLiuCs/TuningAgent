# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TuningAgent is a minimal AI Agent evaluation framework built on [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent). It provides a single-agent agentic loop with tool/skill support for comparing LLM models (Anthropic Claude, OpenAI GPT, MiniMax) on practical coding tasks. Python 3.10+, MIT licensed.

## Build & Run Commands

```bash
# Install dependencies (preferred)
uv sync

# Or with pip
pip install -e .

# Run the interactive CLI
uv run tuningagent
# or: python -m tuningagent.cli

# Run all tests
pytest

# Run a single test file
pytest tests/test_agent.py

# Run a specific test
pytest tests/test_agent.py::test_function_name -v
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in pyproject.toml), so async test functions work without the `@pytest.mark.asyncio` decorator.

## Configuration

Copy `tuningagent/config/config-example.yaml` to `tuningagent/config/config.yaml` and fill in API keys. Config is loaded from three locations in priority order:
1. `tuningagent/config/config.yaml` (dev/local)
2. `~/.tuningagent/config/config.yaml` (user)
3. Package install directory

The `provider` field in config determines which LLM client is used (`"anthropic"` or `"openai"`). MiniMax models use either provider with auto-appended API path suffixes.

## Architecture

### Agent Loop (`tuningagent/agent.py`)

The core execution engine. `Agent.run()` is an async method that:
1. Builds message history (system prompt + user messages + tool results)
2. Calls the LLM via `LLMClient`
3. Parses tool calls from the response
4. Executes tools and appends results to message history
5. Repeats until `max_steps` or the LLM produces a final text response

Token management is handled inline: tiktoken estimates token counts, and when limits are exceeded, the agent summarizes intermediate execution history (preserving user messages) via an LLM call.

### LLM Layer (`tuningagent/llm/`)

- `LLMClient` (in `llm_wrapper.py`) is the unified entry point — it delegates to `AnthropicClient` or `OpenAIClient` based on the `provider` config
- Both clients are async and support retry with exponential backoff (`tuningagent/retry.py`)
- Tool schemas are converted to provider-specific formats within each client
- Both clients support extended thinking / reasoning parameters

### Tool System (`tuningagent/tools/`)

- `Tool` base class (in `base.py`) defines the interface: `name`, `description`, `parameters` dict, and async `execute()` method returning `ToolResult`
- Concrete tools: `BashTool` (shell exec with background process tracking), `BashOutputTool`/`BashKillTool` (background process management), `ReadTool`/`WriteTool`/`EditTool` (file ops with token-aware truncation), `MemoryTool` (project memory via AGENT.md), `SkillTool` (wraps loaded skills)
- Tools are registered by name in `Agent.__init__` and their schemas are passed to the LLM

### Skills (`tuningagent/skills/`)

10 Claude Skills loaded from `SKILL.md` files with YAML frontmatter. `SkillLoader` (in `tools/skill_loader.py`) discovers and parses them. Skills use progressive disclosure — metadata is loaded eagerly, full content on demand. Each skill becomes a `SkillTool` instance registered alongside regular tools. Skills can be hot-reloaded at runtime via the `/reload` CLI command, which calls `SkillLoader.reload_skills()` and refreshes the system prompt metadata.

### Data Models (`tuningagent/schema/`)

Pydantic v2 models: `Message` (role + content blocks), `LLMResponse`, `ToolCall`, `TokenUsage`. Messages support both string and list-of-blocks content formats.

### Logging (`tuningagent/logger.py`)

Execution logs are written to `~/.mini-agent/log/` as JSON. Records LLM requests, responses, and tool call results.

## Key Design Patterns

- **Async throughout**: Agent loop, LLM clients, and tool execution are all async (asyncio)
- **Cancellation**: `Agent` accepts an `asyncio.Event` for cooperative cancellation mid-loop
- **Retry**: Configurable exponential backoff (1s–60s) with per-client retryable exception types
- **Tool subclass signatures**: Tool subclasses intentionally use explicit parameters rather than `**kwargs` for better type hints (pylint `arguments-differ` is disabled for this reason)

## Pylint Note

`arguments-differ` is globally disabled in `pyproject.toml` because `Tool` subclasses override `execute()` with different explicit parameter signatures by design.
