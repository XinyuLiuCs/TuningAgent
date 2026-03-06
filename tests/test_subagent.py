"""Tests for the subagent system."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tuningagent.schema import LLMResponse, TokenUsage
from tuningagent.tools.base import Tool, ToolResult
from tuningagent.tools.subagent_loader import SubagentConfig, SubagentLoader
from tuningagent.tools.subagent_tool import (
    CreateSubagentTool,
    RunSubagentTool,
    SubagentCancelTool,
    SubagentManager,
    _is_subagent_tool,
    _run_subagent,
    create_subagent_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_mock_llm():
    mock = AsyncMock()
    mock.generate = AsyncMock(
        return_value=LLMResponse(
            content="done",
            thinking=None,
            tool_calls=[],
            finish_reason="stop",
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
    )
    return mock


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
              - file_read
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
        assert config.allowed_tools == ["file_read", "bash"]

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
        assert config.run_in_background is False
        assert config.timeout == 300

    def test_load_background_and_timeout(self, tmp_path):
        yaml_file = tmp_path / "SUBAGENT.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
            name: bg-agent
            description: Background agent
            system_prompt: Do bg things.
            run_in_background: true
            timeout: 600
            """)
        )

        loader = SubagentLoader(str(tmp_path))
        config = loader.load_file(yaml_file)

        assert config is not None
        assert config.run_in_background is True
        assert config.timeout == 600

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

    def test_get_subagents_metadata_prompt_empty(self, tmp_path):
        loader = SubagentLoader(str(tmp_path))
        assert loader.get_subagents_metadata_prompt() == ""

    def test_get_subagents_metadata_prompt(self, tmp_path):
        sub = tmp_path / "agent1"
        sub.mkdir()
        (sub / "SUBAGENT.yaml").write_text(
            "name: explorer\ndescription: Explores code\nsystem_prompt: x\n"
        )
        sub2 = tmp_path / "agent2"
        sub2.mkdir()
        (sub2 / "SUBAGENT.yaml").write_text(
            "name: bg-worker\ndescription: Background work\nsystem_prompt: x\nrun_in_background: true\n"
        )

        loader = SubagentLoader(str(tmp_path))
        loader.discover()
        prompt = loader.get_subagents_metadata_prompt()

        assert "## Available Subagents" in prompt
        assert "`explorer`" in prompt
        assert "`bg-worker` [background]" in prompt
        assert "subagent_run(name, task)" in prompt


# ---------------------------------------------------------------------------
# Tool classification tests
# ---------------------------------------------------------------------------


class TestSubagentToolClassification:
    def test_is_subagent_tool(self):
        loader = SubagentLoader("/nonexistent")
        run = RunSubagentTool(loader)
        dynamic = CreateSubagentTool()
        cancel = SubagentCancelTool()

        assert _is_subagent_tool(run) is True
        assert _is_subagent_tool(dynamic) is True
        assert _is_subagent_tool(cancel) is True

    def test_regular_tool_not_subagent(self):
        assert _is_subagent_tool(DummyTool()) is False


# ---------------------------------------------------------------------------
# RunSubagentTool tests
# ---------------------------------------------------------------------------


