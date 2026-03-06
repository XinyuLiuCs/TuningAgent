"""Core Agent implementation."""

import asyncio
import json
import re
from pathlib import Path
from time import perf_counter
from typing import Optional

import tiktoken

from .llm import LLMClient
from .logger import AgentLogger
from .schema import Message
from .tools.base import Tool, ToolResult
from .tools.mode_tool import MODE_PROMPTS, VALID_MODES, WRITE_TOOLS
from .utils import calculate_display_width


# ANSI color codes
class Colors:
    """Terminal color definitions"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Foreground colors
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


PLAN_SUMMARY_PROMPT = """\
Summarize the following plan-mode exploration into a structured plan.
Preserve: goal, concrete steps, file paths discovered, key decisions, risks.
Discard: raw file contents, intermediate reasoning, tool call details.
Format as a concise action plan (under 500 words)."""


class Agent:
    """Single agent with basic tools and MCP support."""

    def __init__(
        self,
        llm_client: LLMClient,
        system_prompt: str,
        tools: list[Tool],
        max_steps: int = 50,
        workspace_dir: str = "./workspace",
        token_limit: int = 80000,  # Summary triggered when tokens exceed this value
        logger: Optional[AgentLogger] = None,
    ):
        self.llm = llm_client
        self._all_tools: dict[str, Tool] = {tool.name: tool for tool in tools}
        self.tools: dict[str, Tool] = dict(self._all_tools)
        self.mode: str = "build"
        self._plan_start_idx: int | None = None
        self.max_steps = max_steps
        self.token_limit = token_limit
        self.workspace_dir = Path(workspace_dir)
        # Cancellation event for interrupting agent execution (set externally, e.g., by Esc key)
        self.cancel_event: Optional[asyncio.Event] = None

        # Ensure workspace exists
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Inject workspace information into system prompt if not already present
        if "Current Workspace" not in system_prompt:
            workspace_info = f"\n\n## Current Workspace\nYou are currently working in: `{self.workspace_dir.absolute()}`\nAll relative paths will be resolved relative to this directory."
            system_prompt = system_prompt + workspace_info

        self.system_prompt = system_prompt

        # Initialize message history
        self.messages: list[Message] = [Message(role="system", content=system_prompt)]

        # Initialize logger (use provided logger or create a new one)
        self.logger = logger if logger is not None else AgentLogger()

        # Inject initial mode prompt into system message
        self._apply_mode_prompt()

        # Token usage from last API response (updated after each LLM call)
        self.api_total_tokens: int = 0
        # Flag to skip token check right after summary (avoid consecutive triggers)
        self._skip_next_token_check: bool = False
        # Deferred plan summary flag (set by ModeSwitchTool, executed after tool results appended)
        self._pending_plan_summary: bool = False

    def switch_mode(self, new_mode: str) -> dict:
        """Switch operating mode and update tool availability + system prompt.

        Returns metadata dict with mode info for the caller to display.
        """
        if new_mode not in VALID_MODES:
            return {"error": f"Invalid mode '{new_mode}'. Must be one of: {', '.join(VALID_MODES)}"}

        old_mode = self.mode
        self.mode = new_mode
        if new_mode == "plan":
            self._plan_start_idx = len(self.messages)
        removed = self._apply_mode_filter()
        self._apply_mode_prompt()

        return {
            "old_mode": old_mode,
            "new_mode": new_mode,
            "tool_count": len(self.tools),
            "removed": removed,
        }

    def _apply_mode_filter(self) -> list[str]:
        """Rebuild self.tools from _all_tools based on current mode.

        Returns list of tool names that were removed (empty for build mode).
        """
        if self.mode == "build":
            self.tools = dict(self._all_tools)
            return []

        # Ask/Plan: remove write tools
        removed = []
        self.tools = {}
        for name, tool in self._all_tools.items():
            if name in WRITE_TOOLS:
                removed.append(name)
            else:
                self.tools[name] = tool
        return removed

    def _apply_mode_prompt(self):
        """Inject or replace the ## Current Mode section in the system prompt."""
        new_section = MODE_PROMPTS[self.mode]
        sys_content = self.messages[0].content

        # Try to replace existing section
        pattern = r"## Current Mode\n.*?(?=\n## |\Z)"
        replaced, count = re.subn(pattern, new_section, sys_content, count=1, flags=re.DOTALL)

        if count > 0:
            sys_content = replaced
        else:
            # Insert before ## Workspace Context (or ## Current Workspace), or append
            insert_pattern = r"(\n## (?:Workspace Context|Current Workspace))"
            match = re.search(insert_pattern, sys_content)
            if match:
                sys_content = sys_content[: match.start()] + "\n\n" + new_section + sys_content[match.start() :]
            else:
                sys_content = sys_content + "\n\n" + new_section

        self.messages[0] = Message(role="system", content=sys_content)
        self.system_prompt = sys_content

    def add_user_message(self, content: str):
        """Add a user message to history."""
        self.messages.append(Message(role="user", content=content))

    def _check_cancelled(self) -> bool:
        """Check if agent execution has been cancelled.

        Returns:
            True if cancelled, False otherwise.
        """
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        return False

    def _cleanup_incomplete_messages(self):
        """Remove the incomplete assistant message and its partial tool results.

        This ensures message consistency after cancellation by removing
        only the current step's incomplete messages, preserving completed steps.
        """
        # Find the index of the last assistant message
        last_assistant_idx = -1
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].role == "assistant":
                last_assistant_idx = i
                break

        if last_assistant_idx == -1:
            # No assistant message found, nothing to clean
            return

        # Remove the last assistant message and all tool results after it
        removed_count = len(self.messages) - last_assistant_idx
        if removed_count > 0:
            self.messages = self.messages[:last_assistant_idx]
            print(f"{Colors.DIM}   Cleaned up {removed_count} incomplete message(s){Colors.RESET}")

    def _estimate_tokens(self) -> int:
        """Accurately calculate token count for message history using tiktoken

        Uses cl100k_base encoder (GPT-4/Claude/M2 compatible)
        """
        try:
            # Use cl100k_base encoder (used by GPT-4 and most modern models)
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback: if tiktoken initialization fails, use simple estimation
            return self._estimate_tokens_fallback()

        total_tokens = 0

        for msg in self.messages:
            # Count text content
            if isinstance(msg.content, str):
                total_tokens += len(encoding.encode(msg.content))
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        # Convert dict to string for calculation
                        total_tokens += len(encoding.encode(str(block)))

            # Count thinking
            if msg.thinking:
                total_tokens += len(encoding.encode(msg.thinking))

            # Count tool_calls
            if msg.tool_calls:
                total_tokens += len(encoding.encode(str(msg.tool_calls)))

            # Metadata overhead per message (approximately 4 tokens)
            total_tokens += 4

        return total_tokens

    def _estimate_tokens_fallback(self) -> int:
        """Fallback token estimation method (when tiktoken is unavailable)"""
        total_chars = 0
        for msg in self.messages:
            if isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        total_chars += len(str(block))

            if msg.thinking:
                total_chars += len(msg.thinking)

            if msg.tool_calls:
                total_chars += len(str(msg.tool_calls))

        # Rough estimation: average 2.5 characters = 1 token
        return int(total_chars / 2.5)

    async def _summarize_messages(self):
        """Message history summarization: summarize conversations between user messages when tokens exceed limit

        Strategy (Agent mode):
        - Keep all user messages (these are user intents)
        - Summarize content between each user-user pair (agent execution process)
        - If last round is still executing (has agent/tool messages but no next user), also summarize
        - Structure: system -> user1 -> summary1 -> user2 -> summary2 -> user3 -> summary3 (if executing)

        Summary is triggered when EITHER:
        - Local token estimation exceeds limit
        - API reported total_tokens exceeds limit
        """
        # Skip check if we just completed a summary (wait for next LLM call to update api_total_tokens)
        if self._skip_next_token_check:
            self._skip_next_token_check = False
            return

        estimated_tokens = self._estimate_tokens()

        # Check both local estimation and API reported tokens
        should_summarize = estimated_tokens > self.token_limit or self.api_total_tokens > self.token_limit

        # If neither exceeded, no summary needed
        if not should_summarize:
            return

        print(
            f"\n{Colors.BRIGHT_YELLOW}📊 Token usage - Local estimate: {estimated_tokens}, API reported: {self.api_total_tokens}, Limit: {self.token_limit}{Colors.RESET}"
        )
        print(f"{Colors.BRIGHT_YELLOW}🔄 Triggering message history summarization...{Colors.RESET}")

        # Find all user message indices (skip system prompt)
        user_indices = [i for i, msg in enumerate(self.messages) if msg.role == "user" and i > 0]

        # Need at least 1 user message to perform summary
        if len(user_indices) < 1:
            print(f"{Colors.BRIGHT_YELLOW}⚠️  Insufficient messages, cannot summarize{Colors.RESET}")
            return

        # Build new message list
        new_messages = [self.messages[0]]  # Keep system prompt
        summary_count = 0

        # Iterate through each user message and summarize the execution process after it
        for i, user_idx in enumerate(user_indices):
            # Add current user message
            new_messages.append(self.messages[user_idx])

            # Determine message range to summarize
            # If last user, go to end of message list; otherwise to before next user
            if i < len(user_indices) - 1:
                next_user_idx = user_indices[i + 1]
            else:
                next_user_idx = len(self.messages)

            # Extract execution messages for this round
            execution_messages = self.messages[user_idx + 1 : next_user_idx]

            # If there are execution messages in this round, summarize them
            if execution_messages:
                summary_text = await self._create_summary(execution_messages, i + 1)
                if summary_text:
                    summary_message = Message(
                        role="user",
                        content=f"[Assistant Execution Summary]\n\n{summary_text}",
                    )
                    new_messages.append(summary_message)
                    summary_count += 1

        # Replace message list
        self.messages = new_messages

        # Skip next token check to avoid consecutive summary triggers
        # (api_total_tokens will be updated after next LLM call)
        self._skip_next_token_check = True

        new_tokens = self._estimate_tokens()
        print(f"{Colors.BRIGHT_GREEN}✓ Summary completed, local tokens: {estimated_tokens} → {new_tokens}{Colors.RESET}")
        print(f"{Colors.DIM}  Structure: system + {len(user_indices)} user messages + {summary_count} summaries{Colors.RESET}")
        print(f"{Colors.DIM}  Note: API token count will update on next LLM call{Colors.RESET}")

    async def _create_summary(self, messages: list[Message], round_num: int, *, summary_prompt: str | None = None) -> str:
        """Create summary for one execution round

        Args:
            messages: List of messages to summarize
            round_num: Round number
            summary_prompt: Optional custom prompt for the LLM summarization call.
                            When None, the default agent-execution summary prompt is used.

        Returns:
            Summary text
        """
        if not messages:
            return ""

        # Build summary content
        summary_content = f"Round {round_num} execution process:\n\n"
        for msg in messages:
            if msg.role == "assistant":
                content_text = msg.content if isinstance(msg.content, str) else str(msg.content)
                summary_content += f"Assistant: {content_text}\n"
                if msg.tool_calls:
                    tool_names = [tc.function.name for tc in msg.tool_calls]
                    summary_content += f"  → Called tools: {', '.join(tool_names)}\n"
            elif msg.role == "tool":
                result_preview = msg.content if isinstance(msg.content, str) else str(msg.content)
                summary_content += f"  ← Tool returned: {result_preview}...\n"

        # Call LLM to generate concise summary
        try:
            if summary_prompt is None:
                summary_prompt = f"""Please provide a concise summary of the following Agent execution process:

{summary_content}

Requirements:
1. Focus on what tasks were completed and which tools were called
2. Keep key execution results and important findings
3. Be concise and clear, within 1000 words
4. Use English
5. Do not include "user" related content, only summarize the Agent's execution process"""
            else:
                summary_prompt = f"{summary_prompt}\n\n{summary_content}"

            summary_msg = Message(role="user", content=summary_prompt)
            response = await self.llm.generate(
                messages=[
                    Message(
                        role="system",
                        content="You are an assistant skilled at summarizing Agent execution processes.",
                    ),
                    summary_msg,
                ]
            )

            summary_text = response.content
            print(f"{Colors.BRIGHT_GREEN}✓ Summary for round {round_num} generated successfully{Colors.RESET}")
            return summary_text

        except Exception as e:
            print(f"{Colors.BRIGHT_RED}✗ Summary generation failed for round {round_num}: {e}{Colors.RESET}")
            # Use simple text summary on failure
            return summary_content

    async def _summarize_plan_context(self):
        """Compress plan-mode exploration into a concise action plan.

        Called when switching from plan → build. Replaces all messages from
        ``_plan_start_idx`` onward with a single ``[Plan Summary]`` user message.
        """
        if self._plan_start_idx is None or self._plan_start_idx >= len(self.messages):
            return

        plan_messages = self.messages[self._plan_start_idx:]

        # Skip if too few messages to warrant summarization
        if len(plan_messages) < 3:
            self._plan_start_idx = None
            return

        print(f"\n{Colors.BRIGHT_YELLOW}🔄 Summarizing plan context...{Colors.RESET}")

        summary_text = await self._create_summary(
            plan_messages, round_num=0, summary_prompt=PLAN_SUMMARY_PROMPT
        )

        if summary_text:
            summary_message = Message(
                role="user",
                content=f"[Plan Summary]\n\n{summary_text}",
            )
            self.messages = self.messages[: self._plan_start_idx] + [summary_message]
            print(f"{Colors.BRIGHT_GREEN}✓ Plan context compressed into summary{Colors.RESET}")

        # Reset bookkeeping
        self.api_total_tokens = 0
        self._skip_next_token_check = False
        self._plan_start_idx = None

    async def run(self, cancel_event: Optional[asyncio.Event] = None) -> str:
        """Execute agent loop until task is complete or max steps reached.

        Args:
            cancel_event: Optional asyncio.Event that can be set to cancel execution.
                          When set, the agent will stop at the next safe checkpoint
                          (after completing the current step to keep messages consistent).

        Returns:
            The final response content, or error message (including cancellation message).
        """
        # Set cancellation event (can also be set via self.cancel_event before calling run())
        if cancel_event is not None:
            self.cancel_event = cancel_event

        # Start new turn (lazily creates log file on first call)
        self.logger.start_turn()
        print(f"{Colors.DIM}📝 Log file: {self.logger.get_log_file_path()}{Colors.RESET}")

        step = 0
        run_start_time = perf_counter()

        while step < self.max_steps:
            # Check for cancellation at start of each step
            if self._check_cancelled():
                self._cleanup_incomplete_messages()
                cancel_msg = "Task cancelled by user."
                print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {cancel_msg}{Colors.RESET}")
                self.logger.end_turn(cancel_msg)
                return cancel_msg

            step_start_time = perf_counter()
            # Check and summarize message history to prevent context overflow
            await self._summarize_messages()

            # Step header with proper width calculation
            BOX_WIDTH = 58
            step_text = f"{Colors.BOLD}{Colors.BRIGHT_CYAN}💭 Step {step + 1}/{self.max_steps}{Colors.RESET}"
            step_display_width = calculate_display_width(step_text)
            padding = max(0, BOX_WIDTH - 1 - step_display_width)  # -1 for leading space

            print(f"\n{Colors.DIM}╭{'─' * BOX_WIDTH}╮{Colors.RESET}")
            print(f"{Colors.DIM}│{Colors.RESET} {step_text}{' ' * padding}{Colors.DIM}│{Colors.RESET}")
            print(f"{Colors.DIM}╰{'─' * BOX_WIDTH}╯{Colors.RESET}")

            # Track step in logger (1-based)
            self.logger.start_step(step + 1)

            # Get tool list for LLM call
            tool_list = list(self.tools.values())

            # Log LLM request and call LLM with Tool objects directly
            self.logger.log_request(messages=self.messages, tools=tool_list)

            try:
                response = await self.llm.generate(messages=self.messages, tools=tool_list)
            except Exception as e:
                # Check if it's a retry exhausted error
                from .retry import RetryExhaustedError

                if isinstance(e, RetryExhaustedError):
                    error_msg = f"LLM call failed after {e.attempts} retries\nLast error: {str(e.last_exception)}"
                    print(f"\n{Colors.BRIGHT_RED}❌ Retry failed:{Colors.RESET} {error_msg}")
                else:
                    error_msg = f"LLM call failed: {str(e)}"
                    print(f"\n{Colors.BRIGHT_RED}❌ Error:{Colors.RESET} {error_msg}")
                self.logger.end_turn(error_msg)
                return error_msg

            # Accumulate API reported token usage
            if response.usage:
                self.api_total_tokens = response.usage.total_tokens

            # Log LLM response
            self.logger.log_response(
                content=response.content,
                thinking=response.thinking,
                tool_calls=response.tool_calls,
                finish_reason=response.finish_reason,
            )

            # Add assistant message
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                thinking=response.thinking,
                tool_calls=response.tool_calls,
            )
            self.messages.append(assistant_msg)

            # Print thinking if present
            if response.thinking:
                print(f"\n{Colors.BOLD}{Colors.MAGENTA}🧠 Thinking:{Colors.RESET}")
                print(f"{Colors.DIM}{response.thinking}{Colors.RESET}")

            # Print assistant response
            if response.content:
                print(f"\n{Colors.BOLD}{Colors.BRIGHT_BLUE}🤖 Assistant:{Colors.RESET}")
                print(f"{response.content}")

            # Check if task is complete (no tool calls)
            if not response.tool_calls:
                step_elapsed = perf_counter() - step_start_time
                total_elapsed = perf_counter() - run_start_time
                print(f"\n{Colors.DIM}⏱️  Step {step + 1} completed in {step_elapsed:.2f}s (total: {total_elapsed:.2f}s){Colors.RESET}")
                self.logger.end_turn(response.content)
                return response.content

            # Check for cancellation before executing tools
            if self._check_cancelled():
                self._cleanup_incomplete_messages()
                cancel_msg = "Task cancelled by user."
                print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {cancel_msg}{Colors.RESET}")
                self.logger.end_turn(cancel_msg)
                return cancel_msg

            # Execute tool calls
            for tool_call in response.tool_calls:
                tool_call_id = tool_call.id
                function_name = tool_call.function.name
                arguments = tool_call.function.arguments

                # Tool call header
                print(f"\n{Colors.BRIGHT_YELLOW}🔧 Tool Call:{Colors.RESET} {Colors.BOLD}{Colors.CYAN}{function_name}{Colors.RESET}")

                # Arguments (formatted display)
                print(f"{Colors.DIM}   Arguments:{Colors.RESET}")
                # Truncate each argument value to avoid overly long output
                truncated_args = {}
                for key, value in arguments.items():
                    value_str = str(value)
                    if len(value_str) > 200:
                        truncated_args[key] = value_str[:200] + "..."
                    else:
                        truncated_args[key] = value
                args_json = json.dumps(truncated_args, indent=2, ensure_ascii=False)
                for line in args_json.split("\n"):
                    print(f"   {Colors.DIM}{line}{Colors.RESET}")

                # Execute tool
                if function_name not in self.tools:
                    result = ToolResult(
                        success=False,
                        content="",
                        error=f"Unknown tool: {function_name}",
                    )
                else:
                    try:
                        tool = self.tools[function_name]
                        result = await tool.execute(**arguments)
                    except Exception as e:
                        # Catch all exceptions during tool execution, convert to failed ToolResult
                        import traceback

                        error_detail = f"{type(e).__name__}: {str(e)}"
                        error_trace = traceback.format_exc()
                        result = ToolResult(
                            success=False,
                            content="",
                            error=f"Tool execution failed: {error_detail}\n\nTraceback:\n{error_trace}",
                        )

                # Log tool execution result
                self.logger.log_tool_result(
                    tool_name=function_name,
                    arguments=arguments,
                    result_success=result.success,
                    result_content=result.content if result.success else None,
                    result_error=result.error if not result.success else None,
                )

                # Print result
                if result.success:
                    result_text = result.content
                    if len(result_text) > 300:
                        result_text = result_text[:300] + f"{Colors.DIM}...{Colors.RESET}"
                    print(f"{Colors.BRIGHT_GREEN}✓ Result:{Colors.RESET} {result_text}")
                else:
                    print(f"{Colors.BRIGHT_RED}✗ Error:{Colors.RESET} {Colors.RED}{result.error}{Colors.RESET}")

                # Add tool result message
                tool_msg = Message(
                    role="tool",
                    content=result.content if result.success else f"Error: {result.error}",
                    tool_call_id=tool_call_id,
                    name=function_name,
                )
                self.messages.append(tool_msg)

                # Check for cancellation after each tool execution
                if self._check_cancelled():
                    self._cleanup_incomplete_messages()
                    cancel_msg = "Task cancelled by user."
                    print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {cancel_msg}{Colors.RESET}")
                    self.logger.end_turn(cancel_msg)
                    return cancel_msg

            # After all tool results appended, handle deferred plan summary
            if self._pending_plan_summary:
                self._pending_plan_summary = False
                await self._summarize_plan_context()

            step_elapsed = perf_counter() - step_start_time
            total_elapsed = perf_counter() - run_start_time
            print(f"\n{Colors.DIM}⏱️  Step {step + 1} completed in {step_elapsed:.2f}s (total: {total_elapsed:.2f}s){Colors.RESET}")

            step += 1

        # Max steps reached
        error_msg = f"Task couldn't be completed after {self.max_steps} steps."
        print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {error_msg}{Colors.RESET}")
        self.logger.end_turn(error_msg)
        return error_msg

    def rewind(self, n_turns: int = 1) -> dict:
        """Rewind conversation by removing the last n turns.

        A "turn" starts at a real user message (not a summary injected by
        summarization, which begins with ``[Assistant Execution Summary]``).
        Everything from that user message onward (assistant replies, tool
        results, etc.) is removed.

        Returns a dict with rewind metadata for the caller to display.
        """
        # Find real user-message indices (skip system prompt at index 0
        # and skip summarization-injected messages).
        user_indices = [
            i
            for i, msg in enumerate(self.messages)
            if msg.role == "user"
            and i > 0
            and not (
                isinstance(msg.content, str)
                and msg.content.startswith("[Assistant Execution Summary]")
            )
        ]

        if not user_indices:
            return {"error": "no_turns", "remaining_turns": 0}

        if n_turns < 1:
            return {"error": "invalid_n", "remaining_turns": len(user_indices)}

        if n_turns > len(user_indices):
            return {
                "error": "too_many",
                "available": len(user_indices),
                "remaining_turns": len(user_indices),
            }

        # Truncate: keep everything *before* the target user message.
        cut_index = user_indices[-n_turns]
        removed_count = len(self.messages) - cut_index
        from_turn = self.logger.turn
        remaining_turns = len(user_indices) - n_turns

        self.messages = self.messages[:cut_index]

        # Reset token bookkeeping so the next LLM call refreshes cleanly.
        self.api_total_tokens = 0
        self._skip_next_token_check = False

        # Log the rewind event (turn number continues to increment).
        self.logger.log_rewind(from_turn=from_turn, to_turn=remaining_turns)

        # Build a preview of the last remaining user message (if any).
        last_user_preview = None
        remaining_user_indices = [
            i
            for i, msg in enumerate(self.messages)
            if msg.role == "user"
            and i > 0
            and not (
                isinstance(msg.content, str)
                and msg.content.startswith("[Assistant Execution Summary]")
            )
        ]
        if remaining_user_indices:
            last_msg = self.messages[remaining_user_indices[-1]]
            preview = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)
            last_user_preview = preview[:80] + ("..." if len(preview) > 80 else "")

        return {
            "removed": removed_count,
            "removed_turns": n_turns,
            "remaining_turns": remaining_turns,
            "remaining_messages": len(self.messages),
            "last_user_preview": last_user_preview,
        }

    def get_history(self) -> list[Message]:
        """Get message history."""
        return self.messages.copy()
