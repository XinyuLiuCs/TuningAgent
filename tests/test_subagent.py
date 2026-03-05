"""Tests for the subagent system."""

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tuningagent.schema import LLMResponse, TokenUsage
from tuningagent.tools.base import Tool, ToolResult
from tuningagent.tools.subagent_loader import SubagentConfig, SubagentLoader
from tuningagent.tools.subagent_tool import (
    CreateSubagentTool,
    FixedSubagentTool,
    _is_subagent_tool,
    _run_subagent,
    create_subagent_tools,
)


# ---------------------------------------------------------------------------
# SubagentLoader tests
# ---------------------------------------------------------------------------


class TestSubagentLoader:
    def test_load_valid_yaml(self, tmp_path):
        yaml_file = tmp_path / "SUBAGENT.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
            name: test-agent
            description: A test subagent
            system_prompt: You are a test agent.
            max_steps: 10
            allowed_tools:
              - read_file
              - bash
            """)
        )

        loader = SubagentLoader(str(tmp_path))
        config = loader.load_file(yaml_file)

        assert config is not None
        assert config.name == "test-agent"
        assert config.description == "A test subagent"
        assert config.system_prompt.strip() == "You are a test agent."
        assert config.max_steps == 10
        assert config.allowed_tools == ["read_file", "bash"]

    def test_load_missing_fields(self, tmp_path):
        yaml_file = tmp_path / "SUBAGENT.yaml"
        yaml_file.write_text("name: incomplete\n")

        loader = SubagentLoader(str(tmp_path))
        config = loader.load_file(yaml_file)
        assert config is None

    def test_load_defaults(self, tmp_path):
        yaml_file = tmp_path / "SUBAGENT.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
            name: minimal
            description: Minimal agent
            system_prompt: Do things.
            """)
        )

        loader = SubagentLoader(str(tmp_path))
        config = loader.load_file(yaml_file)

        assert config is not None
        assert config.max_steps == 30
        assert config.allowed_tools is None

    def test_discover(self, tmp_path):
        sub1 = tmp_path / "agent1"
        sub1.mkdir()
        (sub1 / "SUBAGENT.yaml").write_text(
            "name: a1\ndescription: Agent 1\nsystem_prompt: Hello\n"
        )

        sub2 = tmp_path / "agent2"
        sub2.mkdir()
        (sub2 / "SUBAGENT.yaml").write_text(
            "name: a2\ndescription: Agent 2\nsystem_prompt: World\n"
        )

        loader = SubagentLoader(str(tmp_path))
        configs = loader.discover()

        assert len(configs) == 2
        names = {c.name for c in configs}
        assert names == {"a1", "a2"}

    def test_discover_empty_dir(self, tmp_path):
        loader = SubagentLoader(str(tmp_path))
        assert loader.discover() == []

    def test_discover_nonexistent_dir(self):
        loader = SubagentLoader("/nonexistent/path")
        assert loader.discover() == []

    def test_reload(self, tmp_path):
        sub = tmp_path / "agent1"
        sub.mkdir()
        (sub / "SUBAGENT.yaml").write_text(
            "name: a1\ndescription: Agent 1\nsystem_prompt: Hello\n"
        )

        loader = SubagentLoader(str(tmp_path))
        loader.discover()
        assert "a1" in loader.loaded

        # Add a new one
        sub2 = tmp_path / "agent2"
        sub2.mkdir()
        (sub2 / "SUBAGENT.yaml").write_text(
            "name: a2\ndescription: Agent 2\nsystem_prompt: World\n"
        )

        result = loader.reload()
        assert "a2" in result["added"]
        assert result["total"] == 2


# ---------------------------------------------------------------------------
# Tool classification tests
# ---------------------------------------------------------------------------


class TestSubagentToolClassification:
    def test_is_subagent_tool(self):
        config = SubagentConfig(name="x", description="x", system_prompt="x")
        fixed = FixedSubagentTool(config)
        dynamic = CreateSubagentTool()

        assert _is_subagent_tool(fixed) is True
        assert _is_subagent_tool(dynamic) is True

    def test_regular_tool_not_subagent(self):
        class DummyTool(Tool):
            @property
            def name(self):
                return "dummy"

            @property
            def description(self):
                return "dummy"

            @property
            def parameters(self):
                return {"type": "object", "properties": {}}

            async def execute(self):
                return ToolResult(success=True, content="ok")

        assert _is_subagent_tool(DummyTool()) is False


# ---------------------------------------------------------------------------
# FixedSubagentTool tests
# ---------------------------------------------------------------------------