class TestRunSubagentTool:
    def _make_loader_with_config(self, **kwargs):
        loader = SubagentLoader("/nonexistent")
        config = SubagentConfig(**kwargs)
        loader.loaded[config.name] = config
        return loader

    def test_name_and_parameters(self):
        loader = SubagentLoader("/nonexistent")
        tool = RunSubagentTool(loader)
        assert tool.name == "subagent_run"
        params = tool.parameters
        assert "name" in params["properties"]
        assert "task" in params["properties"]
        assert params["required"] == ["name", "task"]

    async def test_execute_without_context(self):
        loader = SubagentLoader("/nonexistent")
        tool = RunSubagentTool(loader)
        result = await tool.execute(name="x", task="do something")
        assert not result.success
        assert "not initialized" in result.error

    async def test_unknown_name_lists_available(self):
        loader = self._make_loader_with_config(
            name="explorer", description="Explores code", system_prompt="x"
        )
        tool = RunSubagentTool(loader)
        tool._llm_client = AsyncMock()
        tool._all_tools = []

        result = await tool.execute(name="nonexistent", task="do something")
        assert not result.success
        assert "nonexistent" in result.error
        assert "explorer" in result.error

    async def test_foreground_execution(self):
        loader = self._make_loader_with_config(
            name="reviewer", description="Reviews code", system_prompt="You review."
        )
        tool = RunSubagentTool(loader)
        tool._llm_client = AsyncMock()
        tool._all_tools = []

        with patch("tuningagent.tools.subagent_tool._execute_foreground", new_callable=AsyncMock) as mock_fg:
            mock_fg.return_value = ToolResult(success=True, content="reviewed")
            result = await tool.execute(name="reviewer", task="review this code")
            assert result.success
            assert result.content == "reviewed"
            # Verify correct config values passed
            call_kwargs = mock_fg.call_args.kwargs
            assert call_kwargs["system_prompt"] == "You review."
            assert call_kwargs["label"] == "reviewer"

    async def test_background_execution(self, tmp_path):
        loader = self._make_loader_with_config(
            name="bg-worker",
            description="Background work",
            system_prompt="Work in bg.",
            run_in_background=True,
        )
        tool = RunSubagentTool(loader)
        tool._llm_client = AsyncMock()
        tool._all_tools = []
        tool._workspace_dir = str(tmp_path)

        with patch("tuningagent.tools.subagent_tool._execute_background", new_callable=AsyncMock) as mock_bg:
            mock_bg.return_value = ToolResult(success=True, content="bg started")
            result = await tool.execute(name="bg-worker", task="do background work")
            assert result.success

    async def test_foreground_timeout(self):
        loader = self._make_loader_with_config(
            name="slow", description="Slow", system_prompt="x", timeout=1
        )
        tool = RunSubagentTool(loader)
        tool._llm_client = AsyncMock()
        tool._all_tools = []

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(10)
            return "never"

        with patch("tuningagent.tools.subagent_tool._run_subagent", side_effect=slow_run):
            result = await tool.execute(name="slow", task="do something slow")
            assert not result.success
            assert "timed out" in result.error

    async def test_foreground_timeout_does_not_kill_parent(self):
        """When a foreground subagent times out, the parent cancel_event must NOT be set."""
        parent_cancel = asyncio.Event()

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(10)
            return "never"

        with patch("tuningagent.tools.subagent_tool._run_subagent", side_effect=slow_run):
            from tuningagent.tools.subagent_tool import _execute_foreground

            result = await _execute_foreground(
                llm_client=AsyncMock(),
                system_prompt="x",
                all_tools=[],
                task="slow task",
                max_steps=5,
                token_limit=1000,
                workspace_dir="./workspace",
                allowed_tools=None,
                cancel_event=parent_cancel,
                timeout=1,
                label="slow",
            )
            assert not result.success
            assert "timed out" in result.error
            # Critical: parent cancel_event must remain unset
            assert not parent_cancel.is_set()

    async def test_parent_cancel_propagates_to_foreground_child(self):
        """When the parent cancel_event is set (user Esc), the foreground child should be cancelled."""
        parent_cancel = asyncio.Event()
        captured_child_cancel = {}

        async def blocking_run(*args, **kwargs):
            cancel = kwargs.get("cancel_event")
            captured_child_cancel["event"] = cancel
            # Block until cancelled
            await asyncio.sleep(30)
            return "never"

        with patch("tuningagent.tools.subagent_tool._run_subagent", side_effect=blocking_run):
            from tuningagent.tools.subagent_tool import _execute_foreground

            async def set_parent_cancel():
                await asyncio.sleep(0.1)
                parent_cancel.set()

            asyncio.create_task(set_parent_cancel())

            with pytest.raises(asyncio.CancelledError):
                await _execute_foreground(
                    llm_client=AsyncMock(),
                    system_prompt="x",
                    all_tools=[],
                    task="blocking task",
                    max_steps=5,
                    token_limit=1000,
                    workspace_dir="./workspace",
                    allowed_tools=None,
                    cancel_event=parent_cancel,
                    timeout=30,
                    label="blocking",
                )

            # The child's isolated cancel_event should have been set (propagated from parent)
            assert captured_child_cancel["event"].is_set()

    async def test_cancel_event_transparent(self):
        """Parent cancel_event is passed to _execute_foreground for propagation."""
        loader = self._make_loader_with_config(
            name="x", description="x", system_prompt="x"
        )
        tool = RunSubagentTool(loader)

        mock_parent = AsyncMock()
        parent_cancel = asyncio.Event()
        mock_parent.cancel_event = parent_cancel

        tool._llm_client = AsyncMock()
        tool._all_tools = []
        tool._parent_agent = mock_parent

        with patch("tuningagent.tools.subagent_tool._execute_foreground", new_callable=AsyncMock) as mock_fg:
            mock_fg.return_value = ToolResult(success=True, content="result")
            await tool.execute(name="x", task="do something")
            call_kwargs = mock_fg.call_args.kwargs
            assert call_kwargs["cancel_event"] is parent_cancel


# ---------------------------------------------------------------------------
# CreateSubagentTool tests
# ---------------------------------------------------------------------------


