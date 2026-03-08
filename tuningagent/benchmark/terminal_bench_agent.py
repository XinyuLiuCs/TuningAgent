"""Terminal-Bench agent adapter for TuningAgent."""

from __future__ import annotations

import asyncio
import re
import shlex
import tempfile
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from terminal_bench.agents.base_agent import AgentResult, BaseAgent
from terminal_bench.agents.failure_mode import FailureMode
from terminal_bench.terminal.tmux_session import TmuxSession

from tuningagent.agent import Agent
from tuningagent.config import Config
from tuningagent.llm.model_pool import ModelPool
from tuningagent.logger import AgentLogger
from tuningagent.tools.base import Tool, ToolResult
from tuningagent.tools.file_tools import truncate_text_by_tokens

_CMD_START_MARKER = "__TUNINGAGENT_CMD_START__"
_EXIT_CODE_PATTERN = re.compile(r"__TUNINGAGENT_EXIT_CODE__=(\d+)")


@dataclass
class RemoteBackgroundProcess:
    """Background process metadata stored on the host side."""

    bash_id: str
    pid: int
    log_path: str
    command: str
    last_line_count: int = 0


class RemoteBackgroundRegistry:
    """Session-scoped registry for task-container background processes."""

    def __init__(self):
        self._processes: dict[str, RemoteBackgroundProcess] = {}

    def add(self, process: RemoteBackgroundProcess) -> None:
        self._processes[process.bash_id] = process

    def get(self, bash_id: str) -> RemoteBackgroundProcess | None:
        return self._processes.get(bash_id)

    def remove(self, bash_id: str) -> None:
        self._processes.pop(bash_id, None)


class TerminalBenchAgentLogger(AgentLogger):
    """Agent logger that writes into the Terminal-Bench run directory."""

    def __init__(self, log_dir: Path, session_id: str, agent_id: str = "tuningagent"):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.agent_id = agent_id
        self.log_file: Path | None = None
        self.turn = 0
        self.step = 0

    def start_turn(self):
        if self.log_file is None:
            self.log_file = self.log_dir / f"{self.agent_id}.jsonl"
            self._write_event("session_start", {})

        self.turn += 1
        self.step = 0
        self._write_event("turn_start", {})

    def log_error(self, message: str, traceback_text: str | None = None):
        data: dict[str, Any] = {"message": message}
        if traceback_text:
            data["traceback"] = traceback_text
        self._write_event("agent_error", data)


