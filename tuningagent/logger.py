"""Agent run logger — structured JSONL with session/turn/step hierarchy."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import Message, ToolCall


class AgentLogger:
    """Agent run logger

    Writes structured JSONL logs with session/turn/step hierarchy:
    - Session: one CLI interaction, grouped in a session subdirectory
    - Turn: one user input → full agent response (one agent.run() call)
    - Step: one iteration of the agent loop (one LLM call + N tool executions)

    Directory layout:
        ~/.mini-agent/log/<session_id>/
            agent.jsonl              # main agent
            code-explorer-a1b2.jsonl # subagent

    Each line is a self-contained JSON object with session_id and agent_id fields.
    """

    def __init__(
        self,
        session_id: str | None = None,
        agent_id: str = "agent",
    ):
        self.log_dir = Path.home() / ".mini-agent" / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id: str | None = session_id
        self.agent_id: str = agent_id
        self.log_file: Path | None = None
        self.turn: int = 0
        self.step: int = 0

    def start_turn(self):
        """Start a new turn (one agent.run() invocation).

        On the first call, lazily creates the session directory and JSONL file,
        then emits session_start.  Every call increments turn, resets step,
        and emits turn_start.
        """
        if self.log_file is None:
            if self.session_id is None:
                self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir = self.log_dir / self.session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = session_dir / f"{self.agent_id}.jsonl"
            self._write_event("session_start", {})

        self.turn += 1
        self.step = 0
        self._write_event("turn_start", {})

    def start_step(self, step: int):
        """Set the current step number (1-based). No event is written."""
        self.step = step

    def end_turn(self, result: str):
        """Emit turn_end event."""
        self._write_event("turn_end", {"result": result})

    def log_request(self, messages: list[Message], tools: list[Any] | None = None):
        """Log LLM request."""
        request_data: dict[str, Any] = {"messages": [], "tools": []}

        for msg in messages:
            msg_dict: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.thinking:
                msg_dict["thinking"] = msg.thinking
            if msg.tool_calls:
                msg_dict["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            if msg.tool_call_id:
                msg_dict["tool_call_id"] = msg.tool_call_id
            if msg.name:
                msg_dict["name"] = msg.name
            request_data["messages"].append(msg_dict)

        if tools:
            request_data["tools"] = [tool.name for tool in tools]

        self._write_event("llm_request", request_data)

    def log_response(
        self,
        content: str,
        thinking: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        finish_reason: str | None = None,
    ):
        """Log LLM response."""
        response_data: dict[str, Any] = {"content": content}

        if thinking:
            response_data["thinking"] = thinking
        if tool_calls:
            response_data["tool_calls"] = [tc.model_dump() for tc in tool_calls]
        if finish_reason:
            response_data["finish_reason"] = finish_reason

        self._write_event("llm_response", response_data)

    def log_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_success: bool,
        result_content: str | None = None,
        result_error: str | None = None,
    ):
        """Log tool execution result."""
        tool_result_data: dict[str, Any] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "success": result_success,
        }

        if result_success:
            tool_result_data["result"] = result_content
        else:
            tool_result_data["error"] = result_error

        self._write_event("tool_result", tool_result_data)

    def end_session(self):
        """Emit session_end event."""
        self._write_event("session_end", {})

    def log_rewind(self, from_turn: int, to_turn: int):
        """Emit a rewind event marking a timeline fork."""
        self._write_event("rewind", {"from_turn": from_turn, "to_turn": to_turn})

    def log_subagent_dispatched(self, subagent_id: str, mode: str, task: str):
        """Record that a subagent was dispatched from this agent."""
        self._write_event("subagent_dispatched", {
            "subagent_id": subagent_id,
            "mode": mode,
            "task": task[:200],
        })

    def _write_event(self, event: str, data: dict[str, Any]):
        """Write a single JSONL event line."""
        if self.log_file is None:
            return

        record = {
            "event": event,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "turn": self.turn,
            "step": self.step,
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "data": data,
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_log_file_path(self) -> Path | None:
        """Get current log file path."""
        return self.log_file