class TestCreateSubagentTool:
    def test_name_and_parameters(self):
        tool = CreateSubagentTool()
        assert tool.name == "subagent_create"
        params = tool.parameters
        assert "task" in params["properties"]
        assert "system_prompt" in params["properties"]
        assert "allowed_tools" in params["properties"]
        assert "run_in_background" in params["properties"]
        assert "timeout" in params["properties"]

    async def test_execute_without_context(self):
        tool = CreateSubagentTool()
        result = await tool.execute(task="x", system_prompt="y")
        assert not result.success
        assert "not initialized" in result.error

    async def test_foreground_timeout(self):
        """Dynamic subagent foreground should respect timeout parameter."""
        tool = CreateSubagentTool()
        tool._llm_client = AsyncMock()
        tool._all_tools = []

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(10)
            return "never"

        with patch("tuningagent.tools.subagent_tool._run_subagent", side_effect=slow_run):
            result = await tool.execute(task="x", system_prompt="y", timeout=1)
            assert not result.success
            assert "timed out" in result.error

    async def test_background_returns_immediately(self, tmp_path):
        """Background dynamic subagent should return subagent_id immediately."""
        tool = CreateSubagentTool()
        tool._llm_client = AsyncMock()
        tool._all_tools = []
        tool._workspace_dir = str(tmp_path)

        with patch("tuningagent.tools.subagent_tool._background_wrapper", new_callable=AsyncMock) as mock_bg:
            mock_bg.return_value = None
            result = await tool.execute(
                task="x", system_prompt="y", run_in_background=True
            )
            assert result.success
            assert "subagent_id" in result.content
            assert ".subagent/" in result.content


# ---------------------------------------------------------------------------
# _run_subagent tests
# ---------------------------------------------------------------------------


class TestRunSubagent:
    async def test_filters_subagent_tools(self):
        """Child agent should not receive any subagent tools."""
        loader = SubagentLoader("/nonexistent")
        run = RunSubagentTool(loader)
        dynamic = CreateSubagentTool()
        cancel = SubagentCancelTool()
        dummy = DummyTool()
        all_tools = [run, dynamic, cancel, dummy]

        mock_llm = _make_mock_llm()

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

    async def test_cancel_event_passed_to_child(self):
        """cancel_event should be forwarded to child agent.run()."""
        mock_llm = AsyncMock()
        cancel_event = asyncio.Event()

        with patch("tuningagent.agent.Agent") as MockAgent:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value="ok")
            MockAgent.return_value = mock_instance

            await _run_subagent(
                llm_client=mock_llm,
                system_prompt="test",
                tools=[],
                task="do it",
                cancel_event=cancel_event,
            )

            mock_instance.run.assert_called_once_with(cancel_event=cancel_event)


# ---------------------------------------------------------------------------
# SubagentManager tests
# ---------------------------------------------------------------------------


class TestSubagentManager:
    def setup_method(self):
        """Clean up manager state between tests."""
        SubagentManager._tasks.clear()
        SubagentManager._cancel_events.clear()

    async def test_start_and_is_running(self):
        event = asyncio.Event()

        async def long_coro():
            await asyncio.sleep(100)

        SubagentManager.start("test-1", long_coro(), event)
        assert SubagentManager.is_running("test-1")

        # Cleanup
        SubagentManager.cancel("test-1")
        await asyncio.sleep(0.05)
        SubagentManager.cleanup("test-1")

    async def test_cancel_sets_event(self):
        event = asyncio.Event()

        async def wait_coro():
            await asyncio.sleep(100)

        SubagentManager.start("test-2", wait_coro(), event)
        assert SubagentManager.cancel("test-2")
        assert event.is_set()

        # Cleanup
        await asyncio.sleep(0.05)
        SubagentManager.cleanup("test-2")

    def test_cancel_nonexistent(self):
        assert SubagentManager.cancel("nonexistent") is False

    async def test_cancel_all(self):
        e1 = asyncio.Event()
        e2 = asyncio.Event()

        async def noop():
            await asyncio.sleep(100)

        SubagentManager.start("bg-1", noop(), e1)
        SubagentManager.start("bg-2", noop(), e2)

        count = SubagentManager.cancel_all()
        assert count == 2
        assert e1.is_set()
        assert e2.is_set()

        # Cleanup
        await asyncio.sleep(0.05)
        SubagentManager.cleanup("bg-1")
        SubagentManager.cleanup("bg-2")

    async def test_cleanup_removes_tracking(self):
        event = asyncio.Event()

        async def quick():
            pass

        SubagentManager.start("test-3", quick(), event)
        await asyncio.sleep(0.05)  # let task finish
        SubagentManager.cleanup("test-3")

        assert not SubagentManager.is_running("test-3")
        assert "test-3" not in SubagentManager._tasks
        assert "test-3" not in SubagentManager._cancel_events


