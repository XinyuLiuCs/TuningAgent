"""Tests for Agent mode system (Ask / Plan / Build)."""

import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from tuningagent.agent import Agent
from tuningagent.schema import Message
from tuningagent.tools.base import Tool, ToolResult
from tuningagent.tools.mode_tool import (
    MODE_PROMPTS,
    VALID_MODES,
    WRITE_TOOLS,
    ModeSwitchTool,
)


# ---------------------------------------------------------------------------
# Helpers: minimal stub tools that mimic real tool names
# ---------------------------------------------------------------------------

class StubTool(Tool):
    """A no-op tool with a configurable name."""

    def __init__(self, tool_name: str):
        self._name = tool_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Stub for {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self) -> ToolResult:
        return ToolResult(success=True, content="ok")


ALL_TOOL_NAMES = [
    "bash",
    "bash_output",
    "bash_kill",
    "file_read",
    "file_write",
    "file_edit",
    "memory_update",
    "skill_get",
    "subagent_run",
    "subagent_create",
    "subagent_cancel",
]


def _make_agent(tool_names: list[str] | None = None) -> Agent:
    """Create an Agent with stub tools and a mock LLM client."""
    if tool_names is None:
        tool_names = list(ALL_TOOL_NAMES)

    tools = [StubTool(n) for n in tool_names]
    llm = MagicMock()

    with tempfile.TemporaryDirectory() as ws:
        agent = Agent(
            llm_client=llm,
            system_prompt="System prompt.\n\n## Current Mode\nplaceholder\n\n## Workspace Context\nws",
            tools=tools,
            workspace_dir=ws,
        )
    return agent


# ---------------------------------------------------------------------------
# Tests: switch_mode
# ---------------------------------------------------------------------------

class TestSwitchMode:
    def test_default_mode_is_build(self):
        agent = _make_agent()
        assert agent.mode == "build"

    def test_switch_to_ask(self):
        agent = _make_agent()
        result = agent.switch_mode("ask")
        assert result["new_mode"] == "ask"
        assert agent.mode == "ask"

    def test_switch_to_plan(self):
        agent = _make_agent()
        result = agent.switch_mode("plan")
        assert result["new_mode"] == "plan"
        assert agent.mode == "plan"

    def test_switch_back_to_build(self):
        agent = _make_agent()
        agent.switch_mode("ask")
        result = agent.switch_mode("build")
        assert result["new_mode"] == "build"
        assert agent.mode == "build"

    def test_invalid_mode_returns_error(self):
        agent = _make_agent()
        result = agent.switch_mode("invalid")
        assert "error" in result
        assert agent.mode == "build"  # unchanged


# ---------------------------------------------------------------------------
# Tests: tool filtering
# ---------------------------------------------------------------------------

class TestToolFiltering:
    def test_build_mode_has_all_tools(self):
        agent = _make_agent()
        assert set(agent.tools.keys()) == set(ALL_TOOL_NAMES)

    def test_ask_mode_removes_write_tools(self):
        agent = _make_agent()
        agent.switch_mode("ask")
        for wt in WRITE_TOOLS:
            assert wt not in agent.tools, f"{wt} should be removed in ask mode"

    def test_plan_mode_removes_write_tools(self):
        agent = _make_agent()
        agent.switch_mode("plan")
        for wt in WRITE_TOOLS:
            assert wt not in agent.tools, f"{wt} should be removed in plan mode"

    def test_ask_mode_retains_read_tools(self):
        agent = _make_agent()
        agent.switch_mode("ask")
        for name in ["bash", "bash_output", "file_read", "skill_get", "subagent_run", "subagent_cancel"]:
            assert name in agent.tools, f"{name} should be retained in ask mode"

    def test_plan_mode_retains_read_tools(self):
        agent = _make_agent()
        agent.switch_mode("plan")
        for name in ["bash", "bash_output", "file_read", "skill_get", "subagent_run", "subagent_cancel"]:
            assert name in agent.tools, f"{name} should be retained in plan mode"

    def test_switch_result_lists_removed_tools(self):
        agent = _make_agent()
        result = agent.switch_mode("ask")
        assert set(result["removed"]) == WRITE_TOOLS

    def test_build_restores_all_tools(self):
        agent = _make_agent()
        agent.switch_mode("plan")
        assert len(agent.tools) < len(agent._all_tools)
        agent.switch_mode("build")
        assert set(agent.tools.keys()) == set(agent._all_tools.keys())


# ---------------------------------------------------------------------------
# Tests: system prompt injection
# ---------------------------------------------------------------------------

class TestModePrompt:
    def test_initial_prompt_contains_build_mode(self):
        agent = _make_agent()
        sys_content = agent.messages[0].content
        assert "Mode: BUILD" in sys_content

    def test_switch_updates_prompt_to_ask(self):
        agent = _make_agent()
        agent.switch_mode("ask")
        sys_content = agent.messages[0].content
        assert "Mode: ASK" in sys_content
        assert "Mode: BUILD" not in sys_content

    def test_switch_updates_prompt_to_plan(self):
        agent = _make_agent()
        agent.switch_mode("plan")
        sys_content = agent.messages[0].content
        assert "Mode: PLAN" in sys_content
        assert "Mode: BUILD" not in sys_content

    def test_switch_back_restores_build_prompt(self):
        agent = _make_agent()
        agent.switch_mode("ask")
        agent.switch_mode("build")
        sys_content = agent.messages[0].content
        assert "Mode: BUILD" in sys_content
        assert "Mode: ASK" not in sys_content

    def test_workspace_context_preserved(self):
        agent = _make_agent()
        agent.switch_mode("plan")
        sys_content = agent.messages[0].content
        assert "## Workspace Context" in sys_content or "## Current Workspace" in sys_content


# ---------------------------------------------------------------------------
# Tests: ModeSwitchTool
# ---------------------------------------------------------------------------

class TestModeSwitchTool:
    def test_tool_properties(self):
        tool = ModeSwitchTool()
        assert tool.name == "mode_switch"
        assert "mode" in tool.parameters["properties"]

    async def test_execute_switches_mode(self):
        agent = _make_agent()
        tool = ModeSwitchTool()
        tool.set_context(agent)
        result = await tool.execute(mode="ask")
        assert result.success
        assert agent.mode == "ask"
        assert "ASK" in result.content

    async def test_execute_invalid_mode(self):
        agent = _make_agent()
        tool = ModeSwitchTool()
        tool.set_context(agent)
        result = await tool.execute(mode="unknown")
        assert not result.success

    async def test_execute_without_context(self):
        tool = ModeSwitchTool()
        result = await tool.execute(mode="ask")
        assert not result.success

    async def test_execute_with_reason(self):
        agent = _make_agent()
        tool = ModeSwitchTool()
        tool.set_context(agent)
        result = await tool.execute(mode="plan", reason="need to plan first")
        assert result.success
        assert "need to plan first" in result.content