class TestFixedSubagentTool:
    def test_name_and_description(self):
        config = SubagentConfig(
            name="reviewer", description="Reviews code", system_prompt="You review."
        )
        tool = FixedSubagentTool(config)
        assert tool.name == "subagent_reviewer"
        assert "Reviews code" in tool.description

    def test_parameters_schema(self):
        config = SubagentConfig(name="x", description="x", system_prompt="x")
        tool = FixedSubagentTool(config)
        params = tool.parameters
        assert "task" in params["properties"]
        assert params["required"] == ["task"]

    async def test_execute_without_context(self):
        config = SubagentConfig(name="x", description="x", system_prompt="x")
        tool = FixedSubagentTool(config)
        result = await tool.execute(task="do something")
        assert not result.success
        assert "not initialized" in result.error


# ---------------------------------------------------------------------------
# CreateSubagentTool tests
# ---------------------------------------------------------------------------


class TestCreateSubagentTool:
    def test_name_and_parameters(self):
        tool = CreateSubagentTool()
        assert tool.name == "create_subagent"
        params = tool.parameters
        assert "task" in params["properties"]
        assert "system_prompt" in params["properties"]
        assert "allowed_tools" in params["properties"]

    async def test_execute_without_context(self):
        tool = CreateSubagentTool()
        result = await tool.execute(task="x", system_prompt="y")
        assert not result.success
        assert "not initialized" in result.error


# ---------------------------------------------------------------------------
# _run_subagent tests
# ---------------------------------------------------------------------------


class TestRunSubagent:
    async def test_filters_subagent_tools(self):
        """Child agent should not receive any subagent tools."""
        config = SubagentConfig(name="x", description="x", system_prompt="x")
        fixed = FixedSubagentTool(config)
        dynamic = CreateSubagentTool()

        class DummyTool(Tool):
            @property
            def name(self):
                return "dummy"

            @property
            def description(self):
                return "dummy"

            @property
            def parameters(self):
                return {"type": "object", "properties": {}}

            async def execute(self):
                return ToolResult(success=True, content="ok")

        dummy = DummyTool()
        all_tools = [fixed, dynamic, dummy]

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="done",
                thinking=None,
                tool_calls=[],
                finish_reason="stop",
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )

        with patch("tuningagent.agent.Agent") as MockAgent:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value="child result")
            MockAgent.return_value = mock_instance

            result = await _run_subagent(
                llm_client=mock_llm,
                system_prompt="test",
                tools=all_tools,
                task="do something",
            )

            # Verify Agent was created with only the dummy tool
            call_kwargs = MockAgent.call_args
            child_tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
            assert len(child_tools) == 1
            assert child_tools[0].name == "dummy"
            assert result == "child result"

    async def test_applies_allowed_tools_whitelist(self):
        """When allowed_tools is set, only those tools should pass through."""

        class ToolA(Tool):
            @property
            def name(self):
                return "tool_a"

            @property
            def description(self):
                return "A"

            @property
            def parameters(self):
                return {"type": "object", "properties": {}}

            async def execute(self):
                return ToolResult(success=True)

        class ToolB(Tool):
            @property
            def name(self):
                return "tool_b"

            @property
            def description(self):
                return "B"

            @property
            def parameters(self):
                return {"type": "object", "properties": {}}

            async def execute(self):
                return ToolResult(success=True)

        mock_llm = AsyncMock()

        with patch("tuningagent.agent.Agent") as MockAgent:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value="ok")
            MockAgent.return_value = mock_instance

            await _run_subagent(
                llm_client=mock_llm,
                system_prompt="test",
                tools=[ToolA(), ToolB()],
                task="do it",
                allowed_tools=["tool_a"],
            )

            call_kwargs = MockAgent.call_args
            child_tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
            assert len(child_tools) == 1
            assert child_tools[0].name == "tool_a"


# ---------------------------------------------------------------------------
# create_subagent_tools factory tests
# ---------------------------------------------------------------------------


class TestCreateSubagentToolsFactory:
    def test_always_includes_dynamic_tool(self, tmp_path):
        tools, loader = create_subagent_tools(str(tmp_path))
        assert any(isinstance(t, CreateSubagentTool) for t in tools)

    def test_loads_fixed_subagents(self, tmp_path):
        sub = tmp_path / "my-agent"
        sub.mkdir()
        (sub / "SUBAGENT.yaml").write_text(
            "name: my-agent\ndescription: Test\nsystem_prompt: Hello\n"
        )

        tools, loader = create_subagent_tools(str(tmp_path))
        fixed = [t for t in tools if isinstance(t, FixedSubagentTool)]
        assert len(fixed) == 1
        assert fixed[0].name == "subagent_my-agent"