class SessionBashTool(Tool):
    """Bash tool backed by the Terminal-Bench tmux session."""

    def __init__(
        self,
        session: TmuxSession,
        workspace_dir: str,
        background_registry: RemoteBackgroundRegistry,
    ):
        self.session = session
        self.workspace_dir = workspace_dir
        self.background_registry = background_registry
        self._primed = False

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute bash commands inside the Terminal-Bench task container. "
            "Use this for shell operations, running programs, inspecting the environment, "
            "and fixing files from the command line."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command to execute in the task container",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds for foreground commands",
                    "default": 120,
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run the command in the background and monitor it with bash_output",
                    "default": False,
                },
            },
            "required": ["command"],
        }

    def _prime_session_output(self) -> None:
        if not self._primed:
            self.session.get_incremental_output()
            self._primed = True

    def _extract_command_output(self, full_buffer: str) -> tuple[str, int | None]:
        marker_index = full_buffer.rfind(_CMD_START_MARKER)
        if marker_index == -1:
            return full_buffer.strip(), None

        relevant = full_buffer[marker_index + len(_CMD_START_MARKER) :]
        match = _EXIT_CODE_PATTERN.search(relevant)
        exit_code = int(match.group(1)) if match else None
        if match:
            relevant = relevant[: match.start()]

        cleaned = relevant.strip("\n\r ")
        return cleaned, exit_code

    def _container_exec(self, script: str) -> tuple[int, str]:
        result = self.session.container.exec_run(["bash", "-lc", script])
        output = result.output.decode("utf-8", errors="replace")
        return result.exit_code, output

    async def execute(
        self,
        command: str,
        timeout: int = 120,
        run_in_background: bool = False,
    ) -> ToolResult:
        timeout = max(1, min(timeout, 600))

        if run_in_background:
            bash_id = str(uuid.uuid4())[:8]
            log_path = f"/tmp/tuningagent-bg-{bash_id}.log"
            pid_path = f"/tmp/tuningagent-bg-{bash_id}.pid"
            script = (
                f"cd {shlex.quote(self.workspace_dir)} && "
                f"rm -f {shlex.quote(log_path)} {shlex.quote(pid_path)} && "
                f"nohup bash -lc {shlex.quote(command)} > {shlex.quote(log_path)} 2>&1 & "
                f"echo $! | tee {shlex.quote(pid_path)}"
            )
            exit_code, output = self._container_exec(script)
            if exit_code != 0:
                return ToolResult(
                    success=False,
                    error=f"Failed to start background command:\n{output.strip()}",
                )

            try:
                pid = int(output.strip().splitlines()[-1])
            except (IndexError, ValueError):
                return ToolResult(
                    success=False,
                    error=f"Background command started but PID could not be parsed:\n{output.strip()}",
                )

            self.background_registry.add(
                RemoteBackgroundProcess(
                    bash_id=bash_id,
                    pid=pid,
                    log_path=log_path,
                    command=command,
                )
            )
            return ToolResult(
                success=True,
                content=(
                    f"Background command started.\n"
                    f"[bash_id]: {bash_id}\n"
                    f"[pid]: {pid}\n"
                    f"Use bash_output to inspect logs and bash_kill to stop it."
                ),
            )

        self._prime_session_output()
        wrapped_command = (
            f"printf '{_CMD_START_MARKER}\\n'; "
            f"cd {shlex.quote(self.workspace_dir)} && "
            f"({command}); "
            f"__ta_status=$?; "
            f"printf '__TUNINGAGENT_EXIT_CODE__=%s\\n' \"$__ta_status\""
        )
        try:
            self.session.send_keys(
                [wrapped_command, "Enter"],
                block=True,
                max_timeout_sec=float(timeout),
            )
        except TimeoutError:
            return ToolResult(
                success=False,
                error=f"Command timed out after {timeout} seconds",
            )

        output, exit_code = self._extract_command_output(
            self.session.capture_pane(capture_entire=True)
        )

        if exit_code in (None, 0):
            return ToolResult(success=True, content=output.strip() or "(no output)")

        return ToolResult(
            success=False,
            content=output.strip(),
            error=f"Command failed with exit code {exit_code}\n{output.strip()}".strip(),
        )


class SessionBashOutputTool(Tool):
    """Read output from a background bash command."""

    @property
    def name(self) -> str:
        return "bash_output"

    @property
    def description(self) -> str:
        return "Read incremental output from a background bash command started with bash(run_in_background=true)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bash_id": {"type": "string", "description": "Background command ID"},
                "filter_str": {
                    "type": "string",
                    "description": "Optional regex filter applied to newly produced log lines",
                },
            },
            "required": ["bash_id"],
        }

    def __init__(self, session: TmuxSession, background_registry: RemoteBackgroundRegistry):
        self.session = session
        self.background_registry = background_registry

    async def execute(self, bash_id: str, filter_str: str | None = None) -> ToolResult:
        process = self.background_registry.get(bash_id)
        if process is None:
            return ToolResult(success=False, error=f"Background process not found: {bash_id}")

        script = (
            f"if [ -f {shlex.quote(process.log_path)} ]; then "
            f"cat {shlex.quote(process.log_path)}; "
            f"fi"
        )
        result = self.session.container.exec_run(["bash", "-lc", script])
        content = result.output.decode("utf-8", errors="replace")
        lines = content.splitlines()
        new_lines = lines[process.last_line_count :]
        process.last_line_count = len(lines)

        if filter_str:
            try:
                pattern = re.compile(filter_str)
                new_lines = [line for line in new_lines if pattern.search(line)]
            except re.error as exc:
                return ToolResult(success=False, error=f"Invalid regex filter: {exc}")

        output = "\n".join(new_lines).strip() or "(no new output)"
        return ToolResult(success=True, content=output)


