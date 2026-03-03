"""Tests for Agent.rewind()."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from tuningagent.agent import Agent
from tuningagent.schema import Message


def _make_agent(messages: list[Message] | None = None) -> Agent:
    """Create an Agent with a stubbed LLM client and optional preset messages."""
    llm = MagicMock()
    with tempfile.TemporaryDirectory() as tmp:
        agent = Agent(
            llm_client=llm,
            system_prompt="system",
            tools=[],
            workspace_dir=tmp,
        )
    if messages is not None:
        agent.messages = messages
    return agent


def _build_conversation() -> list[Message]:
    """Build a typical multi-turn conversation:

    [0] system
    [1] user1      ← turn 1
    [2] assistant1
    [3] tool1
    [4] user2      ← turn 2
    [5] assistant2
    [6] user3      ← turn 3
    [7] assistant3
    """
    return [
        Message(role="system", content="system prompt"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi there"),
        Message(role="tool", content="tool result", tool_call_id="tc1", name="bash"),
        Message(role="user", content="do something"),
        Message(role="assistant", content="done"),
        Message(role="user", content="another request"),
        Message(role="assistant", content="completed"),
    ]


# ── Basic rewind ─────────────────────────────────────────────


def test_rewind_one_turn():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(1)

    assert "error" not in result
    assert result["removed_turns"] == 1
    assert result["remaining_turns"] == 2
    # Messages should be truncated before user3 (index 6)
    assert len(agent.messages) == 6
    assert agent.messages[-1].role == "assistant"
    assert agent.messages[-1].content == "done"


def test_rewind_two_turns():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(2)

    assert "error" not in result
    assert result["removed_turns"] == 2
    assert result["remaining_turns"] == 1
    # Messages should be truncated before user2 (index 4)
    assert len(agent.messages) == 4
    assert agent.messages[-1].role == "tool"


def test_rewind_all_turns():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(3)

    assert "error" not in result
    assert result["removed_turns"] == 3
    assert result["remaining_turns"] == 0
    # Only system prompt remains
    assert len(agent.messages) == 1
    assert agent.messages[0].role == "system"


# ── Consecutive rewinds ──────────────────────────────────────


def test_consecutive_rewinds():
    agent = _make_agent(_build_conversation())

    r1 = agent.rewind(1)
    assert r1["remaining_turns"] == 2

    r2 = agent.rewind(1)
    assert r2["remaining_turns"] == 1

    r3 = agent.rewind(1)
    assert r3["remaining_turns"] == 0
    assert len(agent.messages) == 1


# ── Edge cases ───────────────────────────────────────────────


def test_rewind_no_turns():
    """Rewind when there are no user messages at all."""
    agent = _make_agent([Message(role="system", content="system")])
    result = agent.rewind(1)

    assert result["error"] == "no_turns"
    assert result["remaining_turns"] == 0


def test_rewind_too_many():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(10)

    assert result["error"] == "too_many"
    assert result["available"] == 3
    # Messages unchanged
    assert len(agent.messages) == 8


def test_rewind_invalid_n():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(0)

    assert result["error"] == "invalid_n"


def test_rewind_negative_n():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(-1)

    assert result["error"] == "invalid_n"


# ── Summary messages are skipped ─────────────────────────────


def test_rewind_skips_summary_messages():
    """Summary-injected user messages should not count as turns."""
    msgs = [
        Message(role="system", content="system"),
        Message(role="user", content="first request"),
        Message(role="user", content="[Assistant Execution Summary]\nSummary of round 1"),
        Message(role="user", content="second request"),
        Message(role="assistant", content="reply"),
    ]
    agent = _make_agent(msgs)
    result = agent.rewind(1)

    assert "error" not in result
    assert result["remaining_turns"] == 1
    # Should cut before "second request" (index 3), keeping the summary
    assert len(agent.messages) == 3
    assert agent.messages[1].content == "first request"
    assert agent.messages[2].content.startswith("[Assistant Execution Summary]")


# ── State reset ──────────────────────────────────────────────


def test_rewind_resets_token_state():
    agent = _make_agent(_build_conversation())
    agent.api_total_tokens = 5000
    agent._skip_next_token_check = True

    agent.rewind(1)

    assert agent.api_total_tokens == 0
    assert agent._skip_next_token_check is False


# ── Default n=1 ──────────────────────────────────────────────


def test_rewind_default_one():
    agent = _make_agent(_build_conversation())
    result = agent.rewind()

    assert result["removed_turns"] == 1
    assert result["remaining_turns"] == 2


# ── last_user_preview ────────────────────────────────────────


def test_rewind_returns_preview():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(1)

    assert result["last_user_preview"] is not None
    assert "do something" in result["last_user_preview"]


def test_rewind_all_no_preview():
    agent = _make_agent(_build_conversation())
    result = agent.rewind(3)

    assert result["last_user_preview"] is None
