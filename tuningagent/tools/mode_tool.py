"""Agent mode switching tool (Ask / Plan / Build)."""

from typing import Any

from .base import Tool, ToolResult

# Valid modes
VALID_MODES = ("ask", "plan", "build")

# Tools to remove in read-only modes (ask/plan)
WRITE_TOOLS = {"file_write", "file_edit", "memory_update", "subagent_create"}

# Mode-specific prompt text injected into ## Current Mode
MODE_PROMPTS = {
    "build": """\
## Current Mode
**Mode: BUILD** — Full execution mode. All tools are available.
You may read, write, edit files, run commands, and use any tool to complete the user's task.""",

    "ask": """\
## Current Mode
**Mode: ASK** — Read-only Q&A mode. Write tools are disabled.
Focus on answering the user's questions with precise code references and explanations.
- Use `file_read`, `bash`, `skill_get`, and `subagent_run` for exploration.
- **Bash**: only execute read-only commands (ls, cat, grep, git log, find, etc.). Do NOT run commands that modify files, install packages, or change system state.
- Do NOT attempt to use `file_write`, `file_edit`, `memory_update`, or `subagent_create` — they are not available in this mode.""",

    "plan": """\
## Current Mode
**Mode: PLAN** — Read-only planning mode. Write tools are disabled.
Produce a structured plan with: **Goal**, **Steps**, **Dependencies**, and **Risks**.
- Use `file_read`, `bash`, `skill_get`, and `subagent_run` to research before planning.
- **Bash**: only execute read-only commands (ls, cat, grep, git log, find, etc.). Do NOT run commands that modify files, install packages, or change system state.
- Do NOT attempt to use `file_write`, `file_edit`, `memory_update`, or `subagent_create` — they are not available in this mode.
- When your plan is complete, present it to the user for review. The user may refine the plan through follow-up messages. When the user approves, switch to Build mode via `mode_switch(mode="build")` to begin execution.""",
}


class ModeSwitchTool(Tool):
    """Tool for switching the agent's operating mode."""

    def __init__(self):
        self._agent = None

    def set_context(self, agent):
        """Store reference to the parent agent (called after Agent creation)."""
        self._agent = agent

    @property
    def name(self) -> str:
        return "mode_switch"

    @property
    def description(self) -> str:
        return (
            "Switch the agent's operating mode. "
            "Modes: 'build' (full execution, all tools), "
            "'ask' (read-only Q&A), "
            "'plan' (read-only planning, produces structured plans)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(VALID_MODES),
                    "description": "The mode to switch to: ask, plan, or build.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for switching modes.",
                },
            },
            "required": ["mode"],
        }

    async def execute(self, mode: str, reason: str = "") -> ToolResult:
        if self._agent is None:
            return ToolResult(success=False, error="Mode tool not initialized (no agent context).")

        if mode not in VALID_MODES:
            return ToolResult(
                success=False,
                error=f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}.",
            )

        old_mode = self._agent.mode
        result = self._agent.switch_mode(mode)

        # Compress plan context when switching plan → build
        if old_mode == "plan" and mode == "build":
            await self._agent._summarize_plan_context()

        summary = f"Switched to {mode.upper()} mode. Tools available: {result['tool_count']}."
        if result.get("removed"):
            summary += f" Removed: {', '.join(result['removed'])}."
        if reason:
            summary += f" Reason: {reason}"

        return ToolResult(success=True, content=summary)
