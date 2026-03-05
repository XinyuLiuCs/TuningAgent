"""
Subagent Tools - Allow the main agent to delegate tasks to child agents.

Supports two modes:
- FixedSubagentTool: pre-defined subagent from SUBAGENT.yaml
- CreateSubagentTool: dynamic subagent created at runtime by the LLM

Child agents run in isolated contexts with no access to subagent tools
(single-level delegation only).
"""

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult
from .subagent_loader import SubagentConfig, SubagentLoader

# Sentinel names used to filter subagent tools from child agents
SUBAGENT_TOOL_PREFIX = "subagent_"
CREATE_SUBAGENT_TOOL_NAME = "create_subagent"


def _is_subagent_tool(tool: Tool) -> bool:
    """Check if a tool is a subagent tool (should be excluded from child agents)."""
    return isinstance(tool, (FixedSubagentTool, CreateSubagentTool))


async def _run_subagent(
    llm_client,
    system_prompt: str,
    tools: List[Tool],
    task: str,
    max_steps: int = 30,
    token_limit: int = 80000,
    workspace_dir: str = "./workspace",
    allowed_tools: Optional[List[str]] = None,
) -> str:
    """Create and run a child agent, returning only its final result.

    This is the core execution function shared by both Fixed and Dynamic
    subagent tools. It enforces:
    - Context isolation: fresh Agent with its own message history
    - Single-level delegation: all subagent tools are filtered out
    - Tool restriction: only allowed_tools are passed (if specified)

    Args:
        llm_client: LLM client (shared with main agent).
        system_prompt: System prompt for the child agent.
        tools: Full tool list from the main agent.
        task: The task/prompt to send to the child agent.
        max_steps: Maximum execution steps for the child agent.
        token_limit: Token limit for triggering summarization.
        workspace_dir: Workspace directory (inherited from main agent).
        allowed_tools: If set, only these tool names are available to the child.

    Returns:
        The child agent's final response string.
    """
    # Import here to avoid circular dependency (agent imports tools)
    from ..agent import Agent

    # Step 1: Filter out all subagent tools (prevent recursion)
    filtered = [t for t in tools if not _is_subagent_tool(t)]

    # Step 2: Apply allowed_tools whitelist if specified
    if allowed_tools is not None:
        allowed_set = set(allowed_tools)
        filtered = [t for t in filtered if t.name in allowed_set]

    # Step 3: Create and run child agent with isolated context
    child = Agent(
        llm_client=llm_client,
        system_prompt=system_prompt,
        tools=filtered,
        max_steps=max_steps,
        token_limit=token_limit,
        workspace_dir=workspace_dir,
    )
    child.add_user_message(task)
    result = await child.run()
    return result


class FixedSubagentTool(Tool):
    """A tool that delegates a task to a pre-defined subagent."""

    def __init__(
        self,
        config: SubagentConfig,
        llm_client=None,
        all_tools: Optional[List[Tool]] = None,
    ):
        self.config = config
        self._llm_client = llm_client
        self._all_tools: List[Tool] = all_tools or []
        self._workspace_dir: str = "./workspace"

    def set_context(self, llm_client, all_tools: List[Tool], workspace_dir: str = "./workspace"):
        """Inject runtime dependencies (called after all tools are assembled)."""
        self._llm_client = llm_client
        self._all_tools = all_tools
        self._workspace_dir = workspace_dir

    @property
    def name(self) -> str:
        return f"{SUBAGENT_TOOL_PREFIX}{self.config.name}"

    @property
    def description(self) -> str:
        return f"[Subagent] {self.config.description}"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task to delegate to this subagent. Be specific and provide all necessary context.",
                }
            },
            "required": ["task"],
        }

    async def execute(self, task: str) -> ToolResult:
        if not self._llm_client:
            return ToolResult(success=False, error="Subagent not initialized (missing llm_client)")

        try:
            result = await _run_subagent(
                llm_client=self._llm_client,
                system_prompt=self.config.system_prompt,
                tools=self._all_tools,
                task=task,
                max_steps=self.config.max_steps,
                token_limit=self.config.token_limit,
                workspace_dir=self._workspace_dir,
                allowed_tools=self.config.allowed_tools,
            )
            return ToolResult(success=True, content=result)
        except Exception as e:
            return ToolResult(success=False, error=f"Subagent execution failed: {e}")


class CreateSubagentTool(Tool):
    """A tool that lets the LLM create and run a subagent dynamically."""

    def __init__(
        self,
        llm_client=None,
        all_tools: Optional[List[Tool]] = None,
        default_max_steps: int = 30,
        default_token_limit: int = 80000,
    ):
        self._llm_client = llm_client
        self._all_tools: List[Tool] = all_tools or []
        self._default_max_steps = default_max_steps
        self._default_token_limit = default_token_limit
        self._workspace_dir: str = "./workspace"

    def set_context(self, llm_client, all_tools: List[Tool], workspace_dir: str = "./workspace"):
        """Inject runtime dependencies (called after all tools are assembled)."""
        self._llm_client = llm_client
        self._all_tools = all_tools
        self._workspace_dir = workspace_dir

    @property
    def name(self) -> str:
        return CREATE_SUBAGENT_TOOL_NAME

    @property
    def description(self) -> str:
        return (
            "[Subagent] Create and run a temporary subagent for a specific task. "
            "Use this when no pre-defined subagent fits the task. "
            "The subagent runs in an isolated context and returns only its final result."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        # Build the list of available (non-subagent) tool names for the enum hint
        available = [t.name for t in self._all_tools if not _is_subagent_tool(t)]
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task to delegate. Be specific and include all necessary context.",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "System prompt defining the subagent's role and constraints.",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Tool whitelist for the subagent. Available: {available}. Omit to allow all non-subagent tools.",
                },
            },
            "required": ["task", "system_prompt"],
        }

    async def execute(
        self,
        task: str,
        system_prompt: str,
        allowed_tools: Optional[List[str]] = None,
    ) -> ToolResult:
        if not self._llm_client:
            return ToolResult(success=False, error="Subagent not initialized (missing llm_client)")

        try:
            result = await _run_subagent(
                llm_client=self._llm_client,
                system_prompt=system_prompt,
                tools=self._all_tools,
                task=task,
                max_steps=self._default_max_steps,
                token_limit=self._default_token_limit,
                workspace_dir=self._workspace_dir,
                allowed_tools=allowed_tools,
            )
            return ToolResult(success=True, content=result)
        except Exception as e:
            return ToolResult(success=False, error=f"Subagent execution failed: {e}")


def create_subagent_tools(
    subagents_dir: str = "./subagents",
) -> tuple[List[Tool], Optional[SubagentLoader]]:
    """Discover fixed subagents and create all subagent tools.

    Returns:
        Tuple of (list of subagent tools, loader instance).
        The tools need set_context() called before use.
    """
    loader = SubagentLoader(subagents_dir)
    configs = loader.discover()

    tools: List[Tool] = []

    # Fixed subagent tools
    for config in configs:
        tools.append(FixedSubagentTool(config))

    # Dynamic subagent tool (always available)
    tools.append(CreateSubagentTool())

    return tools, loader
