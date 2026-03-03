"""Test cases for Memory Tool (AGENT.md)."""

import tempfile
from pathlib import Path

import pytest

from tuningagent.tools.memory_tool import MemoryTool


async def test_create_memory_file():
    """Writing to a new AGENT.md creates the file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = MemoryTool(workspace_dir=tmpdir)
        result = await tool.execute(content="# Project Memory\n\n- fact one\n")

        assert result.success
        assert Path(tmpdir, "AGENT.md").exists()
        assert Path(tmpdir, "AGENT.md").read_text(encoding="utf-8") == "# Project Memory\n\n- fact one\n"


async def test_full_overwrite():
    """Each call replaces the entire file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = MemoryTool(workspace_dir=tmpdir)
        await tool.execute(content="old content")
        result = await tool.execute(content="new content")

        assert result.success
        assert Path(tmpdir, "AGENT.md").read_text(encoding="utf-8") == "new content"


async def test_char_count_in_response():
    """The result message reports the character count."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = MemoryTool(workspace_dir=tmpdir)
        content = "hello world"
        result = await tool.execute(content=content)

        assert result.success
        assert str(len(content)) in result.content


async def test_schema():
    """Tool schema has the expected shape."""
    tool = MemoryTool()
    assert tool.name == "update_memory"
    params = tool.parameters
    assert "content" in params["properties"]
    assert params["required"] == ["content"]


async def test_creates_parent_dirs():
    """Writing succeeds even if the workspace dir doesn't exist yet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        nested = str(Path(tmpdir) / "a" / "b")
        tool = MemoryTool(workspace_dir=nested)
        result = await tool.execute(content="deep")

        assert result.success
        assert Path(nested, "AGENT.md").read_text(encoding="utf-8") == "deep"
