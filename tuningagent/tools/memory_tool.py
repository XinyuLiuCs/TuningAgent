"""Memory Tool - Project-level persistent memory via AGENT.md.

The agent's memory is stored as a human-readable Markdown file (AGENT.md)
in the workspace root.  At session start the file is loaded into the system
prompt, so the agent already *has* its memory — this tool only needs to
support writing (full overwrite).
"""

from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


class MemoryTool(Tool):
    """Write the full contents of the project memory file (AGENT.md).

    The agent decides what to keep, add, or remove and writes the complete
    file each time.  Because AGENT.md is injected into the system prompt at
    startup, no separate read action is needed.
    """

    def __init__(self, workspace_dir: str = "./workspace"):
        self.memory_path = Path(workspace_dir) / "AGENT.md"

    @property
    def name(self) -> str:
        return "memory_update"

    @property
    def description(self) -> str:
        return (
            "Overwrite the project memory file (AGENT.md) with the given Markdown content. "
            "Use this to persist important facts, decisions, or context across sessions. "
            "You must provide the complete file content — previous content is replaced entirely."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Complete Markdown content for AGENT.md.",
                },
            },
            "required": ["content"],
        }

    async def execute(self, content: str) -> ToolResult:
        """Write *content* to AGENT.md, creating parent dirs if needed."""
        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.memory_path.write_text(content, encoding="utf-8")
            char_count = len(content)
            return ToolResult(
                success=True,
                content=f"AGENT.md updated ({char_count} chars).",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"Failed to update AGENT.md: {e}",
            )