class SessionBashKillTool(Tool):
    """Terminate a background bash command."""

    def __init__(self, session: TmuxSession, background_registry: RemoteBackgroundRegistry):
        self.session = session
        self.background_registry = background_registry

    @property
    def name(self) -> str:
        return "bash_kill"

    @property
    def description(self) -> str:
        return "Terminate a background bash command started with bash(run_in_background=true)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bash_id": {"type": "string", "description": "Background command ID"},
            },
            "required": ["bash_id"],
        }

    async def execute(self, bash_id: str) -> ToolResult:
        process = self.background_registry.get(bash_id)
        if process is None:
            return ToolResult(success=False, error=f"Background process not found: {bash_id}")

        script = f"kill {process.pid}"
        result = self.session.container.exec_run(["bash", "-lc", script])
        self.background_registry.remove(bash_id)

        if result.exit_code == 0:
            return ToolResult(
                success=True,
                content=f"Terminated background command {bash_id} (pid={process.pid})",
            )
        return ToolResult(
            success=False,
            error=(
                f"Failed to terminate background command {bash_id} (pid={process.pid}): "
                f"{result.output.decode('utf-8', errors='replace').strip()}"
            ),
        )


class SessionReadTool(Tool):
    """Read files from the task container."""

    _MAX_DEFAULT_LINES = 250

    def __init__(self, session: TmuxSession, workspace_dir: str):
        self.session = session
        self.workspace_dir = workspace_dir

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return (
            "Read file contents from the task container. Output includes line numbers "
            "formatted as 'LINE|CONTENT'."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or workspace-relative file path"},
                "offset": {"type": "integer", "description": "Starting line number (1-indexed)"},
                "limit": {"type": "integer", "description": "Number of lines to read"},
            },
            "required": ["path"],
        }

    def _resolve_remote_path(self, path: str) -> str:
        remote_path = Path(path)
        if remote_path.is_absolute():
            return str(remote_path)
        return str(Path(self.workspace_dir) / remote_path)

    async def execute(self, path: str, offset: int | None = None, limit: int | None = None) -> ToolResult:
        remote_path = self._resolve_remote_path(path)
        start = max(1, offset or 1)
        line_count_result = self.session.container.exec_run(
            ["bash", "-lc", f"if [ -f {shlex.quote(remote_path)} ]; then wc -l < {shlex.quote(remote_path)}; else exit 2; fi"]
        )
        if line_count_result.exit_code != 0:
            return ToolResult(success=False, error=f"Failed to read {path}: file not found")

        total_lines_raw = line_count_result.output.decode("utf-8", errors="replace").strip()
        try:
            total_lines = int(total_lines_raw or "0")
        except ValueError:
            total_lines = 0

        requested_limit = max(1, limit) if limit is not None else None
        effective_limit = requested_limit
        truncated = False
        if requested_limit is None and total_lines > self._MAX_DEFAULT_LINES:
            effective_limit = self._MAX_DEFAULT_LINES
            truncated = True

        if effective_limit is None:
            range_expr = f"{start},$"
        else:
            end = start + effective_limit - 1
            range_expr = f"{start},{end}"
        script = (
            f"if [ ! -f {shlex.quote(remote_path)} ]; then "
            f"echo '__TUNINGAGENT_READ_ERROR__ file not found'; exit 2; fi; "
            f"nl -ba {shlex.quote(remote_path)} | sed -n {shlex.quote(range_expr + 'p')}"
        )
        result = self.session.container.exec_run(["bash", "-lc", script])
        output = result.output.decode("utf-8", errors="replace").rstrip()
        if result.exit_code != 0:
            return ToolResult(success=False, error=f"Failed to read {path}: {output}")
        raw_content = output or "(empty file)"
        normalized_lines = []
        for line in raw_content.splitlines():
            normalized_lines.append(line.replace("\t", "|", 1))
        normalized = "\n".join(normalized_lines)
        truncated_by_tokens = truncate_text_by_tokens(normalized, 12000)
        if truncated_by_tokens != normalized:
            normalized = truncated_by_tokens
            truncated = True
        else:
            normalized = truncated_by_tokens

        if truncated:
            visible_end = min(total_lines, start + (effective_limit or total_lines) - 1)
            normalized = (
                f"[truncated file view for {path}: showing lines {start}-{visible_end} of {total_lines}. "
                "Use file_read with offset/limit for another slice or use bash for full-file processing.]\n"
                f"{normalized}"
            )
        return ToolResult(success=True, content=normalized)