# ---------------------------------------------------------------------------
# SubagentCancelTool tests
# ---------------------------------------------------------------------------


class TestSubagentCancelTool:
    def setup_method(self):
        SubagentManager._tasks.clear()
        SubagentManager._cancel_events.clear()

    def test_name_and_schema(self):
        tool = SubagentCancelTool()
        assert tool.name == "subagent_cancel"
        assert "subagent_id" in tool.parameters["properties"]

    async def test_cancel_existing(self):
        event = asyncio.Event()

        async def noop():
            await asyncio.sleep(100)

        SubagentManager.start("cancel-me", noop(), event)

        tool = SubagentCancelTool()
        result = await tool.execute(subagent_id="cancel-me")
        assert result.success
        assert event.is_set()

        # Cleanup
        await asyncio.sleep(0.05)
        SubagentManager.cleanup("cancel-me")

    async def test_cancel_nonexistent(self):
        tool = SubagentCancelTool()
        result = await tool.execute(subagent_id="no-such-id")
        assert not result.success
        assert "not found" in result.error


# ---------------------------------------------------------------------------
# Background wrapper tests
# ---------------------------------------------------------------------------


class TestBackgroundWrapper:
    def setup_method(self):
        SubagentManager._tasks.clear()
        SubagentManager._cancel_events.clear()

    async def test_fallback_file_written_on_crash(self, tmp_path):
        """If subagent crashes without writing output, framework writes fallback."""
        from tuningagent.tools.subagent_tool import _background_wrapper

        output_path = tmp_path / ".subagent" / "crash-test.md"
        cancel_event = asyncio.Event()

        # Register in manager so cleanup works
        async def do_nothing():
            pass

        SubagentManager.start("crash-test", do_nothing(), cancel_event)

        # Patch _run_subagent to raise
        with patch(
            "tuningagent.tools.subagent_tool._run_subagent",
            side_effect=RuntimeError("boom"),
        ):
            await _background_wrapper(
                subagent_id="crash-test",
                output_path=output_path,
                cancel_event=cancel_event,
                llm_client=AsyncMock(),
                system_prompt="x",
                tools=[],
                task="crash",
                max_steps=5,
                token_limit=1000,
                workspace_dir=str(tmp_path),
                allowed_tools=None,
            )

        assert output_path.exists()
        content = output_path.read_text()
        assert "crash-test" in content
        assert "boom" in content

    async def test_no_fallback_if_file_exists(self, tmp_path):
        """If subagent wrote output, framework should not overwrite it."""
        from tuningagent.tools.subagent_tool import _background_wrapper

        output_dir = tmp_path / ".subagent"
        output_dir.mkdir(parents=True)
        output_path = output_dir / "good-test.md"
        output_path.write_text("Subagent result here.")

        cancel_event = asyncio.Event()

        async def do_nothing():
            pass

        SubagentManager.start("good-test", do_nothing(), cancel_event)

        with patch(
            "tuningagent.tools.subagent_tool._run_subagent",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            await _background_wrapper(
                subagent_id="good-test",
                output_path=output_path,
                cancel_event=cancel_event,
                llm_client=AsyncMock(),
                system_prompt="x",
                tools=[],
                task="good",
                max_steps=5,
                token_limit=1000,
                workspace_dir=str(tmp_path),
                allowed_tools=None,
            )

        # Original content should be preserved
        assert output_path.read_text() == "Subagent result here."


# ---------------------------------------------------------------------------
# create_subagent_tools factory tests
# ---------------------------------------------------------------------------


class TestCreateSubagentToolsFactory:
    def test_always_returns_three_tools(self, tmp_path):
        """Factory should always return exactly 3 tools: subagent_run, subagent_create, subagent_cancel."""
        tools, loader = create_subagent_tools(str(tmp_path))
        assert len(tools) == 3
        assert any(isinstance(t, RunSubagentTool) for t in tools)
        assert any(isinstance(t, CreateSubagentTool) for t in tools)
        assert any(isinstance(t, SubagentCancelTool) for t in tools)

    def test_loader_discovers_configs(self, tmp_path):
        sub = tmp_path / "my-agent"
        sub.mkdir()
        (sub / "SUBAGENT.yaml").write_text(
            "name: my-agent\ndescription: Test\nsystem_prompt: Hello\n"
        )

        tools, loader = create_subagent_tools(str(tmp_path))
        assert len(tools) == 3  # always 3 gateway tools
        assert "my-agent" in loader.loaded
