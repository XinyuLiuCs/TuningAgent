"""
Subagent Tools - Allow the main agent to delegate tasks to child agents.

Supports two modes:
- RunSubagentTool: gateway tool that dispatches to pre-defined subagents by name
- CreateSubagentTool: dynamic subagent created at runtime by the LLM

Execution modes:
- Foreground (default): blocking with cancel_event transparency and timeout
- Background: non-blocking, result written to .subagent/{id}.md

Child agents run in isolated contexts with no access to subagent tools
(single-level delegation only).
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult
from .subagent_loader import SubagentConfig, SubagentLoader

# Sentinel names used to filter subagent tools from child agents
RUN_SUBAGENT_TOOL_NAME = "run_subagent"
CREATE_SUBAGENT_TOOL_NAME = "create_subagent"
SUBAGENT_CANCEL_TOOL_NAME = "subagent_cancel"

# Default timeout for foreground subagent execution (seconds)
DEFAULT_TIMEOUT = 300


def _is_subagent_tool(tool: Tool) -> bool:
    """Check if a tool is a subagent tool (should be excluded from child agents)."""
    return isinstance(tool, (RunSubagentTool, CreateSubagentTool, SubagentCancelTool))


# ---------------------------------------------------------------------------
# SubagentManager — manages background task lifecycle
# ---------------------------------------------------------------------------


class SubagentManager:
    """Manages background subagent asyncio.Task lifecycle.

    Does not store results — results live on the filesystem.
    """

    _tasks: dict[str, asyncio.Task] = {}
    _cancel_events: dict[str, asyncio.Event] = {}

    @classmethod
    def start(cls, subagent_id: str, coro, cancel_event: asyncio.Event) -> None:
        """Start a background subagent task."""
        task = asyncio.create_task(coro)
        cls._tasks[subagent_id] = task
        cls._cancel_events[subagent_id] = cancel_event

    @classmethod
    def cancel(cls, subagent_id: str) -> bool:
        """Cancel a specific background subagent. Returns True if found."""
        event = cls._cancel_events.get(subagent_id)
        if event is None:
            return False
        event.set()
        return True

    @classmethod
    def cancel_all(cls) -> int:
        """Cancel all running background subagents. Returns count cancelled."""
        count = 0
        for sid, event in list(cls._cancel_events.items()):
            if sid in cls._tasks and not cls._tasks[sid].done():
                event.set()
                count += 1
        return count

    @classmethod
    def is_running(cls, subagent_id: str) -> bool:
        """Check if a background subagent is still running."""
        task = cls._tasks.get(subagent_id)
        return task is not None and not task.done()

    @classmethod
    def cleanup(cls, subagent_id: str) -> None:
        """Remove a completed subagent from tracking."""
        cls._tasks.pop(subagent_id, None)
        cls._cancel_events.pop(subagent_id, None)


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------


async def _run_subagent(
    llm_client,
    system_prompt: str,
    tools: List[Tool],
    task: str,
    max_steps: int = 30,
    token_limit: int = 80000,
    workspace_dir: str = "./workspace",
    allowed_tools: Optional[List[str]] = None,
    cancel_event: Optional[asyncio.Event] = None,
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
        cancel_event: Optional cancellation event for cooperative stopping.

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
    result = await child.run(cancel_event=cancel_event)
    return result


async def _background_wrapper(
    subagent_id: str,
    output_path: Path,
    cancel_event: asyncio.Event,
    llm_client,
    system_prompt: str,
    tools: List[Tool],
    task: str,
    max_steps: int,
    token_limit: int,
    workspace_dir: str,
    allowed_tools: Optional[List[str]],
) -> None:
    """Wrapper that runs a subagent in background and ensures output file exists."""
    try:
        await _run_subagent(
            llm_client=llm_client,
            system_prompt=system_prompt,
            tools=tools,
            task=task,
            max_steps=max_steps,
            token_limit=token_limit,
            workspace_dir=workspace_dir,
            allowed_tools=allowed_tools,
            cancel_event=cancel_event,
        )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        # Write error to output file so main agent can discover the failure
        if not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                f"## Error\n\n"
                f"Subagent {subagent_id} failed with error:\n\n```\n{e}\n```\n"
            )
    finally:
        # Fallback: if subagent didn't write the output file, write a notice
        if not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                f"Subagent {subagent_id} ended without writing result.\n"
                "This may indicate cancellation or the subagent "
                "forgot to use write_file."
            )
        SubagentManager.cleanup(subagent_id)


# ---------------------------------------------------------------------------
# Foreground/background execution helpers (shared by RunSubagentTool and CreateSubagentTool)
# ---------------------------------------------------------------------------


async def _execute_foreground(
    llm_client,
    system_prompt: str,
    all_tools: List[Tool],
    task: str,
    max_steps: int,
    token_limit: int,
    workspace_dir: str,
    allowed_tools: Optional[List[str]],
    cancel_event: Optional[asyncio.Event],
    timeout: int,
    label: str,
) -> ToolResult:
    """Run subagent in foreground with cooperative timeout and cancel_event."""
    child_task = asyncio.create_task(
        _run_subagent(
            llm_client=llm_client,
            system_prompt=system_prompt,
            tools=all_tools,
            task=task,
            max_steps=max_steps,
            token_limit=token_limit,
            workspace_dir=workspace_dir,
            allowed_tools=allowed_tools,
            cancel_event=cancel_event,
        )
    )
    try:
        done, _ = await asyncio.wait({child_task}, timeout=timeout)
        if child_task in done:
            return ToolResult(success=True, content=child_task.result())
        # Timeout: signal cooperative cancellation first
        if cancel_event is not None:
            cancel_event.set()
        # Grace period for clean shutdown
        try:
            await asyncio.wait_for(asyncio.shield(child_task), timeout=5)
            return ToolResult(success=True, content=child_task.result())
        except asyncio.TimeoutError:
            child_task.cancel()
            return ToolResult(
                success=False,
                error=f"Subagent '{label}' timed out after {timeout}s",
            )
    except asyncio.CancelledError:
        child_task.cancel()
        raise
    except Exception as e:
        return ToolResult(success=False, error=f"Subagent execution failed: {e}")


async def _execute_background(
    llm_client,
    system_prompt: str,
    all_tools: List[Tool],
    task: str,
    max_steps: int,
    token_limit: int,
    workspace_dir: str,
    allowed_tools: Optional[List[str]],
    subagent_id: str,
) -> ToolResult:
    """Start subagent in background, return immediately."""
    workspace = Path(workspace_dir)
    output_dir = workspace / ".subagent"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{subagent_id}.md"

    # Augment prompt and tools for background mode
    if allowed_tools is not None:
        augmented_tools = list(allowed_tools)
        if "write_file" not in augmented_tools:
            augmented_tools.append("write_file")
    else:
        augmented_tools = allowed_tools

    augmented_prompt = (
        system_prompt
        + f"\n\n## Output\n"
        f"You are running as a background subagent. You MUST write your complete "
        f"final result to: .subagent/{subagent_id}.md using write_file before finishing.\n"
        f"This is the ONLY file you are allowed to write — all other read-only constraints still apply."
    )

    # Each background subagent gets its own cancel_event
    bg_cancel_event = asyncio.Event()

    coro = _background_wrapper(
        subagent_id=subagent_id,
        output_path=output_path,
        cancel_event=bg_cancel_event,
        llm_client=llm_client,
        system_prompt=augmented_prompt,
        tools=all_tools,
        task=task,
        max_steps=max_steps,
        token_limit=token_limit,
        workspace_dir=workspace_dir,
        allowed_tools=augmented_tools,
    )

    SubagentManager.start(subagent_id, coro, bg_cancel_event)

    return ToolResult(
        success=True,
        content=(
            f"Background subagent started.\n"
            f"  subagent_id: {subagent_id}\n"
            f"  output: .subagent/{subagent_id}.md\n"
            f"Use read_file to check the result. "
            f"File absent = still running. File present = done."
        ),
    )


# ---------------------------------------------------------------------------
# RunSubagentTool (gateway for fixed subagents)
# ---------------------------------------------------------------------------


class RunSubagentTool(Tool):
    """Gateway tool that dispatches to pre-defined subagents by name.

    Analogous to GetSkillTool: holds a loader reference, looks up config by name.
    """

    def __init__(self, loader: SubagentLoader):
        self._loader = loader
        self._llm_client = None
        self._all_tools: List[Tool] = []
        self._workspace_dir: str = "./workspace"
        self._parent_agent = None

    def set_context(
        self,
        llm_client,
        all_tools: List[Tool],
        workspace_dir: str = "./workspace",
        parent_agent=None,
    ):
        """Inject runtime dependencies (called after all tools are assembled)."""
        self._llm_client = llm_client
        self._all_tools = all_tools
        self._workspace_dir = workspace_dir
        self._parent_agent = parent_agent

    @property
    def name(self) -> str:
        return RUN_SUBAGENT_TOOL_NAME

    @property
    def description(self) -> str:
        return (
            "[Subagent] Run a pre-defined subagent by name. "
            "See system prompt for available subagents and their descriptions."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the subagent to run (see Available Subagents in system prompt).",
                },
                "task": {
                    "type": "string",
                    "description": "The task to delegate to this subagent. Be specific and provide all necessary context.",
                },
            },
            "required": ["name", "task"],
        }

    async def execute(self, name: str, task: str) -> ToolResult:
        if not self._llm_client:
            return ToolResult(success=False, error="Subagent not initialized (missing llm_client)")

        config = self._loader.loaded.get(name)
        if config is None:
            available = ", ".join(self._loader.loaded.keys()) or "(none)"
            return ToolResult(
                success=False,
                error=f"Subagent '{name}' not found. Available subagents: {available}",
            )

        # Resolve cancel_event from parent agent
        cancel_event = None
        if self._parent_agent is not None:
            cancel_event = getattr(self._parent_agent, "cancel_event", None)

        if config.run_in_background:
            subagent_id = f"{config.name}-{uuid.uuid4().hex[:8]}"
            return await _execute_background(
                llm_client=self._llm_client,
                system_prompt=config.system_prompt,
                all_tools=self._all_tools,
                task=task,
                max_steps=config.max_steps,
                token_limit=config.token_limit,
                workspace_dir=self._workspace_dir,
                allowed_tools=config.allowed_tools,
                subagent_id=subagent_id,
            )
        else:
            return await _execute_foreground(
                llm_client=self._llm_client,
                system_prompt=config.system_prompt,
                all_tools=self._all_tools,
                task=task,
                max_steps=config.max_steps,
                token_limit=config.token_limit,
                workspace_dir=self._workspace_dir,
                allowed_tools=config.allowed_tools,
                cancel_event=cancel_event,
                timeout=config.timeout,
                label=config.name,
            )


# ---------------------------------------------------------------------------
# CreateSubagentTool (dynamic)
# ---------------------------------------------------------------------------


class CreateSubagentTool(Tool):
    """A tool that lets the LLM create and run a subagent dynamically."""

    def __init__(
        self,
        llm_client=None,
        all_tools: Optional[List[Tool]] = None,
        default_max_steps: int = 30,
        default_token_limit: int = 80000,
        default_timeout: int = DEFAULT_TIMEOUT,
    ):
        self._llm_client = llm_client
        self._all_tools: List[Tool] = all_tools or []
        self._default_max_steps = default_max_steps
        self._default_token_limit = default_token_limit
        self._default_timeout = default_timeout
        self._workspace_dir: str = "./workspace"
        self._parent_agent = None

    def set_context(
        self,
        llm_client,
        all_tools: List[Tool],
        workspace_dir: str = "./workspace",
        parent_agent=None,
    ):
        """Inject runtime dependencies (called after all tools are assembled)."""
        self._llm_client = llm_client
        self._all_tools = all_tools
        self._workspace_dir = workspace_dir
        self._parent_agent = parent_agent

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
                "run_in_background": {
                    "type": "boolean",
                    "description": "If true, run the subagent in background and return immediately. Default: false.",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in seconds for foreground execution. Default: {self._default_timeout}.",
                },
            },
            "required": ["task", "system_prompt"],
        }

    async def execute(
        self,
        task: str,
        system_prompt: str,
        allowed_tools: Optional[List[str]] = None,
        run_in_background: bool = False,
        timeout: Optional[int] = None,
    ) -> ToolResult:
        if not self._llm_client:
            return ToolResult(success=False, error="Subagent not initialized (missing llm_client)")

        # Resolve cancel_event from parent agent
        cancel_event = None
        if self._parent_agent is not None:
            cancel_event = getattr(self._parent_agent, "cancel_event", None)

        effective_timeout = timeout if timeout is not None else self._default_timeout

        if run_in_background:
            subagent_id = f"dynamic-{uuid.uuid4().hex[:8]}"
            return await _execute_background(
                llm_client=self._llm_client,
                system_prompt=system_prompt,
                all_tools=self._all_tools,
                task=task,
                max_steps=self._default_max_steps,
                token_limit=self._default_token_limit,
                workspace_dir=self._workspace_dir,
                allowed_tools=allowed_tools,
                subagent_id=subagent_id,
            )
        else:
            return await _execute_foreground(
                llm_client=self._llm_client,
                system_prompt=system_prompt,
                all_tools=self._all_tools,
                task=task,
                max_steps=self._default_max_steps,
                token_limit=self._default_token_limit,
                workspace_dir=self._workspace_dir,
                allowed_tools=allowed_tools,
                cancel_event=cancel_event,
                timeout=effective_timeout,
                label="dynamic",
            )


# ---------------------------------------------------------------------------
# SubagentCancelTool
# ---------------------------------------------------------------------------


class SubagentCancelTool(Tool):
    """Tool to cancel a running background subagent."""

    @property
    def name(self) -> str:
        return SUBAGENT_CANCEL_TOOL_NAME

    @property
    def description(self) -> str:
        return (
            "[Subagent] Cancel a running background subagent by its ID. "
            "The subagent will stop at the next checkpoint."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subagent_id": {
                    "type": "string",
                    "description": "The subagent_id returned when the background subagent was started.",
                }
            },
            "required": ["subagent_id"],
        }

    async def execute(self, subagent_id: str) -> ToolResult:
        if SubagentManager.cancel(subagent_id):
            return ToolResult(
                success=True,
                content=f"Cancel signal sent to subagent '{subagent_id}'. It will stop at the next checkpoint.",
            )
        else:
            return ToolResult(
                success=False,
                error=f"Subagent '{subagent_id}' not found or already finished.",
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_subagent_tools(
    subagents_dir: str = "subagents",
) -> tuple[List[Tool], Optional[SubagentLoader]]:
    """Discover fixed subagents and create all subagent tools.

    Returns:
        Tuple of (list of subagent tools, loader instance).
        The tools need set_context() called before use.
    """
    loader = SubagentLoader(subagents_dir)
    loader.discover()

    tools: List[Tool] = []

    # Gateway tool for fixed subagents
    tools.append(RunSubagentTool(loader))

    # Dynamic subagent tool (always available)
    tools.append(CreateSubagentTool())

    # Cancel tool (always available)
    tools.append(SubagentCancelTool())

    return tools, loader