class SessionWriteTool(Tool):
    """Write files into the task container."""

    def __init__(self, session: TmuxSession, workspace_dir: str):
        self.session = session
        self.workspace_dir = workspace_dir

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "Write content to a file in the task container, overwriting existing content."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or workspace-relative file path"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        }

    def _resolve_remote_path(self, path: str) -> Path:
        remote_path = Path(path)
        if remote_path.is_absolute():
            return remote_path
        return Path(self.workspace_dir) / remote_path

    async def execute(self, path: str, content: str) -> ToolResult:
        remote_path = self._resolve_remote_path(path)
        self.session.container.exec_run(
            ["bash", "-lc", f"mkdir -p {shlex.quote(str(remote_path.parent))}"]
        )

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(content)
            temp_path = Path(handle.name)

        try:
            self.session.copy_to_container(
                temp_path,
                container_dir=str(remote_path.parent),
                container_filename=remote_path.name,
            )
        finally:
            temp_path.unlink(missing_ok=True)

        return ToolResult(success=True, content=f"Successfully wrote {remote_path}")


class SessionEditTool(Tool):
    """Perform exact string replacement inside a task-container file."""

    def __init__(self, read_tool: SessionReadTool, write_tool: SessionWriteTool):
        self.read_tool = read_tool
        self.write_tool = write_tool

    @property
    def name(self) -> str:
        return "file_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by exact string replacement. The old string must exist exactly once."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or workspace-relative file path"},
                "old_str": {"type": "string", "description": "Exact old text"},
                "new_str": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_str", "new_str"],
        }

    async def execute(self, path: str, old_str: str, new_str: str) -> ToolResult:
        remote_path = self.write_tool._resolve_remote_path(path)
        result = self.read_tool.session.container.exec_run(
            ["bash", "-lc", f"cat {shlex.quote(str(remote_path))}"]
        )
        if result.exit_code != 0:
            return ToolResult(success=False, error=f"File not found: {path}")
        content = result.output.decode("utf-8", errors="replace")
        occurrences = content.count(old_str)
        if occurrences == 0:
            return ToolResult(success=False, error="Text not found in file")
        if occurrences > 1:
            return ToolResult(success=False, error="Text occurs multiple times; edit is ambiguous")

        new_content = content.replace(old_str, new_str)
        return await self.write_tool.execute(path, new_content)


def _load_benchmark_system_prompt(remote_workspace: str, system_prompt_path: str | None = None) -> str:
    prompt_path = Path(system_prompt_path) if system_prompt_path else Config.find_config_file("system_prompt.md")
    if prompt_path and prompt_path.exists():
        prompt = prompt_path.read_text(encoding="utf-8")
    else:
        prompt = "You are TuningAgent, an AI assistant that solves tasks using bash and file tools."

    prompt = prompt.replace("{AGENT_MEMORY}", "")
    prompt = prompt.replace("{SKILLS_METADATA}", "")
    prompt = prompt.replace("{SUBAGENTS_METADATA}", "")

    benchmark_context = f"""

## Terminal-Bench Context
You are solving a Terminal-Bench task in a remote task container.
- Use the provided tools to operate on the remote container, not the host machine.
- The remote workspace root is `{remote_workspace}`.
- Prefer `file_read`, `file_write`, and `file_edit` for file manipulation.
- Use `bash` for terminal operations, running programs, tests, package commands, and debugging.
- For large logs, datasets, or generated files, prefer `bash` pipelines over reading the full file.
- Only use `file_read` on large files with `offset`/`limit` or when you specifically need exact file content.
- Treat task instructions as exact requirements. Hidden checks often validate exact formatting, permissions, and filenames.
- If a file read says it is truncated, do not assume you saw the full file. Switch to `bash` or read targeted slices.
- When fixing scripts or executables, set complete permissions explicitly when needed (for example `chmod 755 file`) instead of only adding `+x`.
- When aggregating logs or tabular data, prefer simple shell pipelines that directly compute the required numbers over ad-hoc parsing scripts.
- Before finishing, verify the actual file contents or command output that the grader will inspect.

## Current Workspace
You are currently working in: `{remote_workspace}`
All relative paths refer to this remote workspace inside the task container.
"""
    return prompt + benchmark_context


class TuningAgentTerminalBenchAgent(BaseAgent):
    """Terminal-Bench custom agent that runs the local TuningAgent stack."""

    def __init__(
        self,
        config_path: str | None = None,
        system_prompt_path: str | None = None,
        max_steps: int = 50,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._config_path = config_path
        self._system_prompt_path = system_prompt_path
        self._max_steps = max_steps

    @staticmethod
    def name() -> str:
        return "tuningagent"

    def _load_config(self) -> Config:
        if self._config_path:
            return Config.from_yaml(self._config_path)
        return Config.load()

    def _detect_remote_workspace(self, session: TmuxSession) -> str:
        result = session.container.exec_run(["bash", "-lc", "pwd"])
        workspace = result.output.decode("utf-8", errors="replace").strip()
        return workspace or "/app"

    def _build_tools(self, session: TmuxSession, workspace_dir: str) -> list[Tool]:
        background_registry = RemoteBackgroundRegistry()
        read_tool = SessionReadTool(session, workspace_dir)
        write_tool = SessionWriteTool(session, workspace_dir)
        return [
            SessionBashTool(session, workspace_dir, background_registry),
            SessionBashOutputTool(session, background_registry),
            SessionBashKillTool(session, background_registry),
            read_tool,
            write_tool,
            SessionEditTool(read_tool, write_tool),
        ]

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
    ) -> AgentResult:
        logger: TerminalBenchAgentLogger | None = None
        try:
            config = self._load_config()
            remote_workspace = self._detect_remote_workspace(session)
            tools = self._build_tools(session, remote_workspace)
            prompt = _load_benchmark_system_prompt(
                remote_workspace=remote_workspace,
                system_prompt_path=self._system_prompt_path,
            )

            local_workspace = logging_dir or Path(tempfile.mkdtemp(prefix="tuningagent-tb-"))
            logger = TerminalBenchAgentLogger(
                log_dir=Path(logging_dir) if logging_dir else local_workspace,
                session_id=f"tb-{uuid.uuid4().hex[:8]}",
            )

            model_pool = ModelPool()
            retry_config = config.llm.retry if config.llm.retry.enabled else None
            if config.models:
                for alias, model_cfg in config.models.items():
                    model_pool.add_model(alias, model_cfg, retry_config=retry_config)
                model_pool.set_active(config.default_model)
            else:
                raise RuntimeError("No models configured in config.yaml")

            agent = Agent(
                llm_client=model_pool,
                system_prompt=prompt,
                tools=tools,
                max_steps=self._max_steps,
                token_limit=config.agent.token_limit,
                workspace_dir=str(local_workspace),
                logger=logger,
            )
            agent.add_user_message(self._render_instruction(instruction))

            result_text = asyncio.run(agent.run())

            stats = model_pool.get_stats()
            total_input_tokens = sum(stat.prompt_tokens for stat in stats.values())
            total_output_tokens = sum(stat.completion_tokens for stat in stats.values())

            failure_mode = FailureMode.NONE
            if isinstance(result_text, str) and result_text.startswith("LLM call failed"):
                failure_mode = FailureMode.UNKNOWN_AGENT_ERROR

            return AgentResult(
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                failure_mode=failure_mode,
            )
        except Exception as exc:
            if logger is not None:
                logger.log_error(str(exc), traceback.format_exc())
            return AgentResult(
                total_input_tokens=0,
                total_output_tokens=0,
                failure_mode=FailureMode.UNKNOWN_AGENT_ERROR,
            )
        finally:
            if logger is not None:
                try:
                    logger.end_session()
                except Exception:
                    pass
